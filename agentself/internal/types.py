from dataclasses import dataclass
from typing import Literal, NotRequired, TypedDict

MailboxMessage = TypedDict(
    "MailboxMessage",
    {
        "id": str,
        "ref": str,
        "from": str,
        "to": str,
        "subject": str,
        "body": str,
        "reason": str,
        "status": str,
        "acted": bool,
        "rejected": bool,
    },
    total=False,
)


class MailboxView(TypedDict, total=False):
    address: str | None
    owned_address: bool
    needs_domain: bool
    status: str


EmailConnectView = TypedDict(
    "EmailConnectView",
    {
        "status": Literal[
            "connected", "input_required", "action_required", "pending", "failed"
        ],
        "address": str | None,
        "owned_address": bool,
        "needs_domain": bool,
        "state": str,
        "human_action_required": bool,
        "continue": str,
        "option": dict[str, object],
        "message": str,
        "reason": str,
        "retryable": bool,
    },
    total=False,
)


class WalletView(TypedDict, total=False):
    address: str
    chain: str
    chain_label: str
    chain_id: int
    asset: str
    kind: str
    scheme: str


class WalletBalance(TypedDict, total=False):
    asset: str
    chain: str
    chain_id: str
    address: str
    amount: str
    raw: str
    gas_asset: str
    gas_raw: str
    gas_amount: str


class WalletAuthorization(TypedDict, total=False):
    valid: bool
    address: str
    scheme: str


class WalletSendResult(TypedDict):
    asset: str
    hash: NotRequired[str]


class WalletMaterialStatus(TypedDict):
    ready: bool
    missing: str | None


class IdentityView(TypedDict):
    id: str
    recipient: str
    email: MailboxView
    wallet: WalletView
    email_backend: str
    wallet_backend: str


@dataclass(frozen=True)
class BoundCaller:
    """Never a private key."""

    identity_id: str
    recipient: str


@dataclass(frozen=True)
class Identity:
    """Never a private key."""

    id: str
    recipient: str
    store_binding: str
    wallet_material_names: tuple[str, ...] = ()

    def public_view(self) -> dict[str, str]:
        return {"id": self.id, "recipient": self.recipient}
