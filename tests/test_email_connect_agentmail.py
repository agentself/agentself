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
    SIGN_UP,
    TAKEN,
    VERIFY,
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
    got = run_cli(["secret", "get", "email.address", "--print"], env)
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
    got = run_cli(["secret", "get", "email.address", "--print"], env)
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
    method_state = json.loads(captured.out)["state"]
    method = value_file(tmp_path, "existing_credential", "method.txt")
    choose = main(
        [
            "--json",
            "email",
            "connect",
            "--continue",
            "--state",
            method_state,
            "--result-file",
            method,
        ]
    )
    captured = capsys.readouterr()
    assert choose == 3, captured.out + captured.err
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


def test_agentmail_authorized_signup_json_continuation_persists_generated_key(
    tmp_path, monkeypatch, capsys
) -> None:
    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["--json", "init"], env)
    assert start.returncode == 0, start.stderr
    apply_cli_env(monkeypatch, env)
    generated = "am_generated_api_key_do_not_leak"
    inbox_id = "inb_signed_up"
    http = Http()
    http.on_post(
        SIGN_UP,
        200,
        {
            "organization_id": "org_signed_up",
            "inbox_id": inbox_id,
            "api_key": generated,
        },
    )
    http.on_post(VERIFY, 200, {"verified": True})
    http.on_get(
        INBOXES,
        200,
        {"inboxes": [{"inbox_id": inbox_id, "email": ISSUED}]},
    )
    _patch_agentmail(monkeypatch, http)
    outputs: list[str] = []

    assert main(["--json", "email", "connect"]) == 3
    captured = capsys.readouterr()
    outputs.append(captured.out + captured.err)
    method_step = json.loads(captured.out)
    assert method_step["option"]["name"] == "setup_method"
    assert method_step["option"]["choices"] == [
        "existing_credential",
        "create_account",
    ]

    create = value_file(tmp_path, "create_account", "method.txt")
    assert (
        main(
            [
                "--json",
                "email",
                "connect",
                "--continue",
                "--state",
                method_step["state"],
                "--result-file",
                create,
            ]
        )
        == 3
    )
    captured = capsys.readouterr()
    outputs.append(captured.out + captured.err)
    email_step = json.loads(captured.out)
    assert email_step["option"]["name"] == "human_email"

    human_email = value_file(tmp_path, "owner@example.com", "human-email.txt")
    assert (
        main(
            [
                "--json",
                "email",
                "connect",
                "--continue",
                "--state",
                email_step["state"],
                "--result-file",
                human_email,
            ]
        )
        == 3
    )
    captured = capsys.readouterr()
    outputs.append(captured.out + captured.err)
    otp_step = json.loads(captured.out)
    assert otp_step["option"]["name"] == "otp"
    assert otp_step["option"]["sensitive"] is True
    continuation = (
        vault / "identities" / "agent" / "secrets" / "internal.email.continuation.sops"
    )
    assert continuation.is_file()
    assert generated.encode() not in continuation.read_bytes()

    otp = value_file(tmp_path, "123456", "otp.txt")
    assert (
        main(
            [
                "--json",
                "email",
                "connect",
                "--continue",
                "--state",
                otp_step["state"],
                "--result-file",
                otp,
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    outputs.append(captured.out + captured.err)
    connected = json.loads(captured.out)
    assert connected["address"] == ISSUED
    assert connected["status"] == "connected"
    assert "private_outputs" not in connected
    assert not continuation.exists()
    persisted = run_cli(["--json", "secret", "get", "email.credential", "--print"], env)
    assert persisted.returncode == 0
    assert json.loads(persisted.stdout)["value"] == generated
    address = run_cli(["--json", "secret", "get", "email.address", "--print"], env)
    assert json.loads(address.stdout)["value"] == ISSUED
    assert generated not in "".join(outputs)
    assert "123456" not in "".join(outputs)
    assert len([post for post in http.posts if post[0] == SIGN_UP]) == 1


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
