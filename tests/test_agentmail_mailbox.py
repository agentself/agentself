"""AgentMail mailbox: inbox from secret / GET /v0/inboxes, never identity@domain."""

from __future__ import annotations

import json
import os
import urllib.error
from pathlib import Path
from urllib.parse import quote

import pytest

from agentself.backends.email.agentmail import AgentMailMailboxAccess
from agentself.backends.email.contract import MailboxError
from agentself.internal.custody.errors import ChannelFailure
from agentself.internal.files import identity_home
from agentself.internal.log import MemoryLog

from tests.support import build_app, init_identity

CANARY = "CANARY-AGENTMAIL-TOKEN-DO-NOT-LEAK"
PRINCIPAL = "money-maker"
OURS = "money-maker-bot@agentmail.to"
TAKEN = "money-maker@agentmail.to"
API = "https://api.agentmail.to"
INBOXES = API + "/v0/inboxes"
SIGN_UP = API + "/v0/agent/sign-up"
VERIFY = API + "/v0/agent/verify"


class Http:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict[str, str], bytes]] = []
        self.gets: list[tuple[str, dict[str, str]]] = []
        self._gets: dict[str, tuple[int, bytes] | BaseException] = {}
        self._post: tuple[int, bytes] | BaseException = (200, b"{}")
        self._posts: dict[str, list[tuple[int, bytes] | BaseException]] = {}

    def on_get(self, url: str, status: int, payload: object) -> None:
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self._gets[url] = (status, body)

    def get_raises(self, url: str, exc: BaseException) -> None:
        self._gets[url] = exc

    def post_result(self, status: int = 200, payload: object | None = None) -> None:
        body = (
            b"{}"
            if payload is None
            else (
                payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            )
        )
        self._post = (status, body)

    def post_raises(self, exc: BaseException) -> None:
        self._post = exc

    def on_post(self, url: str, status: int, payload: object) -> None:
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self._posts.setdefault(url, []).append((status, body))

    def poster(
        self, url: str, headers: dict[str, str], payload: bytes
    ) -> tuple[int, bytes]:
        self.posts.append((url, dict(headers), payload))
        queued = self._posts.get(url, [])
        result = queued.pop(0) if queued else self._post
        if isinstance(result, BaseException):
            raise result
        return result

    def getter(self, url: str, headers: dict[str, str]) -> tuple[int, bytes]:
        self.gets.append((url, dict(headers)))
        result = self._gets.get(url)
        if result is None:
            raise AssertionError("unexpected GET")
        if isinstance(result, BaseException):
            raise result
        return result


def _box(
    vault: Path, log: MemoryLog, http: Http, domain: str = ""
) -> AgentMailMailboxAccess:
    return AgentMailMailboxAccess(
        vault, log, domain=domain, poster=http.poster, getter=http.getter
    )


def _secret_absent(log: MemoryLog, exc: BaseException | None = None) -> None:
    blobs = [log.rendered()]
    if exc is not None:
        blobs.append(str(exc))
        blobs.append(repr(exc))
    for blob in blobs:
        assert CANARY not in blob


def _no_local_outbox(vault: Path) -> None:
    for rel in (
        Path("identities") / PRINCIPAL / "outbox",
        Path("identities") / PRINCIPAL / "maildir",
        Path("identities") / PRINCIPAL / "routing",
    ):
        path = vault / rel
        assert not path.exists() or not any(path.rglob("*"))


def test_no_token_zero_http(vault, monkeypatch):
    monkeypatch.delenv("AGENTSELF_AGENTMAIL_API_KEY", raising=False)
    monkeypatch.delenv("AGENTSELF_EMAIL_CREDENTIAL", raising=False)
    log = MemoryLog()
    http = Http()
    mb = _box(vault, log, http, domain="agentmail.to")
    with pytest.raises(MailboxError, match="missing credentials") as send_err:
        mb.send(PRINCIPAL, "a@example.com", "s", "b")
    with pytest.raises(MailboxError, match="missing credentials") as recv_err:
        mb.receive(PRINCIPAL)
    with pytest.raises(MailboxError, match="missing credentials") as list_err:
        mb.list(PRINCIPAL)
    with pytest.raises(MailboxError, match="missing credentials"):
        mb.send(PRINCIPAL, "a@example.com", "s", "b", credential="")
    assert http.posts == []
    assert http.gets == []
    _no_local_outbox(vault)
    for exc in (send_err.value, recv_err.value, list_err.value):
        _secret_absent(log, exc)
        assert TAKEN not in str(exc)


