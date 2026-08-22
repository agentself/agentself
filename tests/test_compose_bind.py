"""compose takes an identity directory and an optional bind callable."""

from __future__ import annotations

from pathlib import Path

from agentself.client import Gateway
from agentself.compose import compose
from agentself.local import default_vault


def _vault(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    monkeypatch.setenv("AGENTSELF_VAULT_ROOT", str(vault))
    monkeypatch.setattr("agentself.compose.default_vault", lambda: vault)
    monkeypatch.setattr("agentself.local.default_vault", lambda: vault)
    return vault


def test_default_vault_uses_home_when_unset(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.delenv("AGENTSELF_VAULT_ROOT", raising=False)
    monkeypatch.setattr("agentself.local.Path.home", staticmethod(lambda: home))
    assert default_vault() == home / ".agentself"


def test_compose_bind_kwarg_builds_gateway(tmp_path, monkeypatch):
    vault = _vault(tmp_path, monkeypatch)

    def dummy():
        raise AssertionError("bind not called during compose")

    gateway = compose(bind=dummy)
    assert isinstance(gateway, Gateway)
    assert gateway._bind is dummy
    assert vault.is_dir()
    assert vault.resolve() != Path.home() / ".agentself"


def test_compose_no_args_uses_default_vault(tmp_path, monkeypatch):
    vault = _vault(tmp_path, monkeypatch)
    gateway = compose()
    assert isinstance(gateway, Gateway)
    assert vault.is_dir()


def test_compose_vault_only_stays(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setenv("AGENTSELF_VAULT_ROOT", str(vault))
    gateway = compose(vault)
    assert isinstance(gateway, Gateway)
    assert vault.is_dir()
