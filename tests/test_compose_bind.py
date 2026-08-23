"""compose takes an identity directory and an optional bind callable."""

from __future__ import annotations

from pathlib import Path

from agentself.client import Client
from agentself.compose import compose
from agentself.local import default_identity_dir


def _vault(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    monkeypatch.setenv("AGENTSELF_IDENTITY_DIR", str(vault))
    monkeypatch.setattr("agentself.compose.default_identity_dir", lambda: vault)
    monkeypatch.setattr("agentself.local.default_identity_dir", lambda: vault)
    return vault


def test_default_identity_dir_uses_home_when_identity_env_unset(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.delenv("AGENTSELF_IDENTITY_DIR", raising=False)
    monkeypatch.setenv("AGENTSELF_VAULT_ROOT", str(tmp_path / "legacy-vault"))
    monkeypatch.setattr("agentself.local.Path.home", staticmethod(lambda: home))
    assert default_identity_dir() == home / ".agentself"


def test_compose_bind_kwarg_builds_client(tmp_path, monkeypatch):
    vault = _vault(tmp_path, monkeypatch)

    def dummy():
        raise AssertionError("bind not called during compose")

    client = compose(bind=dummy)
    assert isinstance(client, Client)
    assert client._bind is dummy
    assert vault.is_dir()
    assert vault.resolve() != Path.home() / ".agentself"