def test_send_unique_inbox_uses_that_inbox_id(vault):
    log = MemoryLog()
    http = Http()
    inbox_id = "inb_unique_bot"
    http.on_get(
        INBOXES,
        200,
        {"inboxes": [{"inbox_id": inbox_id, "email": "bot-only@agentmail.to"}]},
    )
    mb = _box(vault, log, http, domain="agentmail.to")
    mb.send(
        PRINCIPAL,
        "someone@example.com",
        "hello",
        "body-text",
        credential=CANARY,
    )
    assert len(http.posts) == 1
    url, headers, payload = http.posts[0]
    assert url == f"{API}/v0/inboxes/{inbox_id}/messages/send"
    assert TAKEN not in url
    data = json.loads(payload.decode("utf-8"))
    assert data["to"] == "someone@example.com"
    assert data["subject"] == "hello"
    assert data["text"] == "body-text"
    assert "from" not in data
    assert headers["Authorization"] == "Bearer " + CANARY
    _secret_absent(log)
    _no_local_outbox(vault)


def test_send_picks_address_secret_among_many_inboxes(vault):
    log = MemoryLog()
    http = Http()
    taken_id = "inb_taken_mm"
    ours_id = "inb_bot_mmb"
    http.on_get(
        INBOXES,
        200,
        {
            "inboxes": [
                {"inbox_id": taken_id, "email": TAKEN},
                {"inbox_id": ours_id, "email": OURS},
                {"inbox_id": "inb_other", "email": "other@agentmail.to"},
            ]
        },
    )
    mb = _box(vault, log, http, domain="agentmail.to")
    mb.send(
        PRINCIPAL,
        "someone@example.com",
        "hello",
        "body-text",
        credential=CANARY,
        address=OURS,
    )
    assert len(http.posts) == 1
    url, headers, payload = http.posts[0]
    assert url == f"{API}/v0/inboxes/{ours_id}/messages/send"
    assert taken_id not in url
    assert TAKEN not in url
    assert ours_id in url
    data = json.loads(payload.decode("utf-8"))
    assert data["to"] == "someone@example.com"
    assert data["subject"] == "hello"
    assert data["text"] == "body-text"
    assert headers["Authorization"] == "Bearer " + CANARY
    _secret_absent(log)
    _no_local_outbox(vault)


def test_receive_then_receive_empty_seen_modes(vault):
    log = MemoryLog()
    http = Http()
    inbox_id = "inb_recv"
    message_id = "msg@unsafe"
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
                    "message_id": message_id,
                    "from": "a@example.com",
                    "to": [OURS],
                    "subject": "hello",
                    "preview": "short",
                }
            ]
        },
    )
    quoted_id = quote(message_id, safe="")
    http.on_get(
        f"{API}/v0/inboxes/{inbox_id}/messages/{quoted_id}",
        200,
        {"text": "full body"},
    )
    mb = _box(vault, log, http, domain="agentmail.to")
    first = mb.receive(PRINCIPAL, credential=CANARY, address=OURS)
    assert len(first) == 1
    assert first[0]["id"] == message_id
    assert first[0]["from"] == "a@example.com"
    assert first[0]["to"] == OURS
    assert first[0]["subject"] == "hello"
    assert first[0]["body"] == "full body"
    seen = identity_home(vault, PRINCIPAL) / "agentmail" / "seen"
    assert seen.is_dir()
    if os.name != "nt":
        assert (seen.stat().st_mode & 0o777) == 0o700
    marks = list(seen.iterdir())
    assert len(marks) == 1
    if os.name != "nt":
        assert (marks[0].stat().st_mode & 0o777) == 0o600
    assert "@" not in marks[0].name
    body_gets = [
        url for url, _headers in http.gets if url.endswith(f"/messages/{quoted_id}")
    ]
    assert len(body_gets) == 1
    second = mb.receive(PRINCIPAL, credential=CANARY, address=OURS)
    assert second == []
    body_gets_after = [
        url for url, _headers in http.gets if url.endswith(f"/messages/{quoted_id}")
    ]
    assert len(body_gets_after) == 1
    _secret_absent(log)
    _no_local_outbox(vault)


