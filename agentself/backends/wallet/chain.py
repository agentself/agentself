from __future__ import annotations

import json
from dataclasses import dataclass
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
APPROVE_SELECTOR = keccak(text="approve(address,uint256)")[:4]
DECIMALS_SELECTOR = keccak(text="decimals()")[:4]
USDC_ASSET = "USDC"
GAS_ASSET = "ETH"


@dataclass(frozen=True)
class _PreparedSend:
    addr: str
    dest: str
    tx_to: str
    units: int
    asset: str
    data: str
    wei: int


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
        self._decimals = {self.usdc.lower(): USDC_DECIMALS}

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

    def balance(self, identity_id: str, asset: str = "") -> WalletBalance:
        require_safe_token(identity_id, "identity id")
        addr = self._derived_address()
        wei = _hex_int(self._rpc_request("eth_getBalance", [addr, "latest"]))
        wanted = (asset or "").strip()
        if wanted == GAS_ASSET:
            amount = _format_eth(wei)
            self._log.record("wallet_balance", identity_id, None, "ok")
            return {
                "asset": GAS_ASSET,
                "chain": self.chain_name,
                "chain_id": str(self.chain_id),
                "address": addr,
                "amount": amount,
                "raw": str(wei),
                "gas_asset": GAS_ASSET,
                "gas_raw": str(wei),
                "gas_amount": amount,
            }
        try:
            name, token = self._asset_token(wanted)
            decimals = self._token_decimals(token)
        except CannotSend:
            self._log.record("wallet_balance", identity_id, None, "cannot_send")
            raise
        raw = _hex_int(
            self._rpc_request(
                "eth_call",
                [{"to": token, "data": _balance_of_data(addr)}, "latest"],
            )
        )
        amount = _format_units(raw, decimals)
        self._log.record("wallet_balance", identity_id, None, "ok")
        result: WalletBalance = {
            "asset": name,
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

    def send(
        self,
        identity_id: str,
        to: str,
        amount: str,
        asset: str,
        details: str = "",
    ) -> str:
        require_safe_token(identity_id, "identity id")
        self._require_key()
        plan = self._prepare_send(identity_id, to, amount, asset, details)
        if self._root is None:
            self._send_once(identity_id, plan)
            return plan.asset
        try:
            with exclusive(self._root):
                self._send_once(identity_id, plan)
        except IdentityBusy as exc:
            raise WalletError("identity directory busy") from exc
        return plan.asset

    def validate_send(
        self,
        identity_id: str,
        to: str,
        amount: str,
        asset: str,
        details: str = "",
    ) -> str:
        require_safe_token(identity_id, "identity id")
        self._require_key()
        plan = self._prepare_send(identity_id, to, amount, asset, details)
        self._send_gas_preflight(identity_id, plan)
        return plan.asset

    def _asset_token(self, asset: str) -> tuple[str, str]:
        wanted = (asset or "").strip()
        if not wanted or wanted == USDC_ASSET:
            return USDC_ASSET, self.usdc
        if wanted == GAS_ASSET:
            raise CannotSend("unsupported asset", reason="unsupported_asset")
        try:
            token = to_checksum_address(wanted)
        except (ValueError, TypeError):
            raise CannotSend("unsupported asset", reason="unsupported_asset") from None
        if token.lower() == self.usdc.lower():
            return USDC_ASSET, self.usdc
        return token, token

    def _token_decimals(self, token: str) -> int:
        key = token.lower()
        cached = self._decimals.get(key)
        if cached is not None:
            return cached
        raw = _hex_int(
            self._rpc_request(
                "eth_call",
                [{"to": token, "data": _decimals_data()}, "latest"],
            )
        )
        if raw < 0 or raw > 255:
            raise CannotSend("unsupported asset", reason="unsupported_asset")
        self._decimals[key] = raw
        return raw

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

    def _send_once(self, identity_id: str, plan: _PreparedSend) -> None:
        pending = self._load_pending(identity_id)
        if pending and _same_intent(pending, plan, self.chain_id):
            tx_hash = str(pending.get("hash") or "")
            if self._tx_confirmed(pending):
                self._remember_hash(tx_hash)
                self._clear_pending(identity_id)
                self._log.record("wallet_send", identity_id, None, _ok_hash(tx_hash))
                return
            self._finish_pending(identity_id, pending)
            self._remember_hash(tx_hash)
            return
        gas_price, gas_limit = self._send_gas_preflight(identity_id, plan)
        nonce = _hex_int(
            self._rpc_request("eth_getTransactionCount", [plan.addr, "pending"])
        )
        tx = {
            "to": plan.tx_to,
            "value": 0,
            "gas": gas_limit,
            "gasPrice": gas_price,
            "nonce": nonce,
            "data": plan.data,
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
            "from": plan.addr,
            "to": plan.dest,
            "tx_to": plan.tx_to,
            "input": plan.data,
            "units": plan.units,
            "asset": plan.asset,
            "nonce": nonce,
            "hash": tx_hash,
            "raw": raw_hex,
        }
        self._save_pending(identity_id, record)
        self._broadcast(identity_id, record)

    def _prepare_send(
        self,
        identity_id: str,
        to: str,
        amount: str,
        asset: str,
        details: str,
    ) -> _PreparedSend:
        try:
            name, token = self._asset_token(asset)
        except CannotSend:
            self._log.record("wallet_send", identity_id, None, "cannot_send")
            raise
        addr = self._derived_address()
        wei = _hex_int(self._rpc_request("eth_getBalance", [addr, "latest"]))
        if wei == 0:
            self._log.record("wallet_send", identity_id, None, "no_gas")
            raise CannotSend("need ETH for gas", reason="no_gas")
        try:
            dest = to_checksum_address(to)
        except (ValueError, TypeError):
            self._log.record("wallet_send", identity_id, None, "cannot_send")
            raise CannotSend(
                "invalid destination", reason="invalid_destination"
            ) from None
        try:
            units = _token_units(amount, self._token_decimals(token))
            kind, call = _send_details(details)
        except CannotSend:
            self._log.record("wallet_send", identity_id, None, "cannot_send")
            raise
        if kind == "allow":
            tx_to = token
            data = _approve_data(dest, units)
        elif kind == "call":
            tx_to = dest
            data = call
            self._require_held(identity_id, addr, token, units)
        else:
            tx_to = token
            data = _transfer_data(dest, units)
            self._require_held(identity_id, addr, token, units)
        return _PreparedSend(addr, dest, tx_to, units, name, data, wei)

    def _require_held(
        self, identity_id: str, addr: str, token: str, units: int
    ) -> None:
        held = _hex_int(
            self._rpc_request(
                "eth_call",
                [{"to": token, "data": _balance_of_data(addr)}, "latest"],
            )
        )
        if held < units:
            self._log.record("wallet_send", identity_id, None, "cannot_send")
            raise CannotSend("need funds", reason="insufficient_asset")

    def _send_gas_preflight(
        self, identity_id: str, plan: _PreparedSend
    ) -> tuple[int, int]:
        gas_price = self._gas_price()
        gas_limit = self._estimate_gas(plan.addr, plan.tx_to, plan.data)
        if plan.wei < gas_price * gas_limit:
            self._log.record("wallet_send", identity_id, None, "no_gas")
            raise CannotSend("need ETH for gas", reason="no_gas")
        return gas_price, gas_limit

    def _estimate_gas(self, addr: str, tx_to: str, data: str) -> int:
        return _hex_int(
            self._rpc_request(
                "eth_estimateGas",
                [{"from": addr, "to": tx_to, "data": data, "value": "0x0"}],
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
        expected_to = str(pending.get("tx_to") or self.usdc)
        expected_input = str(pending.get("input") or "")
        if not expected_input:
            expected_input = _transfer_data(
                str(pending.get("to") or ""), _stored_int(pending.get("units"))
            )
        return all(
            (
                _same_hash(found.get("hash"), tx_hash),
                _same_hash(receipt.get("transactionHash"), tx_hash),
                str(found.get("from") or "").lower()
                == str(pending.get("from") or "").lower(),
                str(found.get("to") or "").lower() == expected_to.lower(),
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


def _decimals_data() -> str:
    return "0x" + DECIMALS_SELECTOR.hex()


def _transfer_data(dest: str, units: int) -> str:
    return (
        "0x"
        + TRANSFER_SELECTOR.hex()
        + _pad_address(dest)
        + format(units, "x").zfill(64)
    )


def _approve_data(spender: str, units: int) -> str:
    return (
        "0x"
        + APPROVE_SELECTOR.hex()
        + _pad_address(spender)
        + format(units, "x").zfill(64)
    )


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


def _token_units(amount: str, decimals: int) -> int:
    try:
        value = Decimal(str(amount).strip())
    except (InvalidOperation, ValueError, ArithmeticError) as exc:
        raise CannotSend("invalid amount", reason="invalid_amount") from exc
    if not value.is_finite() or value <= 0:
        raise CannotSend("invalid amount", reason="invalid_amount")
    scaled = value * (Decimal(10) ** decimals)
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
    pending: dict[str, object], plan: _PreparedSend, chain_id: int
) -> bool:
    try:
        pending_units = int(str(pending.get("units")))
        pending_chain = int(str(pending.get("chain_id")))
    except (TypeError, ValueError):
        return False
    pending_input = str(pending.get("input") or "")
    if pending_input:
        input_ok = pending_input.lower() == plan.data.lower()
    else:
        input_ok = plan.data.lower() == _transfer_data(plan.dest, plan.units).lower()
    pending_tx_to = str(pending.get("tx_to") or "")
    tx_ok = pending_tx_to.lower() == plan.tx_to.lower() if pending_tx_to else True
    return (
        str(pending.get("to") or "") == plan.dest
        and pending_units == plan.units
        and str(pending.get("asset") or "") == plan.asset
        and str(pending.get("from") or "") == plan.addr
        and pending_chain == chain_id
        and input_ok
        and tx_ok
    )


def _send_details(details: str) -> tuple[str, str]:
    text = (details or "").strip()
    if not text:
        return "transfer", ""
    if _hex_calldata(text):
        return "call", hex_0x(text[2:])
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        raise CannotSend("unsupported details", reason="unsupported_details") from None
    if not isinstance(payload, dict):
        raise CannotSend("unsupported details", reason="unsupported_details")
    allow = payload.get("allow")
    signature = payload.get("signature")
    args = payload.get("args")
    if allow is True and signature is None and args is None:
        return "allow", ""
    if allow is True:
        raise CannotSend("unsupported details", reason="unsupported_details")
    if isinstance(signature, str) and signature.strip():
        if not isinstance(args, list):
            raise CannotSend("unsupported details", reason="unsupported_details")
        return "call", _encode_signature(signature.strip(), args)
    raise CannotSend("unsupported details", reason="unsupported_details")


def _hex_calldata(text: str) -> bool:
    raw = text.strip()
    if not raw.startswith(("0x", "0X")) or len(raw) < 2 or len(raw) % 2:
        return False
    return all(ch in "0123456789abcdefABCDEF" for ch in raw[2:])


def _encode_signature(signature: str, args: list[object]) -> str:
    from eth_abi import encode

    _name, types = _abi_signature_parts(signature)
    del _name
    if len(types) != len(args):
        raise CannotSend("unsupported details", reason="unsupported_details")
    try:
        encoded = encode(
            types, [_abi_arg(typ, value) for typ, value in zip(types, args)]
        )
    except (CannotSend, ValueError, TypeError):
        raise CannotSend("unsupported details", reason="unsupported_details") from None
    except Exception:
        raise CannotSend("unsupported details", reason="unsupported_details") from None
    return hex_0x(keccak(text=signature)[:4].hex() + encoded.hex())


def _abi_signature_parts(signature: str) -> tuple[str, list[str]]:
    text = signature.strip()
    open_at = text.find("(")
    if open_at <= 0 or not text.endswith(")"):
        raise CannotSend("unsupported details", reason="unsupported_details")
    name = text[:open_at].strip()
    inner = text[open_at + 1 : -1].strip()
    if not name:
        raise CannotSend("unsupported details", reason="unsupported_details")
    if not inner:
        return name, []
    return name, _abi_types(inner)


def _abi_types(inner: str) -> list[str]:
    types: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(inner):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            types.append(inner[start:index].strip())
            start = index + 1
    types.append(inner[start:].strip())
    if depth != 0 or any(not item for item in types):
        raise CannotSend("unsupported details", reason="unsupported_details")
    return types


def _abi_arg(typ: str, value: object) -> object:
    wanted = typ.strip()
    if wanted == "address":
        return to_checksum_address(str(value))
    if wanted == "bool":
        if isinstance(value, bool):
            return value
        raise CannotSend("unsupported details", reason="unsupported_details")
    if wanted.startswith("uint") or wanted.startswith("int"):
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise CannotSend("unsupported details", reason="unsupported_details")
        text = str(value).strip()
        if text.startswith(("0x", "0X", "-0x", "-0X")):
            return int(text, 16)
        return int(text)
    if wanted == "string":
        return str(value)
    if wanted == "bytes" or wanted.startswith("bytes"):
        text = str(value).strip()
        if not text.startswith(("0x", "0X")) or len(text) % 2:
            raise CannotSend("unsupported details", reason="unsupported_details")
        return bytes.fromhex(text[2:])
    if wanted.startswith("(") and wanted.endswith(")") and isinstance(value, list):
        inner = _abi_types(wanted[1:-1])
        if len(inner) != len(value):
            raise CannotSend("unsupported details", reason="unsupported_details")
        return tuple(_abi_arg(part, item) for part, item in zip(inner, value))
    raise CannotSend("unsupported details", reason="unsupported_details")
