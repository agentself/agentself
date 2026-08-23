from __future__ import annotations

import builtins
import os
from typing import NoReturn, Protocol

from agentself.backends.email.contract import MailboxAccess, MailboxError
from agentself.backends.store.contract import (
    SecretExists,
    SecretMissing,
    StoreAccess,
    StoreError,
)
from agentself.backends.wallet.contract import (
    CannotAuthorize as WalletCannotAuthorize,
)
from agentself.backends.wallet.contract import (
    CannotSend as WalletCannotSend,
)
from agentself.backends.wallet.contract import (
    WalletAccess,
    WalletError,
)
from agentself.host import (
    ENV_EMAIL_ADDRESS,
    ENV_EMAIL_CREDENTIAL,
    bind_of,
)
from agentself.internal.custody.errors import (
    CannotAuthorize,
    CannotSend,
    ChannelFailure,
    EmailSendNotReady,
    HostToolMissing,
    MissingSecret,
    NoGas,
    ProtectedName,
    Refused,
    StoreFailure,
    UnknownIdentity,
)
from agentself.internal.eoa import generate_secp256k1
from agentself.internal.log import Log
from agentself.internal.names import (
    EMAIL_ADDRESS_NAME,
    EMAIL_CREDENTIAL_NAME,
    PROTECTED_SECRET_NAMES,
    WALLET_KEY_NAME,
    is_reserved_secret_name,
    require_safe_token,
)
from agentself.internal.registry import (
    RegistryError,
    RegistryFormatError,
)
from agentself.internal.setup import (
    OPTION_ADDRESS,
    OPTION_CREDENTIAL,
    SETUP_ACTION_REQUIRED,
    SETUP_CONNECTED,
    SETUP_FAILED,
    continue_command,
    decode_state,
    encode_state,
    setup_status_of,
)
from agentself.internal.types import BoundCaller, Identity

ALLOWED_BINDINGS = frozenset({"sops", "pass"})
_EMAIL_VIEW_KEYS = ("address", "owned_address", "needs_domain")
_WALLET_VIEW_KEYS = (
    "address",
    "chain",
    "chain_label",
    "chain_id",
    "asset",
    "kind",
    "scheme",
)
_BALANCE_KEYS = (
    "asset",
    "chain",
    "chain_id",
    "address",
    "amount",
    "raw",
    "gas_asset",
    "gas_raw",
    "gas_amount",
)
_MAIL_ITEM_KEYS = ("id", "from", "to", "subject", "body", "reason")


class IdentityAccess(Protocol):
    def find(self, identity_id: str) -> Identity | None: ...

    def init(
        self, identity_id: str, recipient: str, store_binding: str
    ) -> Identity: ...


class StoreAccessFactory(Protocol):
    def for_binding(self, binding: str) -> StoreAccess: ...


class MailboxAccessFactory(Protocol):
    def for_binding(self, binding: str) -> MailboxAccess: ...


class WalletAccessFactory(Protocol):
    def for_binding(self, binding: str) -> WalletAccess: ...