def test_list_maps_meta_and_caches_inbox_id(vault):
    log = MemoryLog()
    http = Http()
    inbox_id = "inb_list"
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
                    "message_id": "m1",
                    "from": ["src@example.com"],
                    "to": ["a@x.to", "b@y.to"],
                    "subject": "subj",
                    "preview": "not a body fetch",
                }
            ]
        },
    )
    mb = _box(vault, log, http)
    items = mb.list(PRINCIPAL, credential=CANARY, address=OURS)
    assert items == [
        {
            "id": "m1",
            "from": "src@example.com",
            "to": "a@x.to, b@y.to",
            "subject": "subj",
            "status": "new",
        }
    ]
    cache = identity_home(vault, PRINCIPAL) / "agentmail" / "inbox_id"
    assert cache.read_text(encoding="utf-8") == inbox_id
    if os.name != "nt":
        assert (cache.stat().st_mode & 0o777) == 0o600
        assert (cache.parent.stat().st_mode & 0o777) == 0o700
    assert CANARY not in cache.read_text(encoding="utf-8")
    assert all(not url.endswith("/messages/m1") for url, _h in http.gets)
    _secret_absent(log)


@pytest.mark.parametrize(
    "boom",
    [TimeoutError(), urllib.error.URLError("down")],
)
def test_timeout_urlerror_fail_closed(vault, boom):
    log = MemoryLog()
    http = Http()
    http.get_raises(INBOXES, boom)
    http.post_raises(boom)
    mb = _box(vault, log, http, domain="agentmail.to")
    with pytest.raises(MailboxError, match="rpc failed") as send_err:
        mb.send(
            PRINCIPAL,
            "a@example.com",
            "s",
            "b",
            credential=CANARY,
            address=OURS,
        )
    with pytest.raises(MailboxError, match="rpc failed") as recv_err:
        mb.receive(PRINCIPAL, credential=CANARY, address=OURS)
    with pytest.raises(MailboxError, match="rpc failed") as list_err:
        mb.list(PRINCIPAL, credential=CANARY, address=OURS)
    assert http.posts == []
    _no_local_outbox(vault)
    for exc in (send_err.value, recv_err.value, list_err.value):
        _secret_absent(log, exc)


def test_send_poster_timeout_fail_closed(vault):
    log = MemoryLog()
    http = Http()
    http.on_get(
        INBOXES,
        200,
        {"inboxes": [{"inbox_id": "inb_to", "email": OURS}]},
    )
    http.post_raises(TimeoutError())
    mb = _box(vault, log, http)
    with pytest.raises(MailboxError, match="rpc failed") as err:
        mb.send(
            PRINCIPAL,
            "a@example.com",
            "s",
            "b",
            credential=CANARY,
            address=OURS,
        )
    _secret_absent(log, err.value)
    _no_local_outbox(vault)


def test_describe_address_without_token_is_not_owned(vault):
    log = MemoryLog()
    http = Http()
    mb = _box(vault, log, http, domain="agentmail.to")
    none = mb.describe(PRINCIPAL)
    assert none["owned_address"] is False
    assert none["address"] is None
    assert none["needs_domain"] is False
    assert TAKEN not in str(none)
    unverified = mb.describe(PRINCIPAL, address=OURS)
    assert unverified["owned_address"] is False
    assert unverified["address"] is None
    assert unverified["needs_domain"] is False
    assert TAKEN not in str(unverified)
    assert http.gets == []
    assert http.posts == []
    _secret_absent(log)


