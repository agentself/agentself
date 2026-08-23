from __future__ import annotations

import builtins
import hashlib
import hmac
import json
import os
import secrets
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
from agentself.internal.log import Log
from agentself.internal.names import (
    EMAIL_ADDRESS_NAME,
    EMAIL_CONTINUATION_NAME,
    EMAIL_CREDENTIAL_NAME,
    PROTECTED_SECRET_NAMES,
    is_reserved_secret_name,
    require_safe_token,
)
from agentself.internal.registry import (
    RegistryError,
    RegistryFormatError,
)
from agentself.internal.setup import (
    ENV_EMAIL_ADDRESS,
    ENV_EMAIL_CREDENTIAL,
    OPTION_ADDRESS,
    OPTION_CREDENTIAL,
    SETUP_ACTION_REQUIRED,
    SETUP_CONNECTED,
    SETUP_FAILED,
    continue_command,
    decode_state,
    encode_state,
    public_setup_option,
    setup_status_of,
)
from agentself.internal.types import BoundCaller, Identity

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
        allowed_store_bindings: frozenset[str] | None = None,
    ) -> None:
        self._identities = identities
        self._stores = stores
        self._log = log
        self._mailboxes = mailboxes
        self._wallets = wallets
        self._email_backend = email_backend or "agentmail"
        self._wallet_backend = wallet_backend or "base"
        # Test fallback; production compose injects CHANNELS["store"].names.
        self._allowed_store_bindings = (
            frozenset(allowed_store_bindings)
            if allowed_store_bindings is not None
            else frozenset(("sops", "pass"))
        )

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
            identity = found
        else:
            if store_binding not in self._allowed_store_bindings:
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
        except (StoreError, FileNotFoundError) as exc:
            self._fail_store("init", identity_id, None, exc)
        try:
            store.prepare(identity.id)
        except (StoreError, FileNotFoundError) as exc:
            self._log.record("init", identity_id, None, "store_error")
            tool = _host_tool_from(exc)
            if tool:
                raise HostToolMissing(tool) from None
            raise StoreFailure(str(exc).strip() or "store error") from None
        try:
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
        blob: object | None = None
        token = (state or "").strip()
        if token:
            loaded = self._load_email_continuation(identity, token)
            if loaded is None:
                self._log.record("email_connect", identity.id, None, "error")
                return {"status": SETUP_FAILED, "reason": "unknown setup"}
            blob, asked = loaded
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
                state=blob,
            )
        except MailboxError as exc:
            mapped = _channel_from_mailbox(exc)
            if mapped.reason != "rpc":
                self._delete_email_continuation(identity)
            self._log.record("email_connect", identity.id, None, "error")
            raise mapped from None
        status = setup_status_of(desc)
        if status == SETUP_CONNECTED:
            self._persist_setup_answers(identity, mailbox, incoming)
            view = _email_view(desc)
            self._persist_email_success(identity, view, sources.get(OPTION_ADDRESS))
            self._delete_email_continuation(identity)
            view["status"] = SETUP_CONNECTED
            self._log.record("email_connect", identity.id, None, "ok")
            return view
        if status == SETUP_FAILED:
            self._delete_email_continuation(identity)
            self._log.record("email_connect", identity.id, None, "error")
            reason = str(desc.get("reason") or "error")
            return {"status": SETUP_FAILED, "reason": reason}
        self._persist_setup_answers(identity, mailbox, incoming)
        option = _setup_option_of(desc)
        human = (
            bool(desc.get("human_action_required")) or status == SETUP_ACTION_REQUIRED
        )
        next_state = self._store_email_continuation(
            identity,
            desc.get("continuation"),
            str(option.get("name") or "") if option else "",
        )
        payload: dict[str, object] = {
            "status": status,
            "state": next_state,
            "human_action_required": human,
            "continue": continue_command(next_state),
        }
        if option:
            payload["option"] = public_setup_option(option)
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
            raise _channel_from_mailbox(exc) from None
        if not desc.get("owned_address"):
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
            raise _channel_from_mailbox(exc) from None
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
            raise _channel_from_mailbox(exc) from None
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
            raise _channel_from_mailbox(exc) from None
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
        asset: str = "",
    ) -> str:
        identity = self._require_identity(caller, "wallet_send", None)
        wallet = self._ready_wallet(identity, "wallet_send")
        try:
            used = (wallet.send(identity.id, to, amount, asset) or "").strip()
        except WalletCannotSend as exc:
            reason = getattr(exc, "reason", None) or "cannot_send"
            if reason == "no_gas":
                self._log.record("wallet_send", identity.id, None, "no_gas")
                raise NoGas() from None
            self._log.record("wallet_send", identity.id, None, "cannot_send")
            raise CannotSend(_send_message(reason), reason=reason) from None
        except WalletError as exc:
            self._log.record("wallet_send", identity.id, None, "error")
            raise _channel_from_wallet(exc) from None
        if not used:
            self._log.record("wallet_send", identity.id, None, "cannot_send")
            raise CannotSend(reason="cannot_send")
        self._log.record("wallet_send", identity.id, None, "ok")
        return used

    def wallet_material_status(self, caller: BoundCaller) -> dict[str, object]:
        identity = self._require_identity(caller, "wallet_material", None)
        wallet = self._wallet_for(identity, "wallet_material")
        need = wallet.required_material()
        if need is None:
            self._log.record("wallet_material", identity.id, None, "ok")
            return {"ready": True, "missing": None}
        held = self._optional_secret_value(identity, need.name, "wallet_material")
        if held:
            self._log.record("wallet_material", identity.id, None, "ok")
            return {"ready": True, "missing": None}
        self._log.record("wallet_material", identity.id, None, "missing")
        return {"ready": False, "missing": need.name}

    def identity(self, caller: BoundCaller) -> dict[str, object]:
        identity = self._require_identity(caller, "identity", None)
        mailbox = self._mailbox_for(identity, "identity")
        address, token, _sources = self._resolve_email_inputs(identity, {}, "identity")
        try:
            email = mailbox.describe(identity.id, credential=token, address=address)
        except MailboxError as exc:
            self._log.record("identity", identity.id, None, "error")
            raise _channel_from_mailbox(exc) from None
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
        address, address_src = self._resolve_email_field(
            identity,
            OPTION_ADDRESS,
            ENV_EMAIL_ADDRESS,
            EMAIL_ADDRESS_NAME,
            answers,
            operation,
        )
        credential, cred_src = self._resolve_email_field(
            identity,
            OPTION_CREDENTIAL,
            ENV_EMAIL_CREDENTIAL,
            EMAIL_CREDENTIAL_NAME,
            answers,
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
        operation: str,
    ) -> tuple[str | None, str | None]:
        env_val = os.environ.get(env_generic, "").strip()
        if env_val:
            return env_val, "env"
        held = self._optional_secret_value(identity, vault_name, operation)
        if held:
            return held, "vault"
        setup_val = (answers.get(option_name) or "").strip()
        if setup_val:
            return setup_val, "setup"
        return None, None

    def _persist_setup_answers(
        self,
        identity: Identity,
        mailbox: MailboxAccess,
        answers: dict[str, str],
    ) -> None:
        descriptors = {
            str(item.get("name") or ""): item
            for item in mailbox.setup_options()
            if str(item.get("name") or "").strip()
        }
        for name, value in answers.items():
            text = (value or "").strip()
            if not text:
                continue
            option = descriptors.get(name)
            if option is None:
                continue
            if option.get("runtime_only") is True:
                continue
            if option.get("persist") is not True:
                continue
            persist_as = str(option.get("persist_as") or "").strip()
            key = persist_as or f"email.{self._email_backend}.{name}"
            if is_reserved_secret_name(key) or key in PROTECTED_SECRET_NAMES:
                self._log.record("email_connect", identity.id, key, "refused")
                raise Refused("refused")
            try:
                require_safe_token(key, "name")
            except ValueError:
                self._log.record("email_connect", identity.id, key, "refused")
                raise Refused("refused") from None
            self._store_put(identity, key, text, "email_connect")

    def _persist_email_success(
        self,
        identity: Identity,
        view: dict[str, object],
        address_source: str | None,
    ) -> None:
        email = str(view.get("address") or "").strip()
        if view.get("owned_address") and email and address_source != "env":
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

    def _store_delete(self, identity: Identity, name: str, operation: str) -> None:
        store = self._store_for(identity, operation, name)
        try:
            store.delete(identity.id, name)
        except SecretMissing:
            return
        except (StoreError, FileNotFoundError) as exc:
            self._fail_store(operation, identity.id, name, exc)

    def _store_email_continuation(
        self, identity: Identity, blob: object, option: str
    ) -> str:
        nonce = secrets.token_urlsafe(16)
        mac = _continuation_mac(
            _continuation_key(identity), nonce, blob, option, identity.id
        )
        envelope = {"nonce": nonce, "blob": blob, "option": option, "mac": mac}
        self._store_put(
            identity,
            EMAIL_CONTINUATION_NAME,
            json.dumps(envelope, sort_keys=True, separators=(",", ":")),
            "email_connect",
        )
        return encode_state({"n": nonce})

    def _load_email_continuation(
        self, identity: Identity, token: str
    ) -> tuple[object | None, str] | None:
        decoded = decode_state(token)
        if decoded is None:
            return None
        nonce = str(decoded.get("n") or "").strip()
        if not nonce:
            return None
        raw = self._optional_secret_value(
            identity, EMAIL_CONTINUATION_NAME, "email_connect"
        )
        if not raw:
            return None
        try:
            stored = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(stored, dict):
            return None
        stored_nonce = str(stored.get("nonce") or "")
        option = str(stored.get("option") or "")
        mac = stored.get("mac")
        if stored_nonce != nonce or not isinstance(mac, str) or not mac:
            return None
        expected = _continuation_mac(
            _continuation_key(identity),
            stored_nonce,
            stored.get("blob"),
            option,
            identity.id,
        )
        if not hmac.compare_digest(mac, expected):
            return None
        return stored.get("blob"), option

    def _delete_email_continuation(self, identity: Identity) -> None:
        self._store_delete(identity, EMAIL_CONTINUATION_NAME, "email_connect")

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
        need = wallet.required_material()
        if need is None:
            return wallet
        value = self._optional_secret_value(identity, need.name, operation)
        if not value:
            try:
                created = wallet.create_material()
            except WalletError as exc:
                self._log.record(operation, identity.id, None, "error")
                raise _channel_from_wallet(exc) from None
            store = self._store_for(identity, operation, need.name)
            try:
                store.create(identity.id, need.name, created)
            except SecretExists:
                try:
                    created = store.get(identity.id, need.name)
                except (StoreError, FileNotFoundError) as exc:
                    self._fail_store(operation, identity.id, need.name, exc)
            except (StoreError, FileNotFoundError) as exc:
                self._fail_store(operation, identity.id, need.name, exc)
            value = created
        wallet.bind_material(value)
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


def _continuation_key(identity: Identity) -> bytes:
    return hmac.new(
        identity.recipient.encode("utf-8"),
        identity.id.encode("utf-8"),
        hashlib.sha256,
    ).digest()


def _continuation_mac(
    key: bytes, nonce: str, blob: object, option: str, identity_id: str
) -> str:
    payload = json.dumps(
        {"blob": blob, "id": identity_id, "nonce": nonce, "option": option},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def _channel_from_mailbox(exc: BaseException) -> ChannelFailure:
    msg = str(exc)
    low = msg.lower()
    if (
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
    tagged = getattr(exc, "reason", None)
    if tagged == "rpc" or msg in {"rpc failed", "no RPC configured"}:
        reason = "rpc"
    elif "missing key" in low:
        reason = "no_key"
    elif tagged == "cannot_send" or "cannot send" in low:
        reason = "cannot_send"
    else:
        reason = "error"
    return ChannelFailure("channel error", reason=reason)


def _send_message(reason: str) -> str:
    if reason == "insufficient_asset":
        return "need funds"
    return "backend cannot send"


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
