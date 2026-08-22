from __future__ import annotations

import json
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from pathlib import Path

from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import keccak, to_checksum_address

from agentself.backends.wallet.contract import (
    CannotSend,
    WalletAccess,
    WalletError,
)
from agentself.backends.wallet.rpc import HttpJsonRpc, RpcClient, _dedup_urls
from agentself.internal.eoa import hex_0x
from agentself.internal.files import (
    VaultBusy,
    atomic_write_text,
    exclusive,
    identity_home,
)
from agentself.internal.log import Log
from agentself.internal.names import require_safe_token

USDC_DECIMALS = 6
ETH_DECIMALS = 18
BALANCE_OF_SELECTOR = keccak(text="balanceOf(address)")[:4]
TRANSFER_SELECTOR = keccak(text="transfer(address,uint256)")[:4]
USDC_ASSET = "USDC"
GAS_ASSET = "ETH"
NEED_USDC = "need USDC"


class ChainWalletAccess(WalletAccess):
    needs_material = True
    chain_name: str
    chain_label: str
    chain_id: int
    default_rpc: str
    usdc: str

    def __init__(
        self,
        log: Log,
        *,
        rpc: RpcClient | None = None,
        rpc_url: str | None = None,
        key_hex: str | None = None,
        rpc_opener=None,
        vault_root: str | Path | None = None,
    ) -> None:
        self._log = log
        self._rpc = rpc
        self._rpc_url = (rpc_url if rpc_url is not None else self.default_rpc).strip()
        self._rpc_opener = rpc_opener
        self._http: HttpJsonRpc | None = None
        self.usdc = to_checksum_address(self.usdc)
        self._key_hex = (key_hex or "").strip() or None
        self._root = Path(vault_root) if vault_root is not None else None

    def bind_key(self, key_hex: str) -> None:
        """Non-ABC helper. Inject EOA material from the Manager, never from StoreAccess."""

        self._key_hex = _normalize_key(key_hex)

    def address(self, principal_id: str) -> str:
        require_safe_token(principal_id, "principal id")
        addr = self._derived_address()
        self._log.record("wallet_address", principal_id, None, "ok")
        return addr

    def sign(self, principal_id: str, message: str) -> str:
        require_safe_token(principal_id, "principal id")
        signed = _account_from_key(self._require_key()).sign_message(
            encode_defunct(text=message)
        )
        sig = hex_0x(signed.signature.hex())
        self._log.record("wallet_sign", principal_id, None, "ok")
        return sig

    def balance(self, principal_id: str) -> dict[str, str]:
        require_safe_token(principal_id, "principal id")
        addr = self._derived_address()
        data = "0x" + BALANCE_OF_SELECTOR.hex() + _pad_address(addr)
        result = self._rpc_request(
            "eth_call", [{"to": self.usdc, "data": data}, "latest"]
        )
        raw = _hex_int(result)
        amount = _format_usdc(raw)
        wei = _hex_int(self._rpc_request("eth_getBalance", [addr, "latest"]))
        self._log.record("wallet_balance", principal_id, None, "ok")
        return {
            "asset": USDC_ASSET,
            "chain": self.chain_name,
            "chain_id": str(self.chain_id),
            "address": addr,
            "amount": amount,
            "raw": str(raw),
            "gas_asset": GAS_ASSET,
            "gas_raw": str(wei),
            "gas_amount": _format_eth(wei),
        }

    def send(self, principal_id: str, to: str, amount: str, asset: str) -> None:
        require_safe_token(principal_id, "principal id")
        self._require_key()
        if (asset or "").strip() != USDC_ASSET:
            self._log.record("wallet_send", principal_id, None, "cannot_send")
            raise CannotSend(NEED_USDC)
        if self._root is None:
            self._send_once(principal_id, to, amount, asset)
            return
        try:
            with exclusive(self._root):
                self._send_once(principal_id, to, amount, asset)
        except VaultBusy as exc:
            raise WalletError("rpc failed") from exc

    def describe(self, principal_id: str) -> dict[str, object]:
        require_safe_token(principal_id, "principal id")
        return {
            "address": self._derived_address(),
            "chain": self.chain_name,
            "chain_label": self.chain_label,
            "chain_id": self.chain_id,
            "asset": USDC_ASSET,
        }

    def _send_once(self, principal_id: str, to: str, amount: str, asset: str) -> None:
        addr = self._derived_address()
        wei = _hex_int(self._rpc_request("eth_getBalance", [addr, "latest"]))
        if wei == 0:
            self._log.record("wallet_send", principal_id, None, "no_eth")
            raise CannotSend("EOA has no ETH")
        try:
            units = _usdc_units(amount)
        except CannotSend:
            self._log.record("wallet_send", principal_id, None, "cannot_send")
            raise
        held = _hex_int(
            self._rpc_request(
                "eth_call",
                [
                    {
                        "to": self.usdc,
                        "data": "0x" + BALANCE_OF_SELECTOR.hex() + _pad_address(addr),
                    },
                    "latest",
                ],
            )
        )
        if held < units:
            self._log.record("wallet_send", principal_id, None, "cannot_send")
            raise CannotSend(NEED_USDC)
        try:
            dest = to_checksum_address(to)
        except (ValueError, TypeError):
            self._log.record("wallet_send", principal_id, None, "cannot_send")
            raise CannotSend(NEED_USDC) from None
        pending = self._load_pending(principal_id)
        if pending and _same_intent(pending, dest, units, addr, self.chain_id):
            self._finish_pending(principal_id, pending)
            return
        data = (
            "0x"
            + TRANSFER_SELECTOR.hex()
            + _pad_address(dest)
            + format(units, "x").zfill(64)
        )
        nonce = _hex_int(
            self._rpc_request("eth_getTransactionCount", [addr, "pending"])
        )
        gas_price = _hex_int(self._rpc_request("eth_gasPrice", []))
        gas_limit = _hex_int(
            self._rpc_request(
                "eth_estimateGas",
                [{"from": addr, "to": self.usdc, "data": data, "value": "0x0"}],
            )
        )
        tx = {
            "to": self.usdc,
            "value": 0,
            "gas": gas_limit,
            "gasPrice": gas_price,
            "nonce": nonce,
            "data": data,
            "chainId": self.chain_id,
        }
        try:
            signed = _account_from_key(self._require_key()).sign_transaction(tx)
        except WalletError:
            raise
        except Exception:
            raise WalletError("rpc failed") from None
        raw_hex = hex_0x(signed.raw_transaction.hex())
        tx_hash = _signed_hash(signed)
        record = {
            "chain_id": self.chain_id,
            "from": addr,
            "to": dest,
            "units": units,
            "asset": USDC_ASSET,
            "nonce": nonce,
            "hash": tx_hash,
            "raw": raw_hex,
        }
        self._save_pending(principal_id, record)
        self._broadcast(principal_id, record, signed)

    def _finish_pending(self, principal_id: str, pending: dict[str, object]) -> None:
        tx_hash = str(pending.get("hash") or "")
        if self._tx_known(tx_hash):
            self._log.record("wallet_send", principal_id, None, _ok_hash(tx_hash))
            return
        raw = str(pending.get("raw") or "")
        if not raw.startswith("0x"):
            raise WalletError("rpc failed")
        self._broadcast(principal_id, pending, None)

    def _broadcast(
        self,
        principal_id: str,
        pending: dict[str, object],
        signed: object | None,
    ) -> None:
        raw = str(pending.get("raw") or "")
        tx_hash = str(pending.get("hash") or "")
        try:
            result = self._rpc_request("eth_sendRawTransaction", [raw])
        except WalletError:
            if self._tx_known(tx_hash):
                self._log.record("wallet_send", principal_id, None, _ok_hash(tx_hash))
                return
            raise
        if signed is not None:
            self._log.record(
                "wallet_send", principal_id, None, _send_result(result, signed)
            )
            return
        self._log.record("wallet_send", principal_id, None, _ok_hash(tx_hash))

    def _tx_known(self, tx_hash: str) -> bool:
        if not tx_hash.startswith("0x") or len(tx_hash) != 66:
            return False
        try:
            found = self._rpc_request("eth_getTransactionByHash", [tx_hash])
        except WalletError:
            return False
        if not isinstance(found, dict):
            return False
        got = str(found.get("hash") or "")
        return got.lower() == tx_hash.lower()

    def _pending_path(self, principal_id: str) -> Path | None:
        if self._root is None:
            return None
        return identity_home(self._root, principal_id) / "wallet" / "pending-send.json"

    def _load_pending(self, principal_id: str) -> dict[str, object] | None:
        path = self._pending_path(principal_id)
        if path is None or not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WalletError("rpc failed") from exc
        if not isinstance(data, dict):
            raise WalletError("rpc failed")
        return data

    def _save_pending(self, principal_id: str, record: dict[str, object]) -> None:
        path = self._pending_path(principal_id)
        if path is None:
            return
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = json.dumps(record, indent=2, sort_keys=True) + "\n"
        try:
            atomic_write_text(path, payload)
        except OSError as exc:
            raise WalletError("rpc failed") from exc

    def _derived_address(self) -> str:
        addr = _account_from_key(self._require_key()).address
        return to_checksum_address(addr)

    def _require_key(self) -> str:
        if not self._key_hex:
            raise WalletError("missing key")
        return self._key_hex

    def _rpc_urls(self) -> list[str]:
        extras = list(getattr(self, "fallback_rpcs", ()) or ())
        return _dedup_urls(self._rpc_url, extras)

    def _http_client(self) -> HttpJsonRpc:
        if self._http is None:
            urls = self._rpc_urls()
            if not urls:
                raise WalletError("no RPC configured")
            self._http = HttpJsonRpc(
                urls[0], fallbacks=urls[1:], opener=self._rpc_opener
            )
        return self._http

    def _rpc_request(self, method: str, params: list[object]) -> object:
        if self._rpc is not None:
            return self._rpc.request(method, params)
        return self._http_client().request(method, params)