def test_describe_many_inboxes_without_address_is_no_inbox(vault):
    log = MemoryLog()
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
    mb = _box(vault, log, http, domain="agentmail.to")
    with pytest.raises(MailboxError, match="no inbox") as err:
        mb.describe(PRINCIPAL, credential=CANARY)
    _secret_absent(log, err.value)
    assert TAKEN not in str(err.value)


def test_address_mismatch_is_no_inbox(vault):
    log = MemoryLog()
    http = Http()
    http.on_get(
        INBOXES,
        200,
        {"inboxes": [{"inbox_id": "inb_taken", "email": TAKEN}]},
    )
    mb = _box(vault, log, http)
    with pytest.raises(MailboxError, match="no inbox") as err:
        mb.send(
            PRINCIPAL,
            "a@example.com",
            "s",
            "b",
            credential=CANARY,
            address=OURS,
        )
    assert http.posts == []
    _secret_absent(log, err.value)
    _no_local_outbox(vault)


def test_receive_mixed_inbox_keeps_good_and_reasons_bad(vault):
    log = MemoryLog()
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
    mb = _box(vault, log, http)
    messages = mb.receive(PRINCIPAL, credential=CANARY, address=OURS)
    assert len(messages) == 2
    by_id = {item["id"]: item for item in messages}
    bad = by_id[bad_id]
    good = by_id[good_id]
    assert bad["reason"] in {"mailbox_error", "http"}
    assert bad["body"] in {"short", ""}
    assert good["body"] == "full good"
    assert good.get("reason") not in {"mailbox_error", "http"}
    seen = identity_home(vault, PRINCIPAL) / "agentmail" / "seen"
    marked = {path.name for path in seen.iterdir()} if seen.is_dir() else set()
    assert good_id in marked
    second = mb.receive(PRINCIPAL, credential=CANARY, address=OURS)
    assert [item["id"] for item in second] == [bad_id]
    assert second[0]["reason"] in {"mailbox_error", "http"}
    assert good_id not in {item["id"] for item in second}
    _secret_absent(log)
    _no_local_outbox(vault)


def test_receive_message_id_returns_seen(vault):
    log = MemoryLog()
    http = Http()
    inbox_id = "inb_recv"
    message_id = "msg_ok"
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
                    "message_id": message_id,
                    "from": "a@example.com",
                    "to": [OURS],
                    "subject": "hello",
                    "preview": "short",
                }
            ]
        },
    )
    http.on_get(
        f"{API}/v0/inboxes/{inbox_id}/messages/{quote(message_id, safe='')}",
        200,
        {"text": "full body"},
    )
    mb = _box(vault, log, http)
    first = mb.receive(PRINCIPAL, credential=CANARY, address=OURS)
    assert first[0]["id"] == message_id
    assert mb.receive(PRINCIPAL, credential=CANARY, address=OURS) == []
    again = mb.receive(
        PRINCIPAL, credential=CANARY, address=OURS, message_id=message_id
    )
    assert len(again) == 1
    assert again[0]["id"] == message_id
    assert again[0]["body"] == "full body"
    _secret_absent(log)
    _no_local_outbox(vault)


def test_list_rejects_too_many_remote_messages(vault):
    log = MemoryLog()
    http = Http()
    inbox_id = "inb_many"
    http.on_get(
        INBOXES,
        200,
        {"inboxes": [{"inbox_id": inbox_id, "email": OURS}]},
    )
    http.on_get(
        f"{API}/v0/inboxes/{inbox_id}/messages",
        200,
        {"messages": [{"message_id": f"m{i}"} for i in range(101)]},
    )
    mb = _box(vault, log, http)
    with pytest.raises(MailboxError, match="list failed"):
        mb.list(PRINCIPAL, credential=CANARY, address=OURS)


ISSUED = "glint-otter@agentmail.to"


