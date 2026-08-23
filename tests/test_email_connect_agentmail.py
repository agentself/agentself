"""agentself email connect for AgentMail: discover or create, persist live address."""

from __future__ import annotations

import json

from agentself.backends.email.agentmail import AgentMailMailboxAccess
from agentself.cli.app import main

from tests.support import apply_cli_env, cli_env, run_cli, value_file
from tests.test_agentmail_mailbox import (
    API,
    INBOXES,
    ISSUED,
    OURS,
    PRINCIPAL,
    TAKEN,
    Http,
)

TOKEN = "am_test_token_do_not_leak"


def _patch_agentmail(monkeypatch, http: Http) -> None:
    def _make(self, binding):
        return AgentMailMailboxAccess(
            self._root,
            self._log,
            domain=self._domain,
            poster=http.poster,
            getter=http.getter,
        )

    monkeypatch.setattr(
        "agentself.compose.MailboxAccessFactory.for_binding",
        _make,
    )


def test_agentmail_connect_without_token_points_at_secret(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init"], env)
    assert start.returncode == 0, start.stderr
    proc = run_cli(["email", "connect"], env)
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "input required" in proc.stderr
    assert "credential" in proc.stderr
    assert "need --domain" not in proc.stderr
    js = run_cli(["--json", "email", "connect"], env)
    assert js.returncode == 3, js.stdout + js.stderr
    data = json.loads(js.stdout or js.stderr)
    assert data["ok"] is False
    assert data["error"] == "missing"
    assert data["status"] == "input_required"
    assert data["option"]["name"] == "credential"
    assert "console.agentmail.to" in data["option"]["help"]
    assert "AGENTSELF_AGENTMAIL_API_KEY" in data["option"]["help"]
    assert "init --force --email imap" in data["option"]["help"]
    assert data["next"].startswith("agentself --json email connect --continue --state ")
    assert "--result-file PATH" in data["next"]


def test_agentmail_connect_discovers_unique_inbox(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init"], env)
    assert start.returncode == 0, start.stderr
    sealed = run_cli(
        ["secret", "create", "email.credential", "--file", value_file(tmp_path, TOKEN)],
        env,
    )
    assert sealed.returncode == 0, sealed.stderr
    apply_cli_env(monkeypatch, env)
    http = Http()
    http.on_get(
        INBOXES,
        200,
        {"inboxes": [{"inbox_id": "inb_who", "email": OURS}]},
    )
    _patch_agentmail(monkeypatch, http)
    code = main(["email", "connect"])
    captured = capsys.readouterr()
    assert code == 0, captured.out + captured.err
    assert captured.out == f"email: {OURS}\n"
    assert TOKEN not in captured.out + captured.err
    assert f"{PRINCIPAL}@agentmail.to" not in captured.out
    assert http.posts == []
    shown = main(["email", "show"])
    shown_out = capsys.readouterr()
    assert shown == 0, shown_out.out + shown_out.err
    assert shown_out.out == f"{OURS}\n"
    names = run_cli(["secret", "list"], env)
    assert "email.address" in names.stdout.splitlines()
    got = run_cli(["secret", "get", "email.address"], env)
    assert got.stdout.strip() == OURS
    assert TOKEN not in got.stdout + got.stderr
    again = main(["email", "connect"])
    again_out = capsys.readouterr()
    assert again == 0, again_out.out + again_out.err
    assert again_out.out == f"email: {OURS}\n"
    assert len(http.gets) == 3
    assert http.posts == []


def test_agentmail_connect_creates_when_empty(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init"], env)
    assert start.returncode == 0, start.stderr
    sealed = run_cli(
        ["secret", "create", "email.credential", "--file", value_file(tmp_path, TOKEN)],
        env,
    )
    assert sealed.returncode == 0, sealed.stderr
    apply_cli_env(monkeypatch, env)
    http = Http()
    http.on_get(INBOXES, 200, {"inboxes": []})
    http.post_result(200, {"inbox_id": "inb_new", "email": ISSUED})
    _patch_agentmail(monkeypatch, http)
    code = main(["--json", "email", "connect"])
    captured = capsys.readouterr()
    assert code == 0, captured.out + captured.err
    data = json.loads(captured.out)
    assert data["ok"] is True
    assert data["address"] == ISSUED
    assert data["status"] == "connected"
    assert TOKEN not in captured.out + captured.err
    assert len(http.posts) == 1
    url, headers, payload = http.posts[0]
    assert url == INBOXES
    assert API in url
    body = json.loads(payload.decode("utf-8"))
    assert "username" not in body
    assert "domain" not in body
    assert body["client_id"].startswith("agentself-")
    got = run_cli(["secret", "get", "email.address"], env)
    assert got.stdout.strip() == ISSUED
    assert "Authorization" in headers
    assert TOKEN not in captured.out + captured.err


def test_agentmail_connect_many_inboxes_need_address(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init"], env)
    assert start.returncode == 0, start.stderr
    sealed = run_cli(
        ["secret", "create", "email.credential", "--file", value_file(tmp_path, TOKEN)],
        env,
    )
    assert sealed.returncode == 0, sealed.stderr
    apply_cli_env(monkeypatch, env)
    http = Http()
    http.on_get(
        INBOXES,
        200,
        {
            "inboxes": [
                {"inbox_id": "a", "email": TAKEN},
                {"inbox_id": "b", "email": OURS},
            ]
        },
    )
    _patch_agentmail(monkeypatch, http)
    code = main(["email", "connect"])
    captured = capsys.readouterr()
    assert code == 3, captured.out + captured.err
    assert "input required" in captured.err
    assert "address" in captured.err
    assert TAKEN not in captured.out + captured.err
    assert TOKEN not in captured.out + captured.err
    assert http.posts == []
    js = main(["--json", "email", "connect"])
    js_out = capsys.readouterr()
    assert js == 3, js_out.out + js_out.err
    data = json.loads(js_out.out or js_out.err)
    assert data["ok"] is False
    assert data["error"] == "missing"
    assert data["status"] == "input_required"
    assert data["option"]["name"] == "address"
    assert data["next"].startswith("agentself --json email connect --continue --state ")
    assert "--result-file PATH" in data["next"]


def test_agentmail_connect_rpc_is_error(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init"], env)
    assert start.returncode == 0, start.stderr
    sealed = run_cli(
        ["secret", "create", "email.credential", "--file", value_file(tmp_path, TOKEN)],
        env,
    )
    assert sealed.returncode == 0, sealed.stderr
    apply_cli_env(monkeypatch, env)
    http = Http()
    http.get_raises(INBOXES, TimeoutError("timeout"))
    _patch_agentmail(monkeypatch, http)
    code = main(["email", "connect"])
    captured = capsys.readouterr()
    assert code == 1, captured.out + captured.err
    assert captured.err == "error: rpc\nnext: agentself backends email\n"
    assert TOKEN not in captured.out + captured.err


def test_agentmail_connect_unauthorized_is_invalid_credentials(
    tmp_path, monkeypatch, capsys
) -> None:
    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init"], env)
    assert start.returncode == 0, start.stderr
    apply_cli_env(monkeypatch, env)
    first = main(["--json", "email", "connect"])
    captured = capsys.readouterr()
    assert first == 3, captured.out + captured.err
    state = json.loads(captured.out)["state"]
    http = Http()
    http.on_get(INBOXES, 401, {"error": "nope"})
    _patch_agentmail(monkeypatch, http)
    cred = value_file(tmp_path, TOKEN)
    code = main(
        [
            "--json",
            "email",
            "connect",
            "--continue",
            "--state",
            state,
            "--result-file",
            cred,
        ]
    )
    captured = capsys.readouterr()
    assert code == 1, captured.out + captured.err
    data = json.loads(captured.out)
    assert data["ok"] is False
    assert data["error"] == "error"
    assert data["reason"] == "invalid credentials"
    assert data["next"] == "agentself --json email connect"
    assert TOKEN not in captured.out + captured.err
    exists = run_cli(["--json", "secret", "exists", "email.credential"], env)
    assert exists.returncode == 3


def test_agentmail_connect_unknown_env_address_persists_nothing(
    tmp_path, monkeypatch, capsys
) -> None:
    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init"], env)
    assert start.returncode == 0, start.stderr
    apply_cli_env(monkeypatch, env)
    monkeypatch.setenv("AGENTSELF_EMAIL_ADDRESS", OURS)
    monkeypatch.setenv("AGENTSELF_EMAIL_CREDENTIAL", TOKEN)
    http = Http()
    http.on_get(
        INBOXES,
        200,
        {"inboxes": [{"inbox_id": "inb_other", "email": TAKEN}]},
    )
    _patch_agentmail(monkeypatch, http)

    code = main(["--json", "email", "connect"])
    captured = capsys.readouterr()
    assert code == 1
    data = json.loads(captured.out)
    assert data["ok"] is False
    assert data["reason"] == "mailbox_error"
    assert TOKEN not in captured.out + captured.err
    assert http.posts == []
    for name in ("email.address", "email.credential"):
        exists = run_cli(["--json", "secret", "exists", name], env)
        assert exists.returncode == 3
        assert json.loads(exists.stdout)["exists"] is False