def _normalize_key(key_hex: str) -> str:
    key = key_hex.strip()
    if not key:
        raise WalletError("missing key")
    return key


def _pad_address(address: str) -> str:
    hex_part = address.lower().removeprefix("0x")
    return hex_part.rjust(64, "0")


def _hex_int(value: object) -> int:
    if value in (None, "0x", ""):
        return 0
    if isinstance(value, int):
        if value < 0 or value.bit_length() > 256:
            raise WalletError("rpc failed")
        return value
    text = str(value).strip()
    if len(text) > 80:
        raise WalletError("rpc failed")
    try:
        parsed = int(text, 16) if text.startswith(("0x", "0X")) else int(text)
    except (TypeError, ValueError):
        raise WalletError("rpc failed") from None
    if parsed < 0 or parsed.bit_length() > 256:
        raise WalletError("rpc failed")
    return parsed


def _format_usdc(raw: int) -> str:
    return _format_units(raw, USDC_DECIMALS)


def _format_eth(raw: int) -> str:
    return _format_units(raw, ETH_DECIMALS)


def _format_units(raw: int, decimals: int) -> str:
    sign = "-" if raw < 0 else ""
    raw = abs(raw)
    whole, frac = divmod(raw, 10**decimals)
    if frac == 0:
        return f"{sign}{whole}"
    return f"{sign}{whole}.{frac:0{decimals}d}".rstrip("0")


