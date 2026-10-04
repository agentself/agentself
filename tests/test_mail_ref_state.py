from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
from pathlib import Path

import pytest

from agentself.internal.files import identity_home
from agentself.internal.mail_state import MailRefCollision, MailRefState


def _seed(root, mappings):
    folder = identity_home(root, "agent") / "email" / "refs"
    folder.mkdir(parents=True, exist_ok=True)
    for ref, message_id in mappings.items():
        (folder / ref).write_text(message_id, encoding="utf-8")
    return folder


def test_batch_reads_saved_refs_once_and_reuses_new_refs(tmp_path, monkeypatch):
    folder = _seed(tmp_path, {"m1": "old", "m7": "latest", "ignored": "\n"})
    reads = []
    original = Path.read_text

    def read(path, *args, **kwargs):
        reads.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    messages = [{"id": item} for item in ["old", "new", "latest", "new", "next"]]
    state = MailRefState(tmp_path)
    assert state.apply("agent", messages) is messages
    assert [item["ref"] for item in messages] == ["m1", "m8", "m7", "m8", "m9"]
    assert sorted(path.name for path in reads) == ["m1", "m7"]
    assert all(path.parent == folder for path in reads)
    assert state.apply("agent", [{"id": "new"}])[0]["ref"] == "m8"
    assert state.apply("other", [{"id": "new"}])[0]["ref"] == "m1"


def test_lookup_refreshes_between_calls_and_instances(tmp_path):
    state = MailRefState(tmp_path)
    assert state.apply("agent", [{"id": "first"}])[0]["ref"] == "m1"
    assert MailRefState(tmp_path).remember("agent", "second") == "m2"
    assert state.apply("agent", [{"id": "second"}, {"id": "third"}]) == [
        {"id": "second", "ref": "m2"},
        {"id": "third", "ref": "m3"},
    ]
    folder = _seed(tmp_path, {"m4": "first"})
    with pytest.raises(MailRefCollision):
        state.apply("agent", [{"id": "first"}])
    (folder / "m4").write_text("\n", encoding="utf-8")
    with pytest.raises(OSError, match="invalid mail ref mapping"):
        state.apply("agent", [{"id": "third"}])


def test_duplicate_mapping_only_collides_when_requested(tmp_path):
    folder = _seed(tmp_path, {"m2": "duplicate", "m4": "duplicate"})
    messages = [{"id": "new"}, {"id": "duplicate"}, {"id": "later"}]
    with pytest.raises(MailRefCollision):
        MailRefState(tmp_path).apply("agent", messages)
    assert messages == [
        {"id": "new", "ref": "m5"},
        {"id": "duplicate"},
        {"id": "later"},
    ]
    assert {p.name: p.read_text() for p in folder.iterdir()} == {
        "m2": "duplicate",
        "m4": "duplicate",
        "m5": "new",
    }


@pytest.mark.parametrize("invalid", ["", "bad\n", "é" * 2049])
def test_corrupt_mapping_refuses_batch_before_any_write(tmp_path, invalid):
    folder = _seed(tmp_path, {"m1": "existing", "m2": invalid})
    with pytest.raises(OSError, match="invalid mail ref mapping"):
        MailRefState(tmp_path).apply("agent", [{"id": "existing"}, {"id": "new"}])
    assert sorted(p.name for p in folder.iterdir()) == ["m1", "m2"]


@pytest.mark.parametrize("invalid", ["bad\x00", "é" * 2049])
def test_invalid_input_preserves_prior_batch_progress(tmp_path, invalid):
    messages = [{"id": "valid"}, {"id": invalid}, {"id": "later"}]
    state = MailRefState(tmp_path)
    with pytest.raises(ValueError, match="invalid provider message id"):
        state.apply("agent", messages)
    assert messages[0]["ref"] == "m1"
    assert "ref" not in messages[1] and "ref" not in messages[2]
    assert state.remember("agent", "later") == "m2"


def test_empty_ids_do_not_load_or_validate_storage(tmp_path):
    _seed(tmp_path, {"m1": "\n"})
    state = MailRefState(tmp_path)
    assert state.apply("agent", []) == []
    assert state.apply("../unsafe", [{}, {"id": ""}]) == [{}, {"id": ""}]
    with pytest.raises(ValueError):
        state.apply("../unsafe", [{"id": "valid"}])


@pytest.mark.parametrize("level", ["home", "email", "refs", "mapping"])
@pytest.mark.parametrize("linked", [False, True])
def test_batch_refuses_unsafe_storage(tmp_path, level, linked):
    home = identity_home(tmp_path, "agent")
    folder = home / "email" / "refs"
    path = {
        "home": home,
        "email": home / "email",
        "refs": folder,
        "mapping": folder / "m1",
    }[level]
    path.parent.mkdir(parents=True)
    if linked:
        target = tmp_path / "outside"
        target.mkdir()
        try:
            path.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"directory symlinks unavailable: {exc}")
    elif level == "mapping":
        path.mkdir()
    else:
        path.write_text("not a directory")
    with pytest.raises(OSError, match="unsafe mail"):
        MailRefState(tmp_path).apply("agent", [{"id": "new"}])


def test_batch_refuses_exhausted_ref_space(tmp_path):
    _seed(tmp_path, {"m999999999999": "last"})
    messages = [{"id": "last"}, {"id": "new"}]
    with pytest.raises(OSError, match="mail ref space exhausted"):
        MailRefState(tmp_path).apply("agent", messages)
    assert messages[0]["ref"] == "m999999999999"


def _concurrent_batch(root, index):
    state = MailRefState(root)
    messages = [{"id": f"provider/{n}"} for n in range(index * 20, index * 20 + 50)]
    first = state.apply("agent", messages)
    assert state.apply("agent", [{"id": item["id"]} for item in first]) == first
    return first


def test_concurrent_processes_share_stable_sequential_refs(tmp_path):
    _seed(tmp_path, {"m1": "preexisting"})
    with ProcessPoolExecutor(max_workers=4, mp_context=get_context("spawn")) as pool:
        futures = [pool.submit(_concurrent_batch, tmp_path, i) for i in range(4)]
        batches = [future.result(timeout=60) for future in futures]
    refs = {"preexisting": "m1"}
    for batch in batches:
        for item in batch:
            assert refs.setdefault(item["id"], item["ref"]) == item["ref"]
    assert len(refs) == 111
    assert set(refs.values()) == {f"m{n}" for n in range(1, 112)}
    state = MailRefState(tmp_path)
    for message_id, ref in refs.items():
        assert state.resolve("agent", ref) == message_id