class CustodyManager:
    """Sequence: bind caller, Find, owner check, then StoreAccess."""

    def __init__(
        self,
        identities: IdentityAccess,
        stores: StoreAccessFactory,
        log: Log,
        mailboxes: MailboxAccessFactory | None = None,
        wallets: WalletAccessFactory | None = None,
        *,
        email_backend: str | None = None,
        wallet_backend: str | None = None,
    ) -> None:
        self._identities = identities
        self._stores = stores
        self._log = log
        self._mailboxes = mailboxes
        self._wallets = wallets
        self._email_backend = email_backend or "agentmail"
        self._wallet_backend = wallet_backend or "base"

    def init(self, caller: BoundCaller, store_binding: str = "sops") -> Identity:
        try:
            identity_id = require_safe_token(caller.identity_id, "identity id")
        except ValueError:
            self._log.record("init", caller.identity_id, None, "refused")
            raise Refused("refused") from None
        try:
            found = self._identities.find(identity_id)
        except RegistryError as exc:
            self._fail_store("init", identity_id, "registry.json", exc)
        if found is not None:
            if found.recipient != caller.recipient:
                self._log.record("init", identity_id, None, "refused")
                raise Refused("refused")
            self._log.record("init", identity_id, None, "ok")
            return found
        if store_binding not in ALLOWED_BINDINGS:
            self._log.record("init", identity_id, None, "refused")
            raise Refused("refused")
        try:
            identity = self._identities.init(
                identity_id, caller.recipient, store_binding
            )
        except RegistryError as exc:
            self._fail_store("init", identity_id, "registry.json", exc)
        try:
            store = self._stores.for_binding(identity.store_binding)
            store.list(identity.id)
        except (StoreError, FileNotFoundError) as exc:
            self._fail_store("init", identity_id, None, exc)
        self._log.record("init", identity_id, None, "ok")
        return identity

    def create(
        self,
        caller: BoundCaller,
        name: str,
        value: str,
    ) -> bool:
        identity = self._require_identity(caller, "create", name)
        if is_reserved_secret_name(name):
            self._log.record("create", identity.id, name, "refused")
            raise Refused("refused")
        store = self._store_for(identity, "create", name)
        try:
            store.create(identity.id, name, value)
        except SecretExists:
            try:
                existing = store.get(identity.id, name)
            except (StoreError, FileNotFoundError) as exc:
                self._fail_store("create", identity.id, name, exc)
            if existing == value:
                self._log.record("create", identity.id, name, "ok")
                return True
            self._log.record("create", identity.id, name, "exists")
            raise Refused("refused") from None
        except (StoreError, FileNotFoundError) as exc:
            self._fail_store("create", identity.id, name, exc)
        self._log.record("create", identity.id, name, "ok")
        return False

    def get(
        self,
        caller: BoundCaller,
        name: str,
    ) -> str:
        identity = self._require_identity(caller, "get", name)
        if is_reserved_secret_name(name):
            self._log.record("get", identity.id, name, "missing")
            raise MissingSecret("missing")
        store = self._store_for(identity, "get", name)
        try:
            value = store.get(identity.id, name)
        except SecretMissing:
            self._log.record("get", identity.id, name, "missing")
            raise MissingSecret("missing") from None
        except (StoreError, FileNotFoundError) as exc:
            self._fail_store("get", identity.id, name, exc)
        self._log.record("get", identity.id, name, "ok")
        return value

    def update(
        self,
        caller: BoundCaller,
        name: str,
        value: str,
    ) -> None:
        identity = self._require_identity(caller, "update", name)
        if is_reserved_secret_name(name):
            self._log.record("update", identity.id, name, "missing")
            raise MissingSecret("missing")
        store = self._store_for(identity, "update", name)
        try:
            store.update(identity.id, name, value)
        except SecretMissing:
            self._log.record("update", identity.id, name, "missing")
            raise MissingSecret("missing") from None
        except (StoreError, FileNotFoundError) as exc:
            self._fail_store("update", identity.id, name, exc)
        self._log.record("update", identity.id, name, "ok")

    def list(
        self,
        caller: BoundCaller,
    ) -> builtins.list[str]:
        identity = self._require_identity(caller, "list", None)
        store = self._store_for(identity, "list", None)
        try:
            names = store.list(identity.id)
        except (StoreError, FileNotFoundError) as exc:
            self._fail_store("list", identity.id, None, exc)
        public = [name for name in names if not is_reserved_secret_name(name)]
        self._log.record("list", identity.id, None, "ok")
        return public

    def exists(
        self,
        caller: BoundCaller,
        name: str,
    ) -> bool:
        return name in self.list(caller)

    def delete(
        self,
        caller: BoundCaller,
        name: str,
    ) -> None:
        identity = self._require_identity(caller, "delete", name)
        if is_reserved_secret_name(name):
            self._log.record("delete", identity.id, name, "missing")
            raise MissingSecret("missing")
        if name in PROTECTED_SECRET_NAMES:
            self._log.record("delete", identity.id, name, "refused")
            raise ProtectedName(name)
        store = self._store_for(identity, "delete", name)
        try:
            store.delete(identity.id, name)
        except SecretMissing:
            self._log.record("delete", identity.id, name, "missing")
            raise MissingSecret("missing") from None
        except (StoreError, FileNotFoundError) as exc:
            self._fail_store("delete", identity.id, name, exc)
        self._log.record("delete", identity.id, name, "ok")

    def email_connect(
        self,
        caller: BoundCaller,
        *,
        answers: dict[str, str] | None = None,
        state: str | None = None,
    ) -> dict[str, object]:
        identity = self._require_identity(caller, "email_connect", None)
        incoming = {
            key: str(value)
            for key, value in (answers or {}).items()
            if str(value).strip()
        }
        asked = ""
        token = (state or "").strip()
        if token:
            decoded = decode_state(token)
            if decoded is None:
                self._log.record("email_connect", identity.id, None, "error")
                return {"status": SETUP_FAILED, "reason": "unknown setup"}
            asked = str(decoded.get("option") or "").strip()
            raw_value = incoming.pop("value", "").strip()
            if asked and raw_value and asked not in incoming:
                incoming[asked] = raw_value
        else:
            incoming.pop("value", None)
        address, credential, sources = self._resolve_email_inputs(identity, incoming)
        mailbox = self._mailbox_for(identity, "email_connect")
        try:
            desc = mailbox.connect(
                identity.id,
                credential=credential,
                address=address,
                answers=incoming,
            )
        except MailboxError as exc:
            self._log.record("email_connect", identity.id, None, "error")
            raise _channel_from_mailbox(exc, has_token=bool(credential)) from None
        status = setup_status_of(desc)
        if status == SETUP_CONNECTED:
            self._persist_setup_answers(identity, incoming)
            view = _email_view(desc)
            self._persist_email_success(identity, view, sources.get(OPTION_ADDRESS))
            view["status"] = SETUP_CONNECTED
            self._log.record("email_connect", identity.id, None, "ok")
            return view
        if status == SETUP_FAILED:
            self._log.record("email_connect", identity.id, None, "error")
            reason = str(desc.get("reason") or "error")
            return {"status": SETUP_FAILED, "reason": reason}
        self._persist_setup_answers(identity, incoming)
        option = _setup_option_of(desc)
        human = (
            bool(desc.get("human_action_required")) or status == SETUP_ACTION_REQUIRED
        )
        next_state = encode_state(
            {"option": str(option.get("name") or "")} if option else {"status": status}
        )
        payload: dict[str, object] = {
            "status": status,
            "state": next_state,
            "human_action_required": human,
            "continue": continue_command(next_state),
        }
        if option:
            payload["option"] = option
        if desc.get("message"):
            payload["message"] = desc["message"]
        self._log.record("email_connect", identity.id, None, "ok")
        return payload

    def email_send(
        self,
        caller: BoundCaller,
        to: str,
        subject: str,
        body: str,
    ) -> None:
        identity = self._require_identity(caller, "email_send", None)
        address, token, _sources = self._resolve_email_inputs(
            identity, {}, "email_send"
        )
        mailbox = self._mailbox_for(identity, "email_send")
        try:
            desc = mailbox.describe(identity.id, credential=token, address=address)
        except MailboxError as exc:
            self._log.record("email_send", identity.id, None, "error")
            raise _channel_from_mailbox(exc, has_token=bool(token)) from None
        if not desc.get("owned_address") or not token:
            self._log.record("email_send", identity.id, None, "error")
            raise EmailSendNotReady()
        try:
            mailbox.send(
                identity.id,
                to,
                subject,
                body,
                credential=token,
                address=address,
            )
        except MailboxError as exc:
            self._log.record("email_send", identity.id, None, "error")
            raise _channel_from_mailbox(exc, has_token=bool(token)) from None
        self._log.record("email_send", identity.id, None, "ok")

    def email_receive(
        self,
        caller: BoundCaller,
        message_id: str | None = None,
    ) -> builtins.list[dict[str, str]]:
        identity = self._require_identity(caller, "email_receive", None)
        address, token, _sources = self._resolve_email_inputs(
            identity, {}, "email_receive"
        )
        mailbox = self._mailbox_for(identity, "email_receive")
        try:
            messages = mailbox.receive(
                identity.id,
                credential=token,
                address=address,
                message_id=message_id,
            )
        except MailboxError as exc:
            self._log.record("email_receive", identity.id, None, "error")
            raise _channel_from_mailbox(exc, has_token=bool(token)) from None
        self._log.record("email_receive", identity.id, None, "ok")
        return _items(messages, _MAIL_ITEM_KEYS)

    def email_list(self, caller: BoundCaller) -> builtins.list[dict[str, str]]:
        identity = self._require_identity(caller, "email_list", None)
        address, token, _sources = self._resolve_email_inputs(
            identity, {}, "email_list"
        )
        mailbox = self._mailbox_for(identity, "email_list")
        try:
            items = mailbox.list(identity.id, credential=token, address=address)
        except MailboxError as exc:
            self._log.record("email_list", identity.id, None, "error")
            raise _channel_from_mailbox(exc, has_token=bool(token)) from None
        self._log.record("email_list", identity.id, None, "ok")
        return _items(items, _MAIL_ITEM_KEYS)

    def wallet_address(self, caller: BoundCaller) -> str:
        identity = self._require_identity(caller, "wallet_address", None)
        wallet = self._ready_wallet(identity, "wallet_address")
        try:
            addr = wallet.address(identity.id)
        except WalletError as exc:
            self._log.record("wallet_address", identity.id, None, "error")
            raise _channel_from_wallet(exc) from None
        self._log.record("wallet_address", identity.id, None, "ok")
        return addr

    def wallet_authorize(
        self,
        caller: BoundCaller,
        message: str,
    ) -> str:
        identity = self._require_identity(caller, "wallet_authorize", None)
        wallet = self._ready_wallet(identity, "wallet_authorize")
        try:
            signature = wallet.authorize(identity.id, message)
        except WalletCannotAuthorize:
            self._log.record("wallet_authorize", identity.id, None, "cannot_authorize")
            raise CannotAuthorize() from None
        except WalletError as exc:
            self._log.record("wallet_authorize", identity.id, None, "error")
            raise _channel_from_wallet(exc) from None
        self._log.record("wallet_authorize", identity.id, None, "ok")
        return signature

    def wallet_verify(
        self,
        caller: BoundCaller,
        message: str,
        authorization: str,
    ) -> dict[str, object]:
        identity = self._require_identity(caller, "wallet_verify", None)
        wallet = self._ready_wallet(identity, "wallet_verify")
        try:
            result = wallet.verify(identity.id, message, authorization)
        except WalletError as exc:
            self._log.record("wallet_verify", identity.id, None, "error")
            raise _channel_from_wallet(exc) from None
        self._log.record("wallet_verify", identity.id, None, "ok")
        picked = _pick(result, ("valid", "address", "scheme"))
        if "valid" in picked:
            picked["valid"] = bool(picked["valid"])
        if "address" in picked:
            picked["address"] = str(picked["address"])
        if "scheme" in picked:
            picked["scheme"] = str(picked["scheme"])
        return picked

    def wallet_balance(self, caller: BoundCaller) -> dict[str, str]:
        identity = self._require_identity(caller, "wallet_balance", None)
        wallet = self._ready_wallet(identity, "wallet_balance")
        try:
            result = wallet.balance(identity.id)
        except WalletError as exc:
            self._log.record("wallet_balance", identity.id, None, "error")
            raise _channel_from_wallet(exc) from None
        self._log.record("wallet_balance", identity.id, None, "ok")
        return _balance_view(result)

    def wallet_send(
        self,
        caller: BoundCaller,
        to: str,
        amount: str,
        asset: str = "USDC",
    ) -> None:
        identity = self._require_identity(caller, "wallet_send", None)
        wallet = self._ready_wallet(identity, "wallet_send")
        try:
            wallet.send(identity.id, to, amount, asset)
        except WalletCannotSend as exc:
            msg = str(exc)
            if msg == "need ETH for gas":
                self._log.record("wallet_send", identity.id, None, "no_eth")
                raise NoGas("need ETH for gas") from None
            self._log.record("wallet_send", identity.id, None, "cannot_send")
            if msg in ("need USDC", "need USD"):
                raise CannotSend(msg) from None
            raise CannotSend() from None
        except WalletError as exc:
            self._log.record("wallet_send", identity.id, None, "error")
            raise _channel_from_wallet(exc) from None
        self._log.record("wallet_send", identity.id, None, "ok")

    def identity(self, caller: BoundCaller) -> dict[str, object]:
        identity = self._require_identity(caller, "identity", None)
        mailbox = self._mailbox_for(identity, "identity")
        address, token, _sources = self._resolve_email_inputs(identity, {}, "identity")
        try:
            email = mailbox.describe(identity.id, credential=token, address=address)
        except MailboxError as exc:
            self._log.record("identity", identity.id, None, "error")
            raise _channel_from_mailbox(exc, has_token=bool(token)) from None
        wallet = self._ready_wallet(identity, "identity")
        try:
            wallet_view = wallet.describe(identity.id)
        except WalletError as exc:
            self._log.record("identity", identity.id, None, "error")
            raise _channel_from_wallet(exc) from None
        self._log.record("identity", identity.id, None, "ok")
        return {
            "id": identity.id,
            "recipient": identity.recipient,
            "email": _email_view(email),
            "wallet": _wallet_view(wallet_view),
            "email_backend": self._email_backend,
            "wallet_backend": self._wallet_backend,
        }

    def _resolve_email_inputs(
        self,
        identity: Identity,
        answers: dict[str, str],
        operation: str = "email_connect",
    ) -> tuple[str | None, str | None, dict[str, str]]:
        catalog = bind_of("email", self._email_backend)
        options = list(catalog.options) if catalog is not None else []
        address, address_src = self._resolve_email_field(
            identity,
            OPTION_ADDRESS,
            ENV_EMAIL_ADDRESS,
            EMAIL_ADDRESS_NAME,
            answers,
            options,
            operation,
        )
        credential, cred_src = self._resolve_email_field(
            identity,
            OPTION_CREDENTIAL,
            ENV_EMAIL_CREDENTIAL,
            EMAIL_CREDENTIAL_NAME,
            answers,
            options,
            operation,
        )
        sources = {}
        if address_src:
            sources[OPTION_ADDRESS] = address_src
        if cred_src:
            sources[OPTION_CREDENTIAL] = cred_src
        return address, credential, sources

    def _resolve_email_field(
        self,
        identity: Identity,
        option_name: str,
        env_generic: str,
        vault_name: str,
        answers: dict[str, str],
        options: builtins.list[dict[str, object]],
        operation: str,
    ) -> tuple[str | None, str | None]:
        env_val = os.environ.get(env_generic, "").strip()
        if env_val:
            return env_val, "env"
        held = self._optional_secret_value(identity, vault_name, operation)
        if held:
            return held, "vault"
        alias = ""
        for item in options:
            if item.get("name") == option_name:
                alias = str(item.get("source") or "").strip()
                break
        if alias and alias != env_generic:
            alias_val = os.environ.get(alias, "").strip()
            if alias_val:
                return alias_val, "alias"
        setup_val = (answers.get(option_name) or "").strip()
        if setup_val:
            return setup_val, "setup"
        return None, None

    def _persist_setup_answers(
        self, identity: Identity, answers: dict[str, str]
    ) -> None:
        credential = (answers.get(OPTION_CREDENTIAL) or "").strip()
        if credential:
            self._store_put(
                identity, EMAIL_CREDENTIAL_NAME, credential, "email_connect"
            )
        address = (answers.get(OPTION_ADDRESS) or "").strip()
        if address:
            self._store_put(identity, EMAIL_ADDRESS_NAME, address, "email_connect")

    def _persist_email_success(
        self,
        identity: Identity,
        view: dict[str, object],
        address_source: str | None,
    ) -> None:
        email = str(view.get("address") or "").strip()
        if (
            view.get("owned_address")
            and email
            and address_source not in {"env", "alias"}
        ):
            self._store_put(identity, EMAIL_ADDRESS_NAME, email, "email_connect")

    def _store_put(
        self, identity: Identity, name: str, value: str, operation: str
    ) -> None:
        store = self._store_for(identity, operation, name)
        try:
            store.create(identity.id, name, value)
        except SecretExists:
            try:
                existing = store.get(identity.id, name)
            except (StoreError, FileNotFoundError) as exc:
                self._fail_store(operation, identity.id, name, exc)
            if existing == value:
                return
            try:
                store.update(identity.id, name, value)
            except (StoreError, FileNotFoundError) as exc:
                self._fail_store(operation, identity.id, name, exc)
        except (StoreError, FileNotFoundError) as exc:
            self._fail_store(operation, identity.id, name, exc)

    def _require_identity(
        self,
        caller: BoundCaller,
        operation: str,
        name: str | None,
    ) -> Identity:
        try:
            identity_id = require_safe_token(caller.identity_id, "identity id")
            if name is not None:
                require_safe_token(name, "name")
        except ValueError:
            self._log.record(operation, caller.identity_id, name, "refused")
            raise Refused("refused") from None
        try:
            found = self._identities.find(identity_id)
        except RegistryError as exc:
            self._fail_store(operation, identity_id, "registry.json", exc)
        if found is None:
            self._log.record(operation, identity_id, name, "unknown")
            raise UnknownIdentity("unknown identity")
        if found.recipient != caller.recipient:
            self._log.record(operation, identity_id, name, "refused")
            raise Refused("refused")
        return found

    def _store_for(
        self, identity: Identity, operation: str, name: str | None
    ) -> StoreAccess:
        try:
            return self._stores.for_binding(identity.store_binding)
        except (StoreError, FileNotFoundError) as exc:
            self._fail_store(operation, identity.id, name, exc)

    def _mailbox_for(self, identity: Identity, operation: str) -> MailboxAccess:
        if self._mailboxes is None:
            self._log.record(operation, identity.id, None, "error")
            raise ChannelFailure("channel error")
        try:
            return self._mailboxes.for_binding(self._email_backend)
        except MailboxError as exc:
            self._log.record(operation, identity.id, None, "error")
            raise _channel_from_mailbox(exc) from None

    def _wallet_for(self, identity: Identity, operation: str) -> WalletAccess:
        if self._wallets is None:
            self._log.record(operation, identity.id, None, "error")
            raise ChannelFailure("channel error")
        try:
            return self._wallets.for_binding(self._wallet_backend)
        except WalletError as exc:
            self._log.record(operation, identity.id, None, "error")
            raise _channel_from_wallet(exc) from None

    def _ready_wallet(self, identity: Identity, operation: str) -> WalletAccess:
        wallet = self._wallet_for(identity, operation)
        if not getattr(wallet, "needs_material", False):
            return wallet
        key = self._wallet_key(identity, operation)
        binder = getattr(wallet, "bind_key", None)
        if binder is None:
            self._log.record(operation, identity.id, None, "error")
            raise ChannelFailure("channel error")
        binder(key)
        return wallet

    def _optional_secret_value(
        self, identity: Identity, name: str, operation: str
    ) -> str | None:
        store = self._store_for(identity, operation, name)
        try:
            names = store.list(identity.id)
        except (StoreError, FileNotFoundError) as exc:
            self._fail_store(operation, identity.id, name, exc)
        if name not in names:
            return None
        try:
            return store.get(identity.id, name)
        except SecretMissing:
            return None
        except (StoreError, FileNotFoundError) as exc:
            self._fail_store(operation, identity.id, name, exc)

    def _wallet_key(self, identity: Identity, operation: str) -> str:
        store = self._store_for(identity, operation, WALLET_KEY_NAME)
        try:
            names = store.list(identity.id)
        except (StoreError, FileNotFoundError) as exc:
            self._fail_store(operation, identity.id, WALLET_KEY_NAME, exc)
        if WALLET_KEY_NAME in names:
            try:
                return store.get(identity.id, WALLET_KEY_NAME)
            except SecretMissing:
                pass
            except (StoreError, FileNotFoundError) as exc:
                self._fail_store(operation, identity.id, WALLET_KEY_NAME, exc)
        key = generate_secp256k1()
        try:
            store.create(identity.id, WALLET_KEY_NAME, key)
        except SecretExists:
            try:
                return store.get(identity.id, WALLET_KEY_NAME)
            except (StoreError, FileNotFoundError) as exc:
                self._fail_store(operation, identity.id, WALLET_KEY_NAME, exc)
        except (StoreError, FileNotFoundError) as exc:
            self._fail_store(operation, identity.id, WALLET_KEY_NAME, exc)
        return key

    def _fail_store(
        self,
        operation: str,
        identity_id: str | None,
        name: str | None,
        exc: BaseException,
    ) -> NoReturn:
        self._log.record(operation, identity_id, name, "store_error")
        tool = _host_tool_from(exc)
        if tool:
            raise HostToolMissing(tool) from None
        if isinstance(exc, RegistryFormatError):
            raise StoreFailure(str(exc)) from None
        raise StoreFailure("store error", name=name) from None


