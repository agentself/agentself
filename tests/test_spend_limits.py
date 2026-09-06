"""Identity-local spend limits: inspect, enforce, substitution."""

from __future__ import annotations

import json
import threading
from decimal import Decimal
from pathlib import Path

import pytest

from agentself.cli.app import main
from agentself.internal.custody.errors import CannotSend, Refused
from agentself.internal.spend import (
    LimitError,
    canonical_amount,
    destination_allowed,
    parse_limit,
    remaining_of,
)

from tests.fiat_wallet import FiatWalletAccess
from tests.support import (
    apply_cli_env,
    build_app,
    cli_env,
    init_identity,
    value_file,
)


class DrainingFiat(FiatWalletAccess):
    """Fiat double that subtracts on send so reserve races are observable."""

    def send(self, identity_id, to, amount, asset, details=""):
        wanted = self.validate_send(identity_id, to, amount, asset, details)
        held = Decimal(self.holdings[wanted])
        self.holdings[wanted] = format(held - Decimal(str(amount).strip()), "f")
        self.sends.append((to, amount, wanted, details))
        return wanted


def _limit_file(folder: Path, payload: dict, name: str = "limit.json") -> str:
    return value_file(folder, json.dumps(payload) + "\n", name)


def test_parse_limit_and_destination_matching():
    parsed = parse_limit(
        {
            "format_version": 1,
            "max": "10.00",
            "reserve": "0.010",
            "to": ["acct.merchant", "0xAb"],
            "extra": "ignored",
        }
    )
    assert canonical_amount(parsed.max) == "10"
    assert canonical_amount(parsed.reserve) == "0.01"
    assert destination_allowed("acct.merchant", parsed.to)
    assert destination_allowed("0xab", parsed.to)
    assert not destination_allowed("other", parsed.to)
    assert remaining_of(Decimal("100"), Decimal("90")) == Decimal("10")
    assert remaining_of(Decimal("5"), Decimal("10")) == Decimal("0")
    with pytest.raises(LimitError):
        parse_limit({})
    with pytest.raises(LimitError):
        parse_limit({"format_version": 1, "max": "-1"})


def test_missing_limit_leaves_send_unlimited(vault, monkeypatch):
    app = build_app(vault, wallet_backend="synthetic")
    init_identity(app, monkeypatch)
    assert app.client.wallet_limit() == {"limit": False}
    assert app.client.wallet_send("dest", "1") == {"asset": "NOTE"}


def test_max_and_unlisted_asset_and_test_does_not_send(vault, monkeypatch):
    app = build_app(vault, wallet_backend="synthetic")
    init_identity(app, monkeypatch)
    app.client.wallet_limit_set(
        json.dumps({"format_version": 1, "assets": {"NOTE": {"max": "2"}}})
    )
    with pytest.raises(CannotSend) as caught:
        app.client.wallet_send("dest", "3")
    assert caught.value.reason == "spend_max"
    with pytest.raises(CannotSend) as asset:
        app.client.wallet_send("dest", "1", "LITKEY")
    assert asset.value.reason == "spend_asset"
    assert "ETH" not in str(caught.value) + str(asset.value)
    assert "USDC" not in str(caught.value) + str(asset.value)
    planned = app.client.wallet_send("dest", "1", test=True)
    assert planned == {"asset": "NOTE"}
    calls = app.wallets.instances[-1].calls
    assert ("validate_send",) in calls
    assert ("send",) not in calls


