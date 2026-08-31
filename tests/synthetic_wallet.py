from __future__ import annotations

from agentself.backends.wallet.contract import (
    CannotSend,
    WalletAccess,
    WalletMaterial,
)
from agentself.internal.names import require_safe_token

_ASSET = "NOTE"
_SCHEME = "ed25519"
_ADDRESS = "note.1"
MATERIAL_NAME = "note.seed"
_MATERIAL = "synthetic-note-seed"


class SyntheticWalletAccess(WalletAccess):
    """Test double: declares non-EVM material and no chain asset."""

    def __init__(self) -> None:
        self.bound_material: str | None = None

    def required_material(self):
        return WalletMaterial(name=MATERIAL_NAME)

    def create_material(self) -> str:
        return _MATERIAL

    def bind_material(self, value: str) -> None:
        self.bound_material = value

    def address(self, identity_id: str) -> str:
        require_safe_token(identity_id, "identity id")
        return _ADDRESS

    def authorize(self, identity_id: str, message: str) -> str:
        require_safe_token(identity_id, "identity id")
        return f"{_SCHEME}:{message}"

    def balance(self, identity_id: str) -> dict[str, str]:
        require_safe_token(identity_id, "identity id")
        return {"asset": _ASSET, "amount": "0", "address": _ADDRESS}

    def send(self, identity_id: str, to: str, amount: str, asset: str) -> str:
        return self.validate_send(identity_id, to, amount, asset)

    def validate_send(self, identity_id: str, to: str, amount: str, asset: str) -> str:
        require_safe_token(identity_id, "identity id")
        del to, amount
        wanted = (asset or "").strip()
        if not wanted:
            wanted = _ASSET
        if wanted != _ASSET:
            raise CannotSend("unsupported asset", reason="unsupported_asset")
        return wanted

    def describe(self, identity_id: str) -> dict[str, object]:
        require_safe_token(identity_id, "identity id")
        return {
            "address": _ADDRESS,
            "asset": _ASSET,
            "scheme": _SCHEME,
            "kind": "test",
        }

    def verify(
        self, identity_id: str, message: str, authorization: str
    ) -> dict[str, object]:
        require_safe_token(identity_id, "identity id")
        valid = authorization == f"{_SCHEME}:{message}"
        return {"valid": valid, "address": _ADDRESS, "scheme": _SCHEME}
