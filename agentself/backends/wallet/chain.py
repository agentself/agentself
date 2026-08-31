from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from eth_account import Account
from eth_account.messages import SignableMessage, encode_defunct, encode_typed_data
from eth_utils import keccak, to_checksum_address

from agentself.backends.wallet.contract import (
    CannotAuthorize,
    CannotSend,
    WalletAccess,
    WalletError,
    WalletMaterial,
)
from agentself.backends.wallet.rpc import HttpJsonRpc, RpcClient, _dedup_urls
from agentself.internal.eoa import generate_secp256k1, hex_0x
from agentself.internal.files import (
    IdentityBusy,
    atomic_write_text,
    exclusive,
    identity_home,
)
from agentself.internal.format import load_json_file
from agentself.internal.log import Log
from agentself.internal.names import WALLET_KEY_NAME, require_safe_token
from agentself.internal.text import UTF8_BOM
from agentself.internal.types import WalletAuthorization, WalletBalance, WalletView

USDC_DECIMALS = 6
ETH_DECIMALS = 18
BALANCE_OF_SELECTOR = keccak(text="balanceOf(address)")[:4]
TRANSFER_SELECTOR = keccak(text="transfer(address,uint256)")[:4]
USDC_ASSET = "USDC"
GAS_ASSET = "ETH"