def test_connect_no_token_zero_http(vault):
    log = MemoryLog()
    http = Http()
    mb = _box(vault, log, http)
    desc = mb.connect(PRINCIPAL)
    assert desc["status"] == "input_required"
    assert desc["option"]["name"] == "setup_method"
    assert desc["option"]["choices"] == ["existing_credential", "create_account"]
    selected = mb.connect(PRINCIPAL, address=OURS)
    assert selected["status"] == "input_required"
    assert selected["option"]["name"] == "setup_method"
    assert not selected.get("owned_address")
    assert http.gets == []
    assert http.posts == []
    _secret_absent(log)
    assert TAKEN not in str(desc)
    assert f"{PRINCIPAL}@agentmail.to" not in str(desc)


def test_connect_existing_credential_is_an_explicit_setup_branch(vault):
    log = MemoryLog()
    http = Http()
    http.on_get(
        INBOXES,
        200,
        {"inboxes": [{"inbox_id": "inb_existing", "email": OURS}]},
    )
    mb = _box(vault, log, http)
    credential_step = mb.connect(
        PRINCIPAL,
        answers={"setup_method": "existing_credential"},
        state={"phase": "setup_method"},
    )
    assert credential_step["option"]["name"] == "credential"
    assert http.gets == []
    connected = mb.connect(
        PRINCIPAL,
        credential=CANARY,
        state=credential_step["continuation"],
    )
    assert connected["owned_address"] is True
    assert connected["address"] == OURS
    assert len(http.gets) == 1
    assert http.posts == []


def test_connect_selected_unknown_address_fails_without_local_state(vault):
    log = MemoryLog()
    http = Http()
    http.on_get(
        INBOXES,
        200,
        {"inboxes": [{"inbox_id": "inb_other", "email": TAKEN}]},
    )
    mb = _box(vault, log, http)
    with pytest.raises(MailboxError, match="no inbox"):
        mb.connect(PRINCIPAL, credential=CANARY, address=OURS)
    assert http.posts == []
    assert not (identity_home(vault, PRINCIPAL) / "agentmail").exists()
    _secret_absent(log)


def test_connect_discovers_unique_inbox_no_post(vault):
    log = MemoryLog()
    http = Http()
    http.on_get(
        INBOXES,
        200,
        {"inboxes": [{"inbox_id": "inb_who", "email": OURS}]},
    )
    mb = _box(vault, log, http)
    desc = mb.connect(PRINCIPAL, credential=CANARY)
    assert desc["owned_address"] is True
    assert desc["address"] == OURS
    assert desc["needs_domain"] is False
    assert http.posts == []
    assert len(http.gets) == 1
    cache = identity_home(vault, PRINCIPAL) / "agentmail" / "inbox_id"
    assert cache.read_text(encoding="utf-8") == "inb_who"
    _secret_absent(log)
    assert TAKEN not in str(desc)
    assert f"{PRINCIPAL}@agentmail.to" not in str(desc)


def test_connect_empty_creates_inbox_from_api_address(vault):
    log = MemoryLog()
    http = Http()
    http.on_get(INBOXES, 200, {"inboxes": []})
    http.post_result(
        200,
        {"inbox_id": "inb_new", "email": ISSUED},
    )
    mb = _box(vault, log, http)
    desc = mb.connect(PRINCIPAL, credential=CANARY)
    assert desc["owned_address"] is True
    assert desc["address"] == ISSUED
    assert desc["address"] != f"{PRINCIPAL}@agentmail.to"
    assert len(http.posts) == 1
    url, headers, payload = http.posts[0]
    assert url == INBOXES
    assert headers["Authorization"] == "Bearer " + CANARY
    data = json.loads(payload.decode("utf-8"))
    assert data == {"client_id": "agentself-" + PRINCIPAL}
    assert "username" not in data
    assert "domain" not in data
    cache = identity_home(vault, PRINCIPAL) / "agentmail" / "inbox_id"
    assert cache.read_text(encoding="utf-8") == "inb_new"
    _secret_absent(log)
    _no_local_outbox(vault)