def test_fiat_reserve_destination_and_remaining(vault, monkeypatch):
    fiat = DrainingFiat()
    app = build_app(vault, wallet_backend="synthetic")
    init_identity(app, monkeypatch)
    monkeypatch.setattr(app.wallets.inner, "for_binding", lambda binding: fiat)
    app.client.wallet_limit_set(
        json.dumps(
            {
                "format_version": 1,
                "max": "20",
                "reserve": "90",
                "to": ["merchant"],
            }
        )
    )
    view = app.client.wallet_limit()
    assert view["limit"] is True
    assert view["remaining"] == "10"
    assert view["balance"] == "100"
    blob = json.dumps(view)
    assert "ETH" not in blob
    assert "USDC" not in blob
    with pytest.raises(CannotSend) as dest:
        app.client.wallet_send("other", "5")
    assert dest.value.reason == "spend_destination"
    assert app.client.wallet_send("merchant", "10") == {"asset": "USD"}
    with pytest.raises(CannotSend) as reserved:
        app.client.wallet_send("merchant", "1")
    assert reserved.value.reason == "spend_reserve"
    assert reserved.value.remaining == "0"


def test_replace_limit_needs_force(vault, monkeypatch):
    app = build_app(vault, wallet_backend="synthetic")
    init_identity(app, monkeypatch)
    app.client.wallet_limit_set(json.dumps({"format_version": 1, "max": "1"}))
    with pytest.raises(Refused):
        app.client.wallet_limit_set(json.dumps({"format_version": 1, "max": "2"}))
    updated = app.client.wallet_limit_set(
        json.dumps({"format_version": 1, "max": "2"}), force=True
    )
    assert updated["max"] == "2"


def test_malformed_limit_fails_closed(vault, monkeypatch):
    app = build_app(vault, wallet_backend="synthetic")
    init_identity(app, monkeypatch)
    from agentself.internal.files import identity_home
    from agentself.internal.spend import LIMIT_NAME

    path = identity_home(vault, "P") / LIMIT_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(CannotSend) as caught:
        app.client.wallet_send("dest", "1")
    assert caught.value.reason == "spend_limit"


def test_cli_limit_inspect_force_and_send_next(tmp_path, monkeypatch, capsys):
    env = cli_env(tmp_path / "vault")
    apply_cli_env(monkeypatch, env)
    fiat = DrainingFiat()
    monkeypatch.setattr(
        "agentself.compose.WalletAccessFactory.for_binding",
        lambda self, binding: fiat,
    )
    assert main(["--json", "init"]) == 0
    capsys.readouterr()
    assert main(["--json", "wallet", "limit"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["limit"] is False
    limit = _limit_file(tmp_path, {"format_version": 1, "max": "5", "reserve": "90"})
    assert main(["--json", "wallet", "limit", "--file", limit]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["limit"] is True
    assert data["remaining"] == "10"
    assert main(["--json", "wallet", "limit", "--file", limit]) == 2
    refused = json.loads(capsys.readouterr().out)
    assert refused["reason"] == "file exists"
    assert "--force" in refused["next"]
    assert main(["--json", "wallet", "limit", "--file", limit, "--force"]) == 0
    capsys.readouterr()
    code = main(["--json", "wallet", "send", "merchant", "6", "--test"])
    captured = capsys.readouterr()
    assert code == 2, captured.out + captured.err
    payload = json.loads(captured.out)
    assert payload["reason"] == "spend_max"
    assert payload["next"] == "agentself wallet limit"
    assert payload["_next"]["command"] == payload["next"]
    assert "ETH" not in captured.out
    assert "USDC" not in captured.out
    assert fiat.sends == []


def test_concurrent_reserve_sends_serialize(vault, monkeypatch):
    fiat = DrainingFiat()
    app = build_app(vault, wallet_backend="synthetic")
    init_identity(app, monkeypatch)
    monkeypatch.setattr(app.wallets.inner, "for_binding", lambda binding: fiat)
    app.client.wallet_limit_set(json.dumps({"format_version": 1, "reserve": "90"}))
    results: list[str] = []

    def _send() -> None:
        try:
            app.client.wallet_send("merchant", "10")
            results.append("ok")
        except CannotSend as exc:
            results.append(exc.reason)

    first = threading.Thread(target=_send)
    second = threading.Thread(target=_send)
    first.start()
    second.start()
    first.join()
    second.join()
    assert results.count("ok") == 1
    assert results.count("spend_reserve") == 1