def _channel_from_mailbox(
    exc: BaseException, *, has_token: bool | None = None
) -> ChannelFailure:
    msg = str(exc)
    low = msg.lower()
    if has_token is False or (
        "no token" in low
        or "missing credentials" in low
        or "missing token" in low
        or low == "no_token"
    ):
        reason = "no_token"
    elif "invalid credentials" in low:
        reason = "invalid_credential"
    elif "need address" in low:
        reason = "need_address"
    elif msg in {"invalid host", "invalid port"}:
        reason = msg
    elif _mailbox_rpc_failure(low, exc.__cause__):
        reason = "rpc"
    else:
        reason = "mailbox_error"
    return ChannelFailure("channel error", reason=reason)


def _mailbox_rpc_failure(low: str, cause: BaseException | None) -> bool:
    if "rpc" in low or "http failed" in low:
        return True
    if cause is None:
        return False
    if isinstance(cause, (TimeoutError, ConnectionError)):
        return True
    cause_name = type(cause).__name__.lower()
    return "urlerror" in cause_name or cause_name == "timeout"


def _channel_from_wallet(exc: BaseException) -> ChannelFailure:
    msg = str(exc)
    low = msg.lower()
    if msg in {"rpc failed", "no RPC configured"} or "rpc" in low or "eth_call" in low:
        reason = "rpc"
    elif "missing key" in low:
        reason = "no_key"
    elif "cannot send" in low:
        reason = "cannot_send"
    else:
        reason = "error"
    return ChannelFailure("channel error", reason=reason)


