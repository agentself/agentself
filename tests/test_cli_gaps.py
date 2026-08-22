"""Config persist, live mailbox address, stdin set, channel reason tokens."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agentself.backends.email.contract import MailboxError
from agentself.backends.wallet.contract import WalletError
from agentself.cli.app import _secret_from_args, main
from agentself.compose import compose
from agentself.internal.custody.errors import ChannelFailure
from agentself.internal.log import MemoryLog
from agentself.local import bind_local, load_config

from tests.maildir_mailbox import MaildirMailboxAccess
from tests.support import (
    MockRpc,
    apply_cli_env,
    build_app,
    cli_env,
    compose_with_rpc,
    run_cli,
    setup_principal,
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
    assert "sms_binding" not in cfg

    later = cli_env(vault)
    assert "AGENTSELF_EMAIL_BACKEND" not in later
    assert "AGENTSELF_WALLET_BACKEND" not in later
    ident = run_cli(["--json", "show"], later)
    assert ident.returncode == 0, ident.stderr
    data = json.loads(ident.stdout)
    assert data["email_backend"] == "imap"
    assert data["wallet_backend"] == "base"
    assert "sms_binding" not in data
    shown = run_cli(["wallet", "show"], later)
    assert shown.returncode == 0, shown.stderr


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
    monkeypatch.setenv("AGENTSELF_VAULT_ROOT", str(vault))
    gateway = compose(vault, bind=lambda: bind_local(vault))
    view = gateway.identity()
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


def test_maildir_describe_does_not_invent_principal_at_domain(vault):
    mb = MaildirMailboxAccess(vault, MemoryLog(), domain="example.com")
    desc = mb.describe("P")
    assert desc["owned_address"] is False
    assert desc["address"] is None
    assert desc["address"] != "P@example.com"
    assert "P@example.com" not in str(desc)
    held = mb.describe("P", address="inbox@example.com")
    assert held["owned_address"] is True
    assert held["address"] == "inbox@example.com"


def test_email_set_does_not_print_invented_maildir_inbox(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init"], env)
    assert start.returncode == 0, start.stderr
    proc = run_cli(["email", "connect", "--domain", "example.com"], env)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "unrecognized arguments: --domain" in proc.stderr
    shown = run_cli(["--json", "email", "show"], env)
    assert shown.returncode == 0, shown.stderr
    email = json.loads(shown.stdout)
    assert email["owned_address"] is False
    assert email["address"] is None
    assert "agent@example.com" not in shown.stdout


def test_set_from_stdin_and_argv(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init"], env)
    assert start.returncode == 0, start.stderr

    secret = "stdin-secret-value"
    sealed = run_cli(["secret", "create", "notes"], env, input=secret + "\n")
    assert sealed.returncode == 0, sealed.stderr
    assert sealed.stdout == ""
    assert secret not in sealed.stdout
    assert secret not in sealed.stderr
    got = run_cli(["secret", "get", "notes"], env)
    assert got.returncode == 0, got.stderr
    assert got.stdout.strip() == secret

    argv = run_cli(["secret", "create", "other", "argv-value"], env)
    assert argv.returncode == 0, argv.stderr
    assert argv.stdout == ""
    assert "argv-value" not in argv.stdout
    assert "argv-value" not in argv.stderr
    got_argv = run_cli(["secret", "get", "other"], env)
    assert got_argv.returncode == 0, got_argv.stderr
    assert got_argv.stdout.strip() == "argv-value"


def test_set_from_file_strips_one_newline(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init"], env)
    assert start.returncode == 0, start.stderr
    path = tmp_path / "hold.txt"
    path.write_text("file-secret\n", encoding="utf-8")
    sealed = run_cli(["secret", "create", "notes", "--file", str(path)], env)
    assert sealed.returncode == 0, sealed.stderr
    assert "file-secret" not in sealed.stdout
    assert "file-secret" not in sealed.stderr
    got = run_cli(["secret", "get", "notes"], env)
    assert got.returncode == 0, got.stderr
    assert got.stdout.strip() == "file-secret"


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
    lines = [line for line in proc.stderr.splitlines() if line.strip()]
    assert lines[0].startswith("error:"), proc.stderr
    assert "next:" in proc.stderr
    assert "argv-secret" not in proc.stdout + proc.stderr
    assert "from-file" not in proc.stdout + proc.stderr
    missing = run_cli(["secret", "get", "notes"], env)
    assert missing.returncode == 3


def test_set_tty_without_value_fails_closed(monkeypatch):
    class Tty:
        def isatty(self) -> bool:
            return True

        def read(self) -> str:
            raise AssertionError("must not read")

    monkeypatch.setattr("agentself.cli.app.sys.stdin", Tty())
    value, err = _secret_from_args(SimpleNamespace(value=None, from_file=""))
    assert value is None
    assert err == "need a value"


def test_wallet_balance_rpc_failure_has_reason(vault, monkeypatch):
    class BoomRpc:
        def request(self, method: str, params: list[object]) -> object:
            raise WalletError("rpc failed")

    app = build_app(vault, rpc=BoomRpc())
    app.keys["P"] = setup_principal(app.vault, "P", store="sops")
    app.bind(monkeypatch, "P")
    app.gateway.enroll("sops")
    with pytest.raises(ChannelFailure) as caught:
        app.gateway.wallet_balance()
    assert caught.value.reason == "rpc"


def test_wallet_balance_zero_usdc_is_success(vault, monkeypatch):
    rpc = MockRpc(eth_wei=0, usdc_raw=0)
    app = build_app(vault, rpc=rpc)
    app.keys["P"] = setup_principal(app.vault, "P", store="sops")
    app.bind(monkeypatch, "P")
    app.gateway.enroll("sops")
    bal = app.gateway.wallet_balance()
    assert bal["amount"] == "0"
    assert bal["gas_asset"] == "ETH"
    assert bal["gas_raw"] == "0"
    assert bal["gas_amount"] == "0"
    assert any(c[0] == "eth_call" for c in rpc.calls)


def test_identity_human_names_bindings(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init"], env)
    assert start.returncode == 0, start.stderr
    cfg = load_config(vault)
    assert cfg["email_backend"] == "agentmail"
    assert cfg["wallet_backend"] == "base"
    assert "sms_binding" not in cfg
    ident = run_cli(["show"], env)
    assert ident.returncode == 0, ident.stderr
    out = ident.stdout
    assert "email_backend: agentmail" in out
    assert "wallet_backend: base" in out
    assert "sms_binding" not in out
    assert "email:" in out
    assert "wallet:" in out


_REASONS = frozenset({"no_token", "rpc", "mailbox_error", "not_ready"})


class _FailMailbox:
    def __init__(self, fail: str, *, need_token: bool = False) -> None:
        self._fail = fail
        self._need_token = need_token

    def send(self, principal_id, to, subject, body, send_token=None, address=None):
        if self._need_token and not send_token:
            raise MailboxError("missing credentials")
        raise MailboxError(self._fail)

    def recv(self, principal_id, *, send_token=None, address=None, message_id=None):
        if self._need_token and not send_token:
            raise MailboxError("missing credentials")
        raise MailboxError(self._fail)

    def list(self, principal_id, *, send_token=None, address=None):
        if self._need_token and not send_token:
            raise MailboxError("missing credentials")
        raise MailboxError(self._fail)

    def describe(self, principal_id, *, send_token=None, address=None):
        return {
            "address": None,
            "owned_address": False,
            "needs_domain": True,
        }

    def connect(self, principal_id, *, send_token=None, address=None):
        if self._need_token and not send_token:
            raise MailboxError("missing credentials")
        raise MailboxError(self._fail)


class _FailFactory:
    def __init__(self, mailbox: _FailMailbox) -> None:
        self._mailbox = mailbox

    def for_binding(self, binding: str):
        return self._mailbox


def test_email_recv_list_no_token_sets_reason(vault, monkeypatch):
    app = build_app(vault)
    app.keys["P"] = setup_principal(app.vault, "P", store="sops")
    app.bind(monkeypatch, "P")
    app.gateway.enroll("sops")
    app.manager._mailboxes = _FailFactory(_FailMailbox("recv failed", need_token=True))
    with pytest.raises(ChannelFailure) as recvd:
        app.gateway.email_recv()
    assert recvd.value.reason == "no_token"
    assert "hold" not in str(recvd.value).lower()
    with pytest.raises(ChannelFailure) as listed:
        app.gateway.email_list()
    assert listed.value.reason == "no_token"


def test_email_recv_list_rpc_sets_reason(vault, monkeypatch):
    app = build_app(vault)
    app.keys["P"] = setup_principal(app.vault, "P", store="sops")
    app.bind(monkeypatch, "P")
    app.gateway.enroll("sops")
    app.gateway.seal("email.send.token", "hold-value")
    app.manager._mailboxes = _FailFactory(_FailMailbox("rpc failed"))
    with pytest.raises(ChannelFailure) as recvd:
        app.gateway.email_recv()
    assert recvd.value.reason == "rpc"
    assert "hold-value" not in str(recvd.value)
    with pytest.raises(ChannelFailure) as listed:
        app.gateway.email_list()
    assert listed.value.reason == "rpc"
    assert "hold-value" not in str(listed.value)


def test_email_recv_other_mailbox_error_reason(vault, monkeypatch):
    app = build_app(vault)
    app.keys["P"] = setup_principal(app.vault, "P", store="sops")
    app.bind(monkeypatch, "P")
    app.gateway.enroll("sops")
    app.gateway.seal("email.send.token", "hold-value")
    app.manager._mailboxes = _FailFactory(_FailMailbox("no inbox"))
    with pytest.raises(ChannelFailure) as recvd:
        app.gateway.email_recv()
    assert recvd.value.reason == "mailbox_error"
    assert "hold-value" not in str(recvd.value)


def test_cli_json_email_recv_list_without_token_has_reason(tmp_path):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init", "--email", "agentmail"], env)
    assert start.returncode == 0, start.stderr
    for verb in ("receive", "list"):
        proc = run_cli(["--json", "email", verb], env)
        assert proc.returncode == 1, proc.stdout + proc.stderr
        err = json.loads(proc.stderr)
        assert err["ok"] is False
        assert err["error"] == "error"
        assert "reason" in err
        assert err["reason"] in _REASONS
        assert err["reason"] == "no_token"
        human = run_cli(["email", verb], env)
        assert human.returncode == 1, human.stdout + human.stderr
        assert human.stderr == "error: no_token\nnext: agentself backends email\n"


def test_cli_json_email_recv_list_injected_rpc(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init"], env)
    assert start.returncode == 0, start.stderr
    sealed = run_cli(["secret", "create", "email.send.token", "hold-value"], env)
    assert sealed.returncode == 0, sealed.stderr
    assert "hold-value" not in sealed.stdout
    assert "hold-value" not in sealed.stderr
    monkeypatch.setenv("AGENTSELF_VAULT_ROOT", str(vault))
    monkeypatch.setenv("PATH", env["PATH"])
    for key in (
        "AGENTSELF_EMAIL_BACKEND",
        "AGENTSELF_WALLET_BACKEND",
        "AGENTSELF_MAIL_DOMAIN",
        "AGENTSELF_IDENTITY_ID",
        "AGE_KEY_FILE",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(
        "agentself.compose.MailboxAccessFactory.for_binding",
        lambda self, binding: _FailMailbox("http failed"),
    )
    for verb in ("receive", "list"):
        code = main(["--json", "email", verb])
        captured = capsys.readouterr()
        assert code == 1, captured.out + captured.err
        err = json.loads(captured.err)
        assert err["ok"] is False
        assert err["error"] == "error"
        assert "reason" in err
        assert err["reason"] in _REASONS
        assert err["reason"] == "rpc"
        assert "hold-value" not in captured.out
        assert "hold-value" not in captured.err
        code_h = main(["email", verb])
        human = capsys.readouterr()
        assert code_h == 1, human.out + human.err
        assert human.err == "error: rpc\nnext: agentself backends email\n"
        assert "hold-value" not in human.out + human.err


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
    assert captured.err == "refused: EOA has no ETH\n"


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
    assert "USDC" in captured.err
    assert captured.err.startswith("refused:")


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
    assert "USDC" in captured.err
    assert "EOA has no ETH" not in captured.err


def test_cli_wallet_send_broadcasts_with_mock(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init"], env)
    assert start.returncode == 0, start.stderr
    apply_cli_env(monkeypatch, env)
    rpc = MockRpc(eth_wei=10**18, usdc_raw=2_000_000)
    compose_with_rpc(monkeypatch, rpc)
    code = main(["wallet", "send", _TO, "1"])
    captured = capsys.readouterr()
    assert code == 0, captured.out + captured.err
    assert rpc.broadcast


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
    needs_material = False

    def send(self, principal_id, to, amount, asset):
        raise WalletError("missing key")

    def address(self, principal_id):
        raise WalletError("missing key")

    def sign(self, principal_id, message):
        raise WalletError("missing key")

    def balance(self, principal_id):
        raise WalletError("missing key")

    def describe(self, principal_id):
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


def test_cli_json_wallet_balance_includes_gas(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init"], env)
    assert start.returncode == 0, start.stderr
    apply_cli_env(monkeypatch, env)
    compose_with_rpc(monkeypatch, MockRpc(eth_wei=10**18, usdc_raw=1_500_000))
    code = main(["--json", "wallet", "balance"])
    captured = capsys.readouterr()
    assert code == 0, captured.out + captured.err
    data = json.loads(captured.out)
    assert data["ok"] is True
    assert data["asset"] == "USDC"
    assert data["amount"] == "1.5"
    assert data["gas_asset"] == "ETH"
    assert data["gas_raw"] == "1000000000000000000"
    assert data["gas_amount"] == "1"
    assert "e" not in str(data["gas_amount"]).lower()


def test_cli_json_email_recv_mixed_is_ok(tmp_path, monkeypatch, capsys):
    from urllib.parse import quote

    from agentself.backends.email.agentmail import AgentMailMailboxAccess

    from tests.test_agentmail_mailbox import API, INBOXES, OURS, Http

    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init", "--email", "agentmail"], env)
    assert start.returncode == 0, start.stderr
    sealed = run_cli(["secret", "create", "email.send.token", "hold-value"], env)
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
    assert data.get("ok") is not False
    assert data.get("error") != "error"
    assert data["ok"] is True
    messages = data["messages"]
    by_id = {item["id"]: item for item in messages}
    assert bad_id in by_id
    assert good_id in by_id
    assert by_id[bad_id]["reason"] in {"mailbox_error", "http"}
    assert by_id[good_id]["body"] == "full good"
    assert "hold-value" not in captured.out
    assert "hold-value" not in captured.err


def test_cli_json_email_recv_one_id_is_ok(tmp_path, monkeypatch, capsys):
    from urllib.parse import quote

    from agentself.backends.email.agentmail import AgentMailMailboxAccess

    from tests.test_agentmail_mailbox import API, INBOXES, OURS, Http

    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init", "--email", "agentmail"], env)
    assert start.returncode == 0, start.stderr
    sealed = run_cli(["secret", "create", "email.send.token", "hold-value"], env)
    assert sealed.returncode == 0, sealed.stderr
    apply_cli_env(monkeypatch, env)
    http = Http()
    inbox_id = "inb_recv"
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
                    "message_id": good_id,
                    "from": "b@example.com",
                    "to": [OURS],
                    "subject": "good",
                    "preview": "pre",
                }
            ]
        },
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
    first = main(["--json", "email", "receive"])
    first_cap = capsys.readouterr()
    assert first == 0, first_cap.out + first_cap.err
    first_data = json.loads(first_cap.out)
    assert first_data["ok"] is True
    assert first_data["messages"][0]["id"] == good_id
    code = main(["--json", "email", "receive", good_id])
    captured = capsys.readouterr()
    assert code == 0, captured.out + captured.err
    data = json.loads(captured.out)
    assert data["ok"] is True
    assert data.get("error") != "error"
    assert len(data["messages"]) == 1
    assert data["messages"][0]["id"] == good_id
    assert data["messages"][0]["body"] == "full good"
    assert "hold-value" not in captured.out
    assert "hold-value" not in captured.err


def test_cli_wallet_authorize_and_silent_sign_alias(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init"], env)
    assert start.returncode == 0, start.stderr
    apply_cli_env(monkeypatch, env)
    code = main(["wallet", "authorize", "hello"])
    captured = capsys.readouterr()
    assert code == 0, captured.out + captured.err
    line = captured.out.strip()
    assert line.startswith("0x")
    assert "\n" not in captured.out.strip()
    with pytest.raises(SystemExit) as exited:
        main(["wallet", "sign", "hello"])
    assert exited.value.code == 2
    sign_cap = capsys.readouterr()
    assert "invalid choice" in sign_cap.err


def test_cli_json_wallet_authorize_has_authorization(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    env = cli_env(vault)
    start = run_cli(["init"], env)
    assert start.returncode == 0, start.stderr
    apply_cli_env(monkeypatch, env)
    code = main(["--json", "wallet", "authorize", "hello"])
    captured = capsys.readouterr()
    assert code == 0, captured.out + captured.err
    data = json.loads(captured.out)
    assert data["ok"] is True
    assert data["authorization"].startswith("0x")
    assert data.get("signature") == data["authorization"]
