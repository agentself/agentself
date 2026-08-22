from __future__ import annotations

import builtins
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
from agentself.internal.names import require_safe_token
from agentself.internal.registry import (
    RegistryError,
    RegistryFormatError,
)
from agentself.internal.types import BoundCaller, Principal

ALLOWED_BINDINGS = frozenset({"sops", "pass"})
WALLET_KEY_NAME = "wallet.key"
SEND_TOKEN_NAME = "email.send.token"
EMAIL_ADDRESS_NAME = "email.address"
PROTECTED_HOLD_NAMES = frozenset({WALLET_KEY_NAME})
_EMAIL_VIEW_KEYS = ("address", "owned_address", "needs_domain")
_WALLET_VIEW_KEYS = (
    "address",
    "chain",
    "chain_label",
    "chain_id",
    "asset",
    "kind",
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
    ) -> None:
        principal = self._own_hold(caller, hold_owner, "seal", name)
        store = self._store_for(principal, "seal", name)
        try:
            store.seal(principal.id, name, value)
        except HoldNameExists:
            self._log.record("seal", principal.id, name, "exists")
            raise Refused("refused") from None
        except (StoreError, FileNotFoundError) as exc:
            self._fail_store("seal", principal.id, name, exc)
        self._log.record("seal", principal.id, name, "ok")

    def reveal(
        self,
        caller: BoundCaller,
        name: str,
        hold_owner: str | None = None,
    ) -> str:
        principal = self._own_hold(caller, hold_owner, "reveal", name)
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
        self._log.record("list", principal.id, None, "ok")
        return list(names)

    def delete(
        self,
        caller: BoundCaller,
        name: str,
        hold_owner: str | None = None,
    ) -> None:
        principal = self._own_hold(caller, hold_owner, "delete", name)
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
        self, caller: BoundCaller, hold_owner: str | None = None
    ) -> dict[str, object]:
        principal = self._own_hold(caller, hold_owner, "email_connect", None)
        token = self._optional_hold_value(principal, SEND_TOKEN_NAME, "email_connect")
        address = self._optional_hold_value(
            principal, EMAIL_ADDRESS_NAME, "email_connect"
        )
        mailbox = self._mailbox_for(principal, "email_connect")
        try:
            desc = mailbox.connect(principal.id, send_token=token, address=address)
        except MailboxError as exc:
            self._log.record("email_connect", principal.id, None, "error")
            raise _channel_from_mailbox(exc, has_token=bool(token)) from None
        view = _email_view(desc)
        email = str(view.get("address") or "").strip()
        if view.get("owned_address") and email and not address:
            store = self._store_for(principal, "email_connect", EMAIL_ADDRESS_NAME)
            try:
                store.seal(principal.id, EMAIL_ADDRESS_NAME, email)
            except HoldNameExists:
                pass
            except (StoreError, FileNotFoundError) as exc:
                self._fail_store("email_connect", principal.id, EMAIL_ADDRESS_NAME, exc)
        self._log.record("email_connect", principal.id, None, "ok")
        return view

    def email_send(
        self,
        caller: BoundCaller,
        to: str,
        subject: str,
        body: str,
        hold_owner: str | None = None,
    ) -> None:
        principal = self._own_hold(caller, hold_owner, "email_send", None)
        token = self._optional_hold_value(principal, SEND_TOKEN_NAME, "email_send")
        address = self._optional_hold_value(principal, EMAIL_ADDRESS_NAME, "email_send")
        mailbox = self._mailbox_for(principal, "email_send")
        try:
            desc = mailbox.describe(principal.id, send_token=token, address=address)
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
                send_token=token,
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
        token = self._optional_hold_value(principal, SEND_TOKEN_NAME, "email_recv")
        address = self._optional_hold_value(principal, EMAIL_ADDRESS_NAME, "email_recv")
        mailbox = self._mailbox_for(principal, "email_recv")
        try:
            messages = mailbox.recv(
                principal.id,
                send_token=token,
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
        token = self._optional_hold_value(principal, SEND_TOKEN_NAME, "email_list")
        address = self._optional_hold_value(principal, EMAIL_ADDRESS_NAME, "email_list")
        mailbox = self._mailbox_for(principal, "email_list")
        try:
            items = mailbox.list(principal.id, send_token=token, address=address)
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
        token = self._optional_hold_value(principal, SEND_TOKEN_NAME, "identity")
        address = self._optional_hold_value(principal, EMAIL_ADDRESS_NAME, "identity")
        try:
            email = mailbox.describe(principal.id, send_token=token, address=address)
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
    cause = exc.__cause__
    cause_name = type(cause).__name__.lower() if cause is not None else ""
    if has_token is False or (
        "no token" in low
        or "missing credentials" in low
        or "missing token" in low
        or low == "no_token"
    ):
        reason = "no_token"
    elif "need address" in low:
        reason = "need_address"
    elif (
        "rpc" in low
        or "http" in low
        or "network" in low
        or "timeout" in low
        or "connection" in low
        or "urlerror" in low
        or "urlerror" in cause_name
        or "timeout" in cause_name
        or isinstance(cause, (OSError, TimeoutError))
    ):
        reason = "rpc"
    else:
        reason = "mailbox_error"
    return ChannelFailure("channel error", reason=reason)


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