def test_connect_many_inboxes_need_address_no_post(vault):
    log = MemoryLog()
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
    mb = _box(vault, log, http)
    desc = mb.connect(PRINCIPAL, credential=CANARY)
    assert desc["status"] == "input_required"
    assert desc["option"]["name"] == "address"
    assert http.posts == []
    _secret_absent(log)
    assert desc["option"]["choices"] == [TAKEN, OURS]


def test_connect_unauthorized_is_invalid_credentials(vault):
    log = MemoryLog()
    http = Http()
    http.on_get(INBOXES, 401, {"error": "nope"})
    mb = _box(vault, log, http)
    with pytest.raises(MailboxError, match="invalid credentials") as err:
        mb.connect(PRINCIPAL, credential=CANARY)
    assert CANARY not in str(err.value)
    _secret_absent(log, err.value)
    _no_local_outbox(vault)


def test_alias_env_fills_empty_credential(vault, monkeypatch):
    monkeypatch.setenv("AGENTSELF_AGENTMAIL_API_KEY", CANARY)
    log = MemoryLog()
    http = Http()
    inbox_id = "inb_alias"
    http.on_get(
        INBOXES,
        200,
        {"inboxes": [{"inbox_id": inbox_id, "email": OURS}]},
    )
    http.on_get(
        f"{API}/v0/inboxes/{inbox_id}/messages",
        200,
        {"messages": []},
    )
    mb = _box(vault, log, http)
    view = mb.describe(PRINCIPAL, address=OURS)
    assert view["owned_address"] is True
    assert view["address"] == OURS
    mb.send(PRINCIPAL, "a@example.com", "s", "b", address=OURS)
    listed = mb.list(PRINCIPAL, address=OURS)
    assert listed == []
    connected = mb.connect(PRINCIPAL, address=OURS)
    assert connected["owned_address"] is True
    assert connected["address"] == OURS
    assert http.gets
    assert http.posts
    _secret_absent(log)


def test_alias_env_alone_sends_receives_and_lists_through_manager(vault, monkeypatch):
    monkeypatch.setenv("AGENTSELF_EMAIL_ADDRESS", OURS)
    monkeypatch.setenv("AGENTSELF_AGENTMAIL_API_KEY", CANARY)
    monkeypatch.delenv("AGENTSELF_EMAIL_CREDENTIAL", raising=False)
    http = Http()
    inbox_id = "inb_alias_mgr"
    http.on_get(
        INBOXES,
        200,
        {"inboxes": [{"inbox_id": inbox_id, "email": OURS}]},
    )
    http.on_get(
        f"{API}/v0/inboxes/{inbox_id}/messages",
        200,
        {"messages": []},
    )
    app = build_app(vault, email_backend="agentmail")
    init_identity(app, monkeypatch)

    class Factory:
        def for_binding(self, binding: str) -> AgentMailMailboxAccess:
            del binding
            return _box(app.vault, app.log, http)

    app.manager._mailboxes = Factory()
    app.client.email_send("a@example.com", "s", "b")
    assert app.client.email_list() == []
    assert app.client.email_receive() == []
    blob = app.log.rendered() + json.dumps(app.log.records)
    assert CANARY not in blob
    assert http.posts
    assert http.gets


def test_alias_env_rpc_is_not_mapped_to_no_token(vault, monkeypatch):
    monkeypatch.setenv("AGENTSELF_EMAIL_ADDRESS", OURS)
    monkeypatch.setenv("AGENTSELF_AGENTMAIL_API_KEY", CANARY)
    monkeypatch.delenv("AGENTSELF_EMAIL_CREDENTIAL", raising=False)
    http = Http()
    inbox_id = "inb_alias_rpc"
    http.on_get(
        INBOXES,
        200,
        {"inboxes": [{"inbox_id": inbox_id, "email": OURS}]},
    )
    http.get_raises(
        f"{API}/v0/inboxes/{inbox_id}/messages",
        TimeoutError("timeout"),
    )
    app = build_app(vault, email_backend="agentmail")
    init_identity(app, monkeypatch)

    class Factory:
        def for_binding(self, binding: str) -> AgentMailMailboxAccess:
            del binding
            return _box(app.vault, app.log, http)

    app.manager._mailboxes = Factory()
    with pytest.raises(ChannelFailure) as caught:
        app.client.email_list()
    assert caught.value.reason == "rpc"