def _host_tool_from(exc: BaseException) -> str | None:
    suffix = " not on PATH"
    msg = str(exc)
    if msg.endswith(suffix):
        return msg[: -len(suffix)]
    filename = getattr(exc, "filename", None)
    if isinstance(exc, FileNotFoundError) and filename:
        return str(filename).rsplit("/", 1)[-1]
    return None


def _pick(data: object, keys: tuple[str, ...]) -> dict:
    if not isinstance(data, dict):
        return {}
    return {key: data[key] for key in keys if key in data}


def _setup_option_of(desc: dict[str, object]) -> dict[str, object] | None:
    option = desc.get("option")
    if isinstance(option, dict) and str(option.get("name") or "").strip():
        return dict(option)
    options = desc.get("options")
    if isinstance(options, list):
        for item in options:
            if isinstance(item, dict) and str(item.get("name") or "").strip():
                return dict(item)
    return None


def _email_view(desc: object) -> dict[str, object]:
    return _pick(desc, _EMAIL_VIEW_KEYS)


def _wallet_view(desc: object) -> dict[str, object]:
    return _pick(desc, _WALLET_VIEW_KEYS)


def _balance_view(result: object) -> dict[str, str]:
    picked = _pick(result, _BALANCE_KEYS)
    return {key: str(value) for key, value in picked.items()}


def _items(items: object, keys: tuple[str, ...]) -> list[dict[str, str]]:
    if not isinstance(items, list):
        return []
    return [
        {key: str(value) for key, value in _pick(item, keys).items()} for item in items
    ]
