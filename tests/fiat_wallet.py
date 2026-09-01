from __future__ import annotations

from agentself.backends.wallet.contract import CannotSend, WalletAccess
from agentself.internal.names import require_safe_token

_ASSET = "USD"
_SCHEME = "fiat"
_ADDRESS = "acct.1"


class FiatWalletAccess(WalletAccess):
    """Non-chain wallet used to prove send details and named balances stay generic."""

    def __init__(self) -> None:
        self.sends: list[tuple[str, str, str, str]] = []
        self.holdings = {_ASSET: "100", "EUR": "25"}

    def address(self, identity_id: str) -> str:
        require_safe_token(identity_id, "identity id")
        return _ADDRESS

    def authorize(self, identity_id: str, message: str) -> str:
        require_safe_token(identity_id, "identity id")
        return f"{_SCHEME}:{message}"

    def balance(self, identity_id: str, asset: str = "") -> dict[str, str]:
        require_safe_token(identity_id, "identity id")
        wanted = (asset or "").strip() or _ASSET
        amount = self.holdings.get(wanted)
        if amount is None:
            raise CannotSend("unsupported asset", reason="unsupported_asset")
        return {"asset": wanted, "amount": amount, "address": _ADDRESS}

    def send(
        self,
        identity_id: str,
        to: str,
        amount: str,
        asset: str,
        details: str = "",
    ) -> str:
        wanted = self.validate_send(identity_id, to, amount, asset, details)
        self.sends.append((to, amount, wanted, details))
        return wanted

    def validate_send(
        self,
        identity_id: str,
        to: str,
        amount: str,
        asset: str,
        details: str = "",
    ) -> str:
        require_safe_token(identity_id, "identity id")
        del to, details
        wanted = (asset or "").strip() or _ASSET
        if wanted not in self.holdings:
            raise CannotSend("unsupported asset", reason="unsupported_asset")
        try:
            value = float(str(amount).strip())
        except (TypeError, ValueError):
            raise CannotSend("invalid amount", reason="invalid_amount") from None
        if value <= 0:
            raise CannotSend("invalid amount", reason="invalid_amount")
        return wanted

    def describe(self, identity_id: str) -> dict[str, object]:
        require_safe_token(identity_id, "identity id")
        return {
            "address": _ADDRESS,
            "asset": _ASSET,
            "scheme": _SCHEME,
            "kind": "fiat",
        }

    def verify(
        self, identity_id: str, message: str, authorization: str
    ) -> dict[str, object]:
        require_safe_token(identity_id, "identity id")
        valid = authorization == f"{_SCHEME}:{message}"
        return {"valid": valid, "address": _ADDRESS, "scheme": _SCHEME}