def test_connect_create_rpc_failed(vault):
    log = MemoryLog()
    http = Http()
    http.on_get(INBOXES, 200, {"inboxes": []})
    http.post_result(500, {"error": "nope"})
    mb = _box(vault, log, http)
    with pytest.raises(MailboxError, match="rpc failed") as err:
        mb.connect(PRINCIPAL, credential=CANARY)
    _secret_absent(log, err.value)
    _no_local_outbox(vault)
    _no_local_outbox(vault)


def test_authorized_signup_verifies_otp_and_returns_private_key(vault):
    log = MemoryLog()
    http = Http()
    inbox_id = "inb_signed_up"
    http.on_post(
        SIGN_UP,
        200,
        {
            "organization_id": "org_new",
            "inbox_id": inbox_id,
            "api_key": CANARY,
        },
    )
    http.on_post(VERIFY, 400, {"error": "invalid otp"})
    http.on_post(VERIFY, 200, {"verified": True})
    http.on_get(
        INBOXES,
        200,
        {"inboxes": [{"inbox_id": inbox_id, "email": ISSUED}]},
    )
    mb = _box(vault, log, http)

    email_step = mb.connect(
        PRINCIPAL,
        answers={"setup_method": "create_account"},
        state={"phase": "setup_method"},
    )
    assert email_step["option"]["name"] == "human_email"
    otp_step = mb.connect(
        PRINCIPAL,
        answers={"human_email": "owner@example.com"},
        state=email_step["continuation"],
    )
    assert otp_step["option"]["name"] == "otp"
    assert otp_step["option"]["sensitive"] is True
    assert len(http.posts) == 1
    signup_url, signup_headers, signup_body = http.posts[0]
    assert signup_url == SIGN_UP
    assert "Authorization" not in signup_headers
    signup = json.loads(signup_body)
    assert signup["human_email"] == "owner@example.com"
    assert signup["source"] == "agentself"
    assert signup["username"].startswith(PRINCIPAL + "-")
    assert len(signup["username"].rsplit("-", 1)[1]) == 16

    malformed = mb.connect(
        PRINCIPAL,
        answers={"otp": "12345"},
        state=otp_step["continuation"],
    )
    assert malformed["option"]["name"] == "otp"
    assert len(http.posts) == 1
    rejected = mb.connect(
        PRINCIPAL,
        answers={"otp": "123456"},
        state=malformed["continuation"],
    )
    assert rejected["option"]["name"] == "otp"
    assert "try again" in rejected["message"].lower()
    connected = mb.connect(
        PRINCIPAL,
        answers={"otp": "654321"},
        state=rejected["continuation"],
    )
    assert connected["address"] == ISSUED
    assert connected["owned_address"] is True
    assert connected["private_outputs"] == {"credential": CANARY}
    verify_posts = [item for item in http.posts if item[0] == VERIFY]
    assert [json.loads(item[2])["otp_code"] for item in verify_posts] == [
        "123456",
        "654321",
    ]
    assert all(item[1]["Authorization"] == "Bearer " + CANARY for item in verify_posts)
    _secret_absent(log)


@pytest.mark.parametrize("status", [400, 403, 409, 422])
def test_signup_unavailable_stops_without_alias_probe(vault, status):
    log = MemoryLog()
    http = Http()
    http.on_post(SIGN_UP, status, {"error": "unavailable"})
    mb = _box(vault, log, http)
    result = mb.connect(
        PRINCIPAL,
        answers={"human_email": "owner@example.com"},
        state={"phase": "create_account"},
    )
    assert result["status"] == "failed"
    assert result["reason"] == "signup unavailable; connect with existing_credential"
    assert len(http.posts) == 1
    body = json.loads(http.posts[0][2])
    assert set(body) == {"human_email", "username", "source"}
    assert http.gets == []
    assert VERIFY not in [item[0] for item in http.posts]
