"""Config persist, live mailbox address, stdin set, channel reason tokens."""

from __future__ import annotations

import json

from agentself.backends.wallet.contract import WalletError
from agentself.cli.app import main
from agentself.compose import compose
from agentself.local import bind_local, load_config

from tests.support import (
    MockRpc,
    apply_cli_env,
    cli_env,
    compose_with_rpc,
    run_cli,
    value_file,
)


def test_start_persists_bindings_restart_without_env(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(
        ["init", "--email", "imap", "--wallet", "base"],
        env,
    )
    assert start.returncode == 0, start.stderr
    cfg = load_config(vault)
    assert cfg["email_backend"] == "imap"
    assert cfg["wallet_backend"] == "base"

    later = cli_env(vault)
    assert "AGENTSELF_EMAIL_BACKEND" not in later
    assert "AGENTSELF_WALLET_BACKEND" not in later
    ident = run_cli(["--json", "show"], later)
    assert ident.returncode == 0, ident.stderr
    data = json.loads(ident.stdout)
    assert data["email_backend"] == "imap"
    assert data["wallet_backend"] == "base"


def test_start_env_binding_persists_then_compose_without_env(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    env["AGENTSELF_EMAIL_BACKEND"] = "imap"
    start = run_cli(["init"], env)
    assert start.returncode == 0, start.stderr
    cfg = load_config(vault)
    assert cfg["email_backend"] == "imap"

    later = cli_env(vault)
    for key in (
        "AGENTSELF_EMAIL_BACKEND",
        "AGENTSELF_WALLET_BACKEND",
        "AGENTSELF_MAIL_DOMAIN",
        "AGENTSELF_IDENTITY_ID",
        "AGE_KEY_FILE",
    ):
        later.pop(key, None)
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AGENTSELF_IDENTITY_DIR", str(vault))
    client = compose(vault, bind=lambda: bind_local(vault))
    view = client.identity()
    assert view["email_backend"] == "imap"
    ident = run_cli(["--json", "show"], later)
    assert ident.returncode == 0, ident.stderr
    assert json.loads(ident.stdout)["email_backend"] == "imap"


def test_env_overrides_config_bindings(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init", "--email", "imap"], env)
    assert start.returncode == 0, start.stderr
    cfg = load_config(vault)
    assert cfg["email_backend"] == "imap"

    override = cli_env(vault)
    override["AGENTSELF_EMAIL_BACKEND"] = "agentmail"
    ident = run_cli(["--json", "show"], override)
    assert ident.returncode == 0, ident.stderr
    data = json.loads(ident.stdout)
    assert data["email_backend"] == "agentmail"


def test_set_from_stdin_and_argv(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init"], env)
    assert start.returncode == 0, start.stderr

    secret = "stdin-secret-value"
    sealed = run_cli(["secret", "create", "notes"], env, input=secret + "\n")
    assert sealed.returncode == 0, sealed.stderr
    assert secret not in sealed.stdout
    assert secret not in sealed.stderr
    got = run_cli(["secret", "get", "notes", "--print"], env)
    assert got.returncode == 0, got.stderr
    assert got.stdout == secret + "\n"

    argv = run_cli(["secret", "create", "other", "argv-value"], env)
    assert argv.returncode == 0, argv.stderr
    assert "argv-value" not in argv.stdout
    assert "argv-value" not in argv.stderr
    got_argv = run_cli(["secret", "get", "other", "--print"], env)
    assert got_argv.returncode == 0, got_argv.stderr
    assert got_argv.stdout == "argv-value\n"

    line_file = tmp_path / "line.txt"
    line_file.write_text("already-terminated\n", encoding="utf-8")
    created = run_cli(
        ["secret", "create", "line", "--file", str(line_file)],
        env,
    )
    assert created.returncode == 0, created.stderr
    got_line = run_cli(["secret", "get", "line", "--print"], env)
    assert got_line.returncode == 0, got_line.stderr
    assert got_line.stdout == "already-terminated\n"


def test_set_value_and_file_fails_closed(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init"], env)
    assert start.returncode == 0, start.stderr
    path = tmp_path / "hold.txt"
    path.write_text("from-file\n", encoding="utf-8")
    proc = run_cli(
        ["secret", "create", "notes", "argv-secret", "--file", str(path)], env
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "next:" in proc.stderr
    assert "argv-secret" not in proc.stdout + proc.stderr
    assert "from-file" not in proc.stdout + proc.stderr
    missing = run_cli(["secret", "get", "notes", "--meta"], env)
    assert missing.returncode == 3


def test_cli_json_email_receive_list_without_token_has_reason(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init", "--email", "agentmail"], env)
    assert start.returncode == 0, start.stderr
    for verb in ("receive", "list"):
        proc = run_cli(["--json", "email", verb], env)
        assert proc.returncode == 1, proc.stdout + proc.stderr
        err = json.loads(proc.stdout or proc.stderr)
        assert err["ok"] is False
        assert err["reason"] == "no_token"
        human = run_cli(["email", verb], env)
        assert human.returncode == 1, human.stdout + human.stderr


_TO = "0x" + "11" * 20


def test_cli_wallet_send_no_eth_names_eth(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init"], env)
    assert start.returncode == 0, start.stderr
    apply_cli_env(monkeypatch, env)
    compose_with_rpc(monkeypatch, MockRpc(eth_wei=0))
    code = main(["wallet", "send", _TO, "1"])
    captured = capsys.readouterr()
    assert code == 2, captured.out + captured.err
    assert captured.err == "refused: need gas\n"
    assert "ETH" not in captured.err
    assert "USDC" not in captured.err


def test_cli_wallet_send_no_usdc_names_usdc(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init"], env)
    assert start.returncode == 0, start.stderr
    apply_cli_env(monkeypatch, env)
    compose_with_rpc(monkeypatch, MockRpc(eth_wei=10**18, usdc_raw=0))
    code = main(["wallet", "send", _TO, "1"])
    captured = capsys.readouterr()
    assert code == 2, captured.out + captured.err
    assert captured.err == "refused: need funds\n"
    assert "USDC" not in captured.err
    assert "ETH" not in captured.err


def test_cli_wallet_send_wrong_asset_names_usdc(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init"], env)
    assert start.returncode == 0, start.stderr
    apply_cli_env(monkeypatch, env)
    compose_with_rpc(monkeypatch, MockRpc(eth_wei=0))
    code = main(["wallet", "send", _TO, "1", "ETH"])
    captured = capsys.readouterr()
    assert code == 2, captured.out + captured.err
    assert captured.err == "refused: backend cannot send\n"
    assert "USDC" not in captured.err
    assert "need ETH for gas" not in captured.err


def test_cli_wallet_send_rpc_is_error_rpc(tmp_path, monkeypatch, capsys):
    class BoomRpc:
        def request(self, method: str, params: list[object]) -> object:
            raise WalletError("rpc failed")

    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init"], env)
    assert start.returncode == 0, start.stderr
    apply_cli_env(monkeypatch, env)
    compose_with_rpc(monkeypatch, BoomRpc())
    code = main(["wallet", "send", _TO, "1"])
    captured = capsys.readouterr()
    assert code == 1, captured.out + captured.err
    assert captured.err == "error: rpc\nnext: agentself backends wallet\n"


class _MissingKeyWallet:
    def required_material(self):
        return None

    def send(self, identity_id, to, amount, asset):
        raise WalletError("missing key")

    def address(self, identity_id):
        raise WalletError("missing key")

    def authorize(self, identity_id, message):
        raise WalletError("missing key")

    def verify(self, identity_id, message, authorization):
        raise WalletError("missing key")

    def balance(self, identity_id):
        raise WalletError("missing key")

    def describe(self, identity_id):
        raise WalletError("missing key")


def test_cli_wallet_send_missing_key_is_no_key(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init"], env)
    assert start.returncode == 0, start.stderr
    apply_cli_env(monkeypatch, env)
    monkeypatch.setattr(
        "agentself.compose.WalletAccessFactory.for_binding",
        lambda self, binding: _MissingKeyWallet(),
    )
    code = main(["wallet", "send", _TO, "1"])
    captured = capsys.readouterr()
    assert code == 1, captured.out + captured.err
    assert captured.err == "error: no_key\nnext: agentself backends wallet\n"


def test_cli_json_email_receive_mixed_is_ok(tmp_path, monkeypatch, capsys):
    from urllib.parse import quote

    from agentself.backends.email.agentmail import AgentMailMailboxAccess

    from tests.test_agentmail_mailbox import API, INBOXES, OURS, Http

    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init", "--email", "agentmail"], env)
    assert start.returncode == 0, start.stderr
    sealed = run_cli(
        [
            "secret",
            "create",
            "email.credential",
            "--file",
            value_file(tmp_path, "hold-value"),
        ],
        env,
    )
    assert sealed.returncode == 0, sealed.stderr
    apply_cli_env(monkeypatch, env)
    http = Http()
    inbox_id = "inb_recv"
    bad_id = "msg@unsafe"
    good_id = "msg_ok"
    http.on_get(
        INBOXES,
        200,
        {"inboxes": [{"inbox_id": inbox_id, "email": OURS}]},
    )
    http.on_get(
        f"{API}/v0/inboxes/{inbox_id}/messages",
        200,
        {
            "messages": [
                {
                    "message_id": bad_id,
                    "from": "a@example.com",
                    "to": [OURS],
                    "subject": "bad",
                    "preview": "short",
                },
                {
                    "message_id": good_id,
                    "from": "b@example.com",
                    "to": [OURS],
                    "subject": "good",
                    "preview": "pre",
                },
            ]
        },
    )
    http.on_get(
        f"{API}/v0/inboxes/{inbox_id}/messages/{quote(bad_id, safe='')}",
        400,
        {"error": "bad id"},
    )
    http.on_get(
        f"{API}/v0/inboxes/{inbox_id}/messages/{quote(good_id, safe='')}",
        200,
        {"text": "full good"},
    )

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
    code = main(["--json", "email", "receive"])
    captured = capsys.readouterr()
    assert code == 0, captured.out + captured.err
    data = json.loads(captured.out)
    assert data["ok"] is True
    messages = data["messages"]
    by_id = {item["id"]: item for item in messages}
    assert bad_id in by_id
    assert good_id in by_id
    assert all("body" not in item for item in messages)
    assert "body" not in by_id[good_id]
    assert by_id[good_id]["status"] == "seen"
    assert not [url for url, _headers in http.gets if "/messages/msg" in url]
    assert "hold-value" not in captured.out
    assert "hold-value" not in captured.err

    body_file = tmp_path / "message-body.txt"
    code = main(["--json", "email", "receive", good_id, "--file", str(body_file)])
    captured = capsys.readouterr()
    assert code == 0, captured.out + captured.err
    exported = json.loads(captured.out)["messages"][0]
    assert "body" not in exported
    assert exported["body_file"] == str(body_file)
    assert body_file.read_text(encoding="utf-8") == "full good"

    code = main(["--json", "email", "receive", good_id, "--print"])
    captured = capsys.readouterr()
    assert code == 0, captured.out + captured.err
    assert json.loads(captured.out)["messages"][0]["body"] == "full good"