class ChainWalletAccess(WalletAccess):
    chain_name: str
    chain_label: str
    chain_id: int
    default_rpc: str
    fallback_rpcs: tuple[str, ...] = ()
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
        if rpc_url is None:
            self._rpc_url = self.default_rpc.strip()
            self._rpc_override = False
        else:
            self._rpc_url = rpc_url.strip()
            self._rpc_override = bool(self._rpc_url)
        self._rpc_opener = rpc_opener
        self._http: HttpJsonRpc | None = None
        self.usdc = to_checksum_address(self.usdc)
        self._key_hex = (key_hex or "").strip() or None
        self._root = Path(vault_root) if vault_root is not None else None
        self._payment_hash = ""

    def required_material(self) -> WalletMaterial | None:
        return WalletMaterial(name=WALLET_KEY_NAME)

    def create_material(self) -> str:
        return generate_secp256k1()

    def bind_material(self, value: str) -> None:
        self._key_hex = _normalize_key(value)

    def address(self, identity_id: str) -> str:
        require_safe_token(identity_id, "identity id")
        addr = self._derived_address()
        self._log.record("wallet_address", identity_id, None, "ok")
        return addr

    def authorize(self, identity_id: str, message: str) -> str:
        require_safe_token(identity_id, "identity id")
        signed = self._account().sign_message(_encode_statement(message))
        sig = hex_0x(signed.signature.hex())
        self._log.record("wallet_authorize", identity_id, None, "ok")
        return sig

    def balance(self, identity_id: str) -> WalletBalance:
        require_safe_token(identity_id, "identity id")
        addr = self._derived_address()
        raw_result = self._rpc_request(
            "eth_call",
            [{"to": self.usdc, "data": _balance_of_data(addr)}, "latest"],
        )
        raw = _hex_int(raw_result)
        amount = _format_usdc(raw)
        wei = _hex_int(self._rpc_request("eth_getBalance", [addr, "latest"]))
        self._log.record("wallet_balance", identity_id, None, "ok")
        result: WalletBalance = {
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
        return result

    def send(self, identity_id: str, to: str, amount: str, asset: str) -> str:
        require_safe_token(identity_id, "identity id")
        self._require_key()
        wanted = self._send_asset(identity_id, asset)
        if self._root is None:
            self._send_once(identity_id, to, amount)
            return wanted
        try:
            with exclusive(self._root):
                self._send_once(identity_id, to, amount)
        except IdentityBusy as exc:
            raise WalletError("identity directory busy") from exc
        return wanted

    def validate_send(self, identity_id: str, to: str, amount: str, asset: str) -> str:
        require_safe_token(identity_id, "identity id")
        self._require_key()
        wanted = self._send_asset(identity_id, asset)
        addr, _dest, _units, data, wei = self._validate_send_once(
            identity_id, to, amount
        )
        self._send_gas_preflight(identity_id, wei, addr, data)
        return wanted

    def _send_asset(self, identity_id: str, asset: str) -> str:
        wanted = (asset or "").strip()
        if not wanted:
            return USDC_ASSET
        if wanted != USDC_ASSET:
            self._log.record("wallet_send", identity_id, None, "cannot_send")
            raise CannotSend("unsupported asset", reason="unsupported_asset")
        return wanted

    def payment_ref(self) -> str:
        return self._payment_hash

    def describe(self, identity_id: str) -> WalletView:
        require_safe_token(identity_id, "identity id")
        return {
            "address": self._derived_address(),
            "chain": self.chain_name,
            "chain_label": self.chain_label,
            "chain_id": self.chain_id,
            "asset": USDC_ASSET,
            "scheme": "eip191",
        }

    def verify(
        self, identity_id: str, message: str, authorization: str
    ) -> WalletAuthorization:
        require_safe_token(identity_id, "identity id")
        expected = self._derived_address()
        scheme = _statement_scheme(message)
        try:
            recovered = Account.recover_message(
                _encode_statement(message),
                signature=_normalize_signature(authorization),
            )
            valid = to_checksum_address(recovered) == to_checksum_address(expected)
        except Exception:
            valid = False
        self._log.record("wallet_verify", identity_id, None, "ok" if valid else "error")
        return {"valid": valid, "address": expected, "scheme": scheme}

    def _send_once(self, identity_id: str, to: str, amount: str) -> None:
        addr, dest, units, data, wei = self._validate_send_once(identity_id, to, amount)
        pending = self._load_pending(identity_id)
        if pending and _same_intent(pending, dest, units, addr, self.chain_id):
            tx_hash = str(pending.get("hash") or "")
            if self._tx_confirmed(pending):
                self._remember_hash(tx_hash)
                self._clear_pending(identity_id)
                self._log.record("wallet_send", identity_id, None, _ok_hash(tx_hash))
                return
            self._finish_pending(identity_id, pending)
            self._remember_hash(tx_hash)
            return
        gas_price, gas_limit = self._send_gas_preflight(identity_id, wei, addr, data)
        nonce = _hex_int(
            self._rpc_request("eth_getTransactionCount", [addr, "pending"])
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
            signed = self._account().sign_transaction(tx)
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
        self._save_pending(identity_id, record)
        self._broadcast(identity_id, record)

    def _validate_send_once(
        self, identity_id: str, to: str, amount: str
    ) -> tuple[str, str, int, str, int]:
        addr = self._derived_address()
        wei = _hex_int(self._rpc_request("eth_getBalance", [addr, "latest"]))
        if wei == 0:
            self._log.record("wallet_send", identity_id, None, "no_gas")
            raise CannotSend("need ETH for gas", reason="no_gas")
        try:
            units = _usdc_units(amount)
        except CannotSend:
            self._log.record("wallet_send", identity_id, None, "cannot_send")
            raise
        held = _hex_int(
            self._rpc_request(
                "eth_call",
                [{"to": self.usdc, "data": _balance_of_data(addr)}, "latest"],
            )
        )
        if held < units:
            self._log.record("wallet_send", identity_id, None, "cannot_send")
            raise CannotSend("need USDC", reason="insufficient_asset")
        try:
            dest = to_checksum_address(to)
        except (ValueError, TypeError):
            self._log.record("wallet_send", identity_id, None, "cannot_send")
            raise CannotSend(
                "invalid destination", reason="invalid_destination"
            ) from None
        data = (
            "0x"
            + TRANSFER_SELECTOR.hex()
            + _pad_address(dest)
            + format(units, "x").zfill(64)
        )
        return addr, dest, units, data, wei

    def _send_gas_preflight(
        self, identity_id: str, wei: int, addr: str, data: str
    ) -> tuple[int, int]:
        gas_price = self._gas_price()
        gas_limit = self._estimate_gas(addr, data)
        if wei < gas_price * gas_limit:
            self._log.record("wallet_send", identity_id, None, "no_gas")
            raise CannotSend("need ETH for gas", reason="no_gas")
        return gas_price, gas_limit

    def _estimate_gas(self, addr: str, data: str) -> int:
        return _hex_int(
            self._rpc_request(
                "eth_estimateGas",
                [{"from": addr, "to": self.usdc, "data": data, "value": "0x0"}],
            )
        )

    def _gas_price(self) -> int:
        return _hex_int(self._rpc_request("eth_gasPrice", []))

    def _finish_pending(self, identity_id: str, pending: dict[str, object]) -> None:
        tx_hash = str(pending.get("hash") or "")
        if self._tx_confirmed(pending):
            self._remember_hash(tx_hash)
            self._clear_pending(identity_id)
            self._log.record("wallet_send", identity_id, None, _ok_hash(tx_hash))
            return
        raw = str(pending.get("raw") or "")
        if not raw.startswith("0x"):
            raise WalletError("rpc failed")
        self._broadcast(identity_id, pending)

    def _broadcast(
        self,
        identity_id: str,
        pending: dict[str, object],
    ) -> None:
        raw = str(pending.get("raw") or "")
        tx_hash = str(pending.get("hash") or "")
        try:
            result = self._rpc_request("eth_sendRawTransaction", [raw])
        except WalletError:
            if self._tx_confirmed(pending):
                self._remember_hash(tx_hash)
                self._log.record("wallet_send", identity_id, None, _ok_hash(tx_hash))
                return
            raise
        if not _same_hash(result, tx_hash):
            raise WalletError("rpc failed")
        self._remember_hash(tx_hash)
        self._log.record("wallet_send", identity_id, None, _ok_hash(tx_hash))
        if self._tx_confirmed(pending):
            self._clear_pending(identity_id)

    def _tx_confirmed(self, pending: dict[str, object]) -> bool:
        tx_hash = str(pending.get("hash") or "")
        if not tx_hash.startswith("0x") or len(tx_hash) != 66:
            return False
        try:
            found = self._rpc_request("eth_getTransactionByHash", [tx_hash])
            receipt = self._rpc_request("eth_getTransactionReceipt", [tx_hash])
        except WalletError:
            return False
        if not isinstance(found, dict) or not isinstance(receipt, dict):
            return False
        expected_input = (
            "0x"
            + TRANSFER_SELECTOR.hex()
            + _pad_address(str(pending.get("to") or ""))
            + format(_stored_int(pending.get("units")), "x").zfill(64)
        )
        return all(
            (
                _same_hash(found.get("hash"), tx_hash),
                _same_hash(receipt.get("transactionHash"), tx_hash),
                str(found.get("from") or "").lower()
                == str(pending.get("from") or "").lower(),
                str(found.get("to") or "").lower() == self.usdc.lower(),
                _rpc_int(found.get("nonce")) == _stored_int(pending.get("nonce")),
                _rpc_int(found.get("chainId")) == _stored_int(pending.get("chain_id")),
                str(found.get("input") or "").lower() == expected_input.lower(),
                _rpc_int(receipt.get("status")) == 1,
            )
        )

    def _pending_path(self, identity_id: str) -> Path | None:
        if self._root is None:
            return None
        return identity_home(self._root, identity_id) / "wallet" / "pending-send.json"

    def _load_pending(self, identity_id: str) -> dict[str, object] | None:
        path = self._pending_path(identity_id)
        if path is None or not path.is_file():
            return None
        try:
            data = load_json_file(path)
        except (OSError, json.JSONDecodeError) as exc:
            raise WalletError("rpc failed") from exc
        if not isinstance(data, dict):
            raise WalletError("rpc failed")
        return data

    def _save_pending(self, identity_id: str, record: dict[str, object]) -> None:
        path = self._pending_path(identity_id)
        if path is None:
            return
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = json.dumps(record, indent=2, sort_keys=True) + "\n"
        try:
            atomic_write_text(path, payload)
        except OSError as exc:
            raise WalletError("rpc failed") from exc

    def _clear_pending(self, identity_id: str) -> None:
        path = self._pending_path(identity_id)
        if path is None:
            return
        try:
            if path.is_symlink():
                path.unlink()
                return
            if path.is_file():
                path.unlink()
        except OSError as exc:
            raise WalletError("rpc failed") from exc

    def _remember_hash(self, tx_hash: str) -> None:
        text = (tx_hash or "").strip()
        if text.startswith("0x") and len(text) == 66:
            body = text[2:]
            if all(ch in "0123456789abcdefABCDEF" for ch in body):
                self._payment_hash = text
                return
        self._payment_hash = ""

    def _derived_address(self) -> str:
        return to_checksum_address(self._account().address)

    def _account(self):
        return _account_from_key(self._require_key())

    def _require_key(self) -> str:
        if not self._key_hex:
            raise WalletError("missing key")
        return self._key_hex

    def _rpc_urls(self) -> list[str]:
        extras = [] if self._rpc_override else list(self.fallback_rpcs)
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


def _typed_statement(message: str) -> dict[str, object] | None:
    if not message.lstrip().startswith("{"):
        return None
    try:
        data = json.loads(message)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if not {"domain", "types", "message"} <= data.keys():
        return None
    statement: dict[str, object] = {
        "domain": data["domain"],
        "types": data["types"],
        "message": data["message"],
    }
    if "primaryType" in data:
        statement["primaryType"] = data["primaryType"]
    return statement


def _statement_scheme(message: str) -> str:
    return "eip712" if _typed_statement(message) is not None else "eip191"


def _encode_statement(message: str) -> SignableMessage:
    typed = _typed_statement(message)
    if typed is None:
        return encode_defunct(text=message)
    try:
        return encode_typed_data(full_message=typed)
    except Exception as exc:
        # A typed-shaped file must not silently become a personal signature.
        raise CannotAuthorize() from exc


def _normalize_key(key_hex: str) -> str:
    key = key_hex.removeprefix(UTF8_BOM).strip()
    if not key:
        raise WalletError("missing key")
    return key


def _normalize_signature(value: str) -> str:
    text = (value or "").strip()
    if not text:
        raise WalletError("missing authorization")
    return text


def _pad_address(address: str) -> str:
    hex_part = address.lower().removeprefix("0x")
    return hex_part.rjust(64, "0")


def _balance_of_data(address: str) -> str:
    return "0x" + BALANCE_OF_SELECTOR.hex() + _pad_address(address)


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
        raise CannotSend("invalid amount", reason="invalid_amount") from exc
    if not value.is_finite() or value <= 0:
        raise CannotSend("invalid amount", reason="invalid_amount")
    scaled = value * (Decimal(10) ** USDC_DECIMALS)
    integral = scaled.to_integral_value()
    if scaled != integral:
        raise CannotSend("invalid amount", reason="invalid_amount")
    return int(integral)


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


def _same_hash(value: object, expected: str) -> bool:
    text = str(value or "").strip()
    return _ok_hash(text) != "ok" and text.lower() == expected.lower()


def _rpc_int(value: object) -> int:
    try:
        return int(str(value), 16)
    except (TypeError, ValueError):
        return -1


def _stored_int(value: object) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return -1


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
