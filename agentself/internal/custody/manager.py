from __future__ import annotations

import builtins
import os
from typing import NoReturn, Protocol

from agentself.backends.email.contract import MailboxAccess, MailboxError
from agentself.backends.store.contract import (
    HoldNameExists,
    HoldNameMissing,
    StoreAccess,
    StoreError,
)
from agentself.backends.wallet.contract import (
    CannotSend as WalletCannotSend,
)
from agentself.backends.wallet.contract import (
    CannotSign as WalletCannotSign,
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
    CannotSend,
    CannotSign,
    ChannelFailure,
    EmailSendNotReady,
    HostToolMissing,
    MissingHoldName,
    NoGas,
    ProtectedName,
    Refused,
    StoreFailure,
    UnknownPrincipal,
)
from agentself.internal.eoa import generate_secp256k1
from agentself.internal.log import Log
from agentself.internal.names import (
    EMAIL_ADDRESS_NAME,
    PROTECTED_HOLD_NAMES,
    EMAIL_CREDENTIAL_NAME,
    WALLET_KEY_NAME,
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
    is_reserved_secret_name,
    setup_status_of,
)
from agentself.internal.types import BoundCaller, Principal

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


class PrincipalAccess(Protocol):
    def find(self, principal_id: str) -> Principal | None: ...

    def enroll(
        self, principal_id: str, recipient: str, store_binding: str
    ) -> Principal: ...


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
        principals: PrincipalAccess,
        stores: StoreAccessFactory,
        log: Log,
        mailboxes: MailboxAccessFactory | None = None,
        wallets: WalletAccessFactory | None = None,
        *,
        email_backend: str | None = None,
        wallet_backend: str | None = None,
    ) -> None:
        self._principals = principals
        self._stores = stores
        self._log = log
        self._mailboxes = mailboxes
        self._wallets = wallets
        self._email_backend = email_backend or "agentmail"
        self._wallet_backend = wallet_backend or "base"

    def enroll(self, caller: BoundCaller, store_binding: str = "sops") -> Principal:
        try:
            principal_id = require_safe_token(caller.principal_id, "principal id")
        except ValueError:
            self._log.record("enroll", caller.principal_id, None, "refused")
            raise Refused("refused") from None
        try:
            found = self._principals.find(principal_id)
        except RegistryError as exc:
            self._fail_store("enroll", principal_id, "registry.json", exc)
        if found is not None:
            if found.recipient != caller.recipient:
                self._log.record("enroll", principal_id, None, "refused")
                raise Refused("refused")
            self._log.record("enroll", principal_id, None, "ok")
            return found
        if store_binding not in ALLOWED_BINDINGS:
            self._log.record("enroll", principal_id, None, "refused")
            raise Refused("refused")
        try:
            principal = self._principals.enroll(
                principal_id, caller.recipient, store_binding
            )
        except RegistryError as exc:
            self._fail_store("enroll", principal_id, "registry.json", exc)
        try:
            store = self._stores.for_binding(principal.store_binding)
            store.list(principal.id)
        except (StoreError, FileNotFoundError) as exc:
            self._fail_store("enroll", principal_id, None, exc)
        self._log.record("enroll", principal_id, None, "ok")
        return principal

    def seal(
        self,
        caller: BoundCaller,
        name: str,
        value: str,
        hold_owner: str | None = None,
    ) -> bool:
        principal = self._own_hold(caller, hold_owner, "seal", name)
        if is_reserved_secret_name(name):
            self._log.record("seal", principal.id, name, "refused")
            raise Refused("refused")
        store = self._store_for(principal, "seal", name)
        try:
            store.seal(principal.id, name, value)
        except HoldNameExists:
            try:
                existing = store.reveal(principal.id, name)
            except (StoreError, FileNotFoundError) as exc:
                self._fail_store("seal", principal.id, name, exc)
            if existing == value:
                self._log.record("seal", principal.id, name, "ok")
                return True
            self._log.record("seal", principal.id, name, "exists")
            raise Refused("refused") from None
        except (StoreError, FileNotFoundError) as exc:
            self._fail_store("seal", principal.id, name, exc)
        self._log.record("seal", principal.id, name, "ok")
        return False

    def reveal(
        self,
        caller: BoundCaller,
        name: str,
        hold_owner: str | None = None,
    ) -> str:
        principal = self._own_hold(caller, hold_owner, "reveal", name)
        if is_reserved_secret_name(name):
            self._log.record("reveal", principal.id, name, "missing")
            raise MissingHoldName("missing")
        store = self._store_for(principal, "reveal", name)
        try:
            value = store.reveal(principal.id, name)
        except HoldNameMissing:
            self._log.record("reveal", principal.id, name, "missing")
            raise MissingHoldName("missing") from None
        except (StoreError, FileNotFoundError) as exc:
            self._fail_store("reveal", principal.id, name, exc)
        self._log.record("reveal", principal.id, name, "ok")
        return value

    def replace(
        self,
        caller: BoundCaller,
        name: str,
        value: str,
        hold_owner: str | None = None,
    ) -> None:
        principal = self._own_hold(caller, hold_owner, "replace", name)
        if is_reserved_secret_name(name):
            self._log.record("replace", principal.id, name, "missing")
            raise MissingHoldName("missing")
        store = self._store_for(principal, "replace", name)
        try:
            store.replace(principal.id, name, value)
        except HoldNameMissing:
            self._log.record("replace", principal.id, name, "missing")
            raise MissingHoldName("missing") from None
        except (StoreError, FileNotFoundError) as exc:
            self._fail_store("replace", principal.id, name, exc)
        self._log.record("replace", principal.id, name, "ok")

    def list(
        self,
        caller: BoundCaller,
        hold_owner: str | None = None,
    ) -> builtins.list[str]:
        principal = self._own_hold(caller, hold_owner, "list", None)
        store = self._store_for(principal, "list", None)
        try:
            names = store.list(principal.id)
        except (StoreError, FileNotFoundError) as exc:
            self._fail_store("list", principal.id, None, exc)
        public = [name for name in names if not is_reserved_secret_name(name)]
        self._log.record("list", principal.id, None, "ok")
        return list(public)

    def exists(
        self,
        caller: BoundCaller,
        name: str,
        hold_owner: str | None = None,
    ) -> bool:
        return name in self.list(caller, hold_owner=hold_owner)

    def delete(
        self,
        caller: BoundCaller,
        name: str,
        hold_owner: str | None = None,
    ) -> None:
        principal = self._own_hold(caller, hold_owner, "delete", name)
        if is_reserved_secret_name(name):
            self._log.record("delete", principal.id, name, "missing")
            raise MissingHoldName("missing")
        if name in PROTECTED_HOLD_NAMES:
            self._log.record("delete", principal.id, name, "refused")
            raise ProtectedName(name)
        store = self._store_for(principal, "delete", name)
        try:
            store.delete(principal.id, name)
        except HoldNameMissing:
            self._log.record("delete", principal.id, name, "missing")
            raise MissingHoldName("missing") from None
        except (StoreError, FileNotFoundError) as exc:
            self._fail_store("delete", principal.id, name, exc)
        self._log.record("delete", principal.id, name, "ok")

    def email_connect(
        self,
        caller: BoundCaller,
        hold_owner: str | None = None,
        *,
        answers: dict[str, str] | None = None,
        state: str | None = None,
    ) -> dict[str, object]:
        principal = self._own_hold(caller, hold_owner, "email_connect", None)
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
                self._log.record("email_connect", principal.id, None, "error")
                return {"status": SETUP_FAILED, "reason": "unknown setup"}
            asked = str(decoded.get("option") or "").strip()
            raw_value = incoming.pop("value", "").strip()
            if asked and raw_value and asked not in incoming:
                incoming[asked] = raw_value
        else:
            incoming.pop("value", None)
        address, credential, sources = self._resolve_email_inputs(principal, incoming)
        mailbox = self._mailbox_for(principal, "email_connect")
        try:
            desc = mailbox.connect(
                principal.id,
                credential=credential,
                address=address,
                answers=incoming,
            )
        except MailboxError as exc:
            self._log.record("email_connect", principal.id, None, "error")
            raise _channel_from_mailbox(exc, has_token=bool(credential)) from None
        status = setup_status_of(desc)
        if status == SETUP_CONNECTED:
            self._persist_setup_answers(principal, incoming)
            view = _email_view(desc)
            self._persist_email_success(principal, view, sources.get(OPTION_ADDRESS))
            view["status"] = SETUP_CONNECTED
            self._log.record("email_connect", principal.id, None, "ok")
            return view
        if status == SETUP_FAILED:
            self._log.record("email_connect", principal.id, None, "error")
            reason = str(desc.get("reason") or "error")
            return {"status": SETUP_FAILED, "reason": reason}
        self._persist_setup_answers(principal, incoming)
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
        self._log.record("email_connect", principal.id, None, "ok")
        return payload

    def email_send(
        self,
        caller: BoundCaller,
        to: str,
        subject: str,
        body: str,
        hold_owner: str | None = None,
    ) -> None:
        principal = self._own_hold(caller, hold_owner, "email_send", None)
        address, token, _sources = self._resolve_email_inputs(
            principal, {}, "email_send"
        )
        mailbox = self._mailbox_for(principal, "email_send")
        try:
            desc = mailbox.describe(principal.id, credential=token, address=address)
        except MailboxError as exc:
            self._log.record("email_send", principal.id, None, "error")
            raise _channel_from_mailbox(exc, has_token=bool(token)) from None
        if not desc.get("owned_address") or not token:
            self._log.record("email_send", principal.id, None, "error")
            raise EmailSendNotReady()
        try:
            mailbox.send(
                principal.id,
                to,
                subject,
                body,
                credential=token,
                address=address,
            )
        except MailboxError as exc:
            self._log.record("email_send", principal.id, None, "error")
            raise _channel_from_mailbox(exc, has_token=bool(token)) from None
        self._log.record("email_send", principal.id, None, "ok")

    def email_recv(
        self,
        caller: BoundCaller,
        hold_owner: str | None = None,
        message_id: str | None = None,
    ) -> builtins.list[dict[str, str]]:
        principal = self._own_hold(caller, hold_owner, "email_recv", None)
        address, token, _sources = self._resolve_email_inputs(
            principal, {}, "email_recv"
        )
        mailbox = self._mailbox_for(principal, "email_recv")
        try:
            messages = mailbox.recv(
                principal.id,
                credential=token,
                address=address,
                message_id=message_id,
            )
        except MailboxError as exc:
            self._log.record("email_recv", principal.id, None, "error")
            raise _channel_from_mailbox(exc, has_token=bool(token)) from None
        self._log.record("email_recv", principal.id, None, "ok")
        return _items(messages, _MAIL_ITEM_KEYS)

    def email_list(
        self, caller: BoundCaller, hold_owner: str | None = None
    ) -> builtins.list[dict[str, str]]:
        principal = self._own_hold(caller, hold_owner, "email_list", None)
        address, token, _sources = self._resolve_email_inputs(
            principal, {}, "email_list"
        )
        mailbox = self._mailbox_for(principal, "email_list")
        try:
            items = mailbox.list(principal.id, credential=token, address=address)
        except MailboxError as exc:
            self._log.record("email_list", principal.id, None, "error")
            raise _channel_from_mailbox(exc, has_token=bool(token)) from None
        self._log.record("email_list", principal.id, None, "ok")
        return _items(items, _MAIL_ITEM_KEYS)

    def wallet_address(self, caller: BoundCaller, hold_owner: str | None = None) -> str:
        principal = self._own_hold(caller, hold_owner, "wallet_address", None)
        wallet = self._ready_wallet(principal, "wallet_address")
        try:
            addr = wallet.address(principal.id)
        except WalletError as exc:
            self._log.record("wallet_address", principal.id, None, "error")
            raise _channel_from_wallet(exc) from None
        self._log.record("wallet_address", principal.id, None, "ok")
        return addr

    def wallet_sign(
        self,
        caller: BoundCaller,
        message: str,
        hold_owner: str | None = None,
    ) -> str:
        principal = self._own_hold(caller, hold_owner, "wallet_sign", None)
        wallet = self._ready_wallet(principal, "wallet_sign")
        try:
            signature = wallet.sign(principal.id, message)
        except WalletCannotSign:
            self._log.record("wallet_sign", principal.id, None, "cannot_sign")
            raise CannotSign() from None
        except WalletError as exc:
            self._log.record("wallet_sign", principal.id, None, "error")
            raise _channel_from_wallet(exc) from None
        self._log.record("wallet_sign", principal.id, None, "ok")
        return signature

    def wallet_verify(
        self,
        caller: BoundCaller,
        message: str,
        authorization: str,
        hold_owner: str | None = None,
    ) -> dict[str, object]:
        principal = self._own_hold(caller, hold_owner, "wallet_verify", None)
        wallet = self._ready_wallet(principal, "wallet_verify")
        try:
            result = wallet.verify(principal.id, message, authorization)
        except WalletError as exc:
            self._log.record("wallet_verify", principal.id, None, "error")
            raise _channel_from_wallet(exc) from None
        self._log.record("wallet_verify", principal.id, None, "ok")
        picked = _pick(result, ("valid", "address", "scheme"))
        if "valid" in picked:
            picked["valid"] = bool(picked["valid"])
        if "address" in picked:
            picked["address"] = str(picked["address"])
        if "scheme" in picked:
            picked["scheme"] = str(picked["scheme"])
        return picked

    def wallet_balance(
        self, caller: BoundCaller, hold_owner: str | None = None
    ) -> dict[str, str]:
        principal = self._own_hold(caller, hold_owner, "wallet_balance", None)
        wallet = self._ready_wallet(principal, "wallet_balance")
        try:
            result = wallet.balance(principal.id)
        except WalletError as exc:
            self._log.record("wallet_balance", principal.id, None, "error")
            raise _channel_from_wallet(exc) from None
        self._log.record("wallet_balance", principal.id, None, "ok")
        return _balance_view(result)

    def wallet_send(
        self,
        caller: BoundCaller,
        to: str,
        amount: str,
        asset: str = "USDC",
        hold_owner: str | None = None,
    ) -> None:
        principal = self._own_hold(caller, hold_owner, "wallet_send", None)
        wallet = self._ready_wallet(principal, "wallet_send")
        try:
            wallet.send(principal.id, to, amount, asset)
        except WalletCannotSend as exc:
            msg = str(exc)
            if msg == "EOA has no ETH":
                self._log.record("wallet_send", principal.id, None, "no_eth")
                raise NoGas("EOA has no ETH") from None
            self._log.record("wallet_send", principal.id, None, "cannot_send")
            if msg in ("need USDC", "need USD"):
                raise CannotSend(msg) from None
            raise CannotSend() from None
        except WalletError as exc:
            self._log.record("wallet_send", principal.id, None, "error")
            raise _channel_from_wallet(exc) from None
        self._log.record("wallet_send", principal.id, None, "ok")

    def identity(
        self, caller: BoundCaller, hold_owner: str | None = None
    ) -> dict[str, object]:
        principal = self._own_hold(caller, hold_owner, "identity", None)
        mailbox = self._mailbox_for(principal, "identity")
        address, token, _sources = self._resolve_email_inputs(principal, {}, "identity")
        try:
            email = mailbox.describe(principal.id, credential=token, address=address)
        except MailboxError as exc:
            self._log.record("identity", principal.id, None, "error")
            raise _channel_from_mailbox(exc, has_token=bool(token)) from None
        wallet = self._ready_wallet(principal, "identity")
        try:
            wallet_view = wallet.describe(principal.id)
        except WalletError as exc:
            self._log.record("identity", principal.id, None, "error")
            raise _channel_from_wallet(exc) from None
        self._log.record("identity", principal.id, None, "ok")
        return {
            "id": principal.id,
            "recipient": principal.recipient,
            "email": _email_view(email),
            "wallet": _wallet_view(wallet_view),
            "email_backend": self._email_backend,
            "wallet_backend": self._wallet_backend,
        }

    def _resolve_email_inputs(
        self,
        principal: Principal,
        answers: dict[str, str],
        operation: str = "email_connect",
    ) -> tuple[str | None, str | None, dict[str, str]]:
        catalog = bind_of("email", self._email_backend)
        options = list(catalog.options) if catalog is not None else []
        address, address_src = self._resolve_email_field(
            principal,
            OPTION_ADDRESS,
            ENV_EMAIL_ADDRESS,
            EMAIL_ADDRESS_NAME,
            answers,
            options,
            operation,
        )
        credential, cred_src = self._resolve_email_field(
            principal,
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
        principal: Principal,
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
        held = self._optional_hold_value(principal, vault_name, operation)
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
        self, principal: Principal, answers: dict[str, str]
    ) -> None:
        credential = (answers.get(OPTION_CREDENTIAL) or "").strip()
        if credential:
            self._store_put(
                principal, EMAIL_CREDENTIAL_NAME, credential, "email_connect"
            )
        address = (answers.get(OPTION_ADDRESS) or "").strip()
        if address:
            self._store_put(principal, EMAIL_ADDRESS_NAME, address, "email_connect")

    def _persist_email_success(
        self,
        principal: Principal,
        view: dict[str, object],
        address_source: str | None,
    ) -> None:
        email = str(view.get("address") or "").strip()
        if (
            view.get("owned_address")
            and email
            and address_source not in {"env", "alias"}
        ):
            self._store_put(principal, EMAIL_ADDRESS_NAME, email, "email_connect")

    def _store_put(
        self, principal: Principal, name: str, value: str, operation: str
    ) -> None:
        store = self._store_for(principal, operation, name)
        try:
            store.seal(principal.id, name, value)
        except HoldNameExists:
            try:
                existing = store.reveal(principal.id, name)
            except (StoreError, FileNotFoundError) as exc:
                self._fail_store(operation, principal.id, name, exc)
            if existing == value:
                return
            try:
                store.replace(principal.id, name, value)
            except (StoreError, FileNotFoundError) as exc:
                self._fail_store(operation, principal.id, name, exc)
        except (StoreError, FileNotFoundError) as exc:
            self._fail_store(operation, principal.id, name, exc)

    def _own_hold(
        self,
        caller: BoundCaller,
        hold_owner: str | None,
        operation: str,
        name: str | None,
    ) -> Principal:
        try:
            principal_id = require_safe_token(caller.principal_id, "principal id")
            owner = hold_owner if hold_owner is not None else caller.principal_id
            require_safe_token(owner, "hold owner")
            if name is not None:
                require_safe_token(name, "name")
        except ValueError:
            self._log.record(operation, caller.principal_id, name, "refused")
            raise Refused("refused") from None
        try:
            found = self._principals.find(principal_id)
        except RegistryError as exc:
            self._fail_store(operation, principal_id, "registry.json", exc)
        if found is None:
            self._log.record(operation, principal_id, name, "unknown")
            raise UnknownPrincipal("unknown principal")
        if found.recipient != caller.recipient:
            self._log.record(operation, principal_id, name, "refused")
            raise Refused("refused")
        if owner != caller.principal_id:
            self._log.record(operation, principal_id, name, "refused")
            raise Refused("refused")
        return found

    def _store_for(
        self, principal: Principal, operation: str, name: str | None
    ) -> StoreAccess:
        try:
            return self._stores.for_binding(principal.store_binding)
        except (StoreError, FileNotFoundError) as exc:
            self._fail_store(operation, principal.id, name, exc)
            raise StoreFailure("store error") from None

    def _mailbox_for(self, principal: Principal, operation: str) -> MailboxAccess:
        if self._mailboxes is None:
            self._log.record(operation, principal.id, None, "error")
            raise ChannelFailure("channel error")
        try:
            return self._mailboxes.for_binding(self._email_backend)
        except MailboxError as exc:
            self._log.record(operation, principal.id, None, "error")
            raise _channel_from_mailbox(exc) from None

    def _wallet_for(self, principal: Principal, operation: str) -> WalletAccess:
        if self._wallets is None:
            self._log.record(operation, principal.id, None, "error")
            raise ChannelFailure("channel error")
        try:
            return self._wallets.for_binding(self._wallet_backend)
        except WalletError as exc:
            self._log.record(operation, principal.id, None, "error")
            raise _channel_from_wallet(exc) from None

    def _ready_wallet(self, principal: Principal, operation: str) -> WalletAccess:
        wallet = self._wallet_for(principal, operation)
        if not getattr(wallet, "needs_material", False):
            return wallet
        key = self._wallet_key(principal, operation)
        binder = getattr(wallet, "bind_key", None)
        if binder is None:
            self._log.record(operation, principal.id, None, "error")
            raise ChannelFailure("channel error")
        binder(key)
        return wallet

    def _optional_hold_value(
        self, principal: Principal, name: str, operation: str
    ) -> str | None:
        store = self._store_for(principal, operation, name)
        try:
            names = store.list(principal.id)
        except (StoreError, FileNotFoundError) as exc:
            self._fail_store(operation, principal.id, name, exc)
        if name not in names:
            return None
        try:
            return store.reveal(principal.id, name)
        except HoldNameMissing:
            return None
        except (StoreError, FileNotFoundError) as exc:
            self._fail_store(operation, principal.id, name, exc)

    def _wallet_key(self, principal: Principal, operation: str) -> str:
        store = self._store_for(principal, operation, WALLET_KEY_NAME)
        try:
            names = store.list(principal.id)
        except (StoreError, FileNotFoundError) as exc:
            self._fail_store(operation, principal.id, WALLET_KEY_NAME, exc)
        if WALLET_KEY_NAME in names:
            try:
                return store.reveal(principal.id, WALLET_KEY_NAME)
            except HoldNameMissing:
                pass
            except (StoreError, FileNotFoundError) as exc:
                self._fail_store(operation, principal.id, WALLET_KEY_NAME, exc)
        key = generate_secp256k1()
        try:
            store.seal(principal.id, WALLET_KEY_NAME, key)
        except HoldNameExists:
            try:
                return store.reveal(principal.id, WALLET_KEY_NAME)
            except (StoreError, FileNotFoundError) as exc:
                self._fail_store(operation, principal.id, WALLET_KEY_NAME, exc)
        except (StoreError, FileNotFoundError) as exc:
            self._fail_store(operation, principal.id, WALLET_KEY_NAME, exc)
        return key

    def _fail_store(
        self,
        operation: str,
        principal_id: str | None,
        name: str | None,
        exc: BaseException,
    ) -> NoReturn:
        self._log.record(operation, principal_id, name, "store_error")
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
    out: list[dict[str, str]] = []
    for item in items:
        picked = _pick(item, keys)
        out.append({key: str(value) for key, value in picked.items()})
    return out