def _usdc_units(amount: str) -> int:
    try:
        value = Decimal(str(amount).strip())
    except (InvalidOperation, ValueError, ArithmeticError) as exc:
        raise CannotSend(NEED_USDC) from exc
    if not value.is_finite() or value < 0:
        raise CannotSend(NEED_USDC)
    scaled = value * (Decimal(10) ** USDC_DECIMALS)
    return int(scaled.to_integral_value(rounding=ROUND_DOWN))


def _account_from_key(key_hex: str):
    try:
        return Account.from_key(key_hex)
    except Exception:
        raise WalletError("missing key") from None


def _ok_hash(value: object) -> str:
    text = str(value or "").strip()
    if text.startswith("0x") and len(text) == 66:
        body = text[2:]
        if all(ch in "0123456789abcdefABCDEF" for ch in body):
            return f"ok {text}"
    return "ok"


def _send_result(result: object, signed: object) -> str:
    hashed = _ok_hash(result)
    if hashed != "ok":
        return hashed
    digest = _signed_hash(signed)
    if digest:
        return _ok_hash(digest)
    return "ok"


def _signed_hash(signed: object) -> str:
    digest = getattr(signed, "hash", None)
    if digest is None:
        return ""
    hexed = digest.hex() if hasattr(digest, "hex") else str(digest)
    return hex_0x(hexed)


def _same_intent(
    pending: dict[str, object],
    dest: str,
    units: int,
    addr: str,
    chain_id: int,
) -> bool:
    try:
        pending_units = int(str(pending.get("units")))
        pending_chain = int(str(pending.get("chain_id")))
    except (TypeError, ValueError):
        return False
    return (
        str(pending.get("to") or "") == dest
        and pending_units == units
        and str(pending.get("asset") or "") == USDC_ASSET
        and str(pending.get("from") or "") == addr
        and pending_chain == chain_id
    )
