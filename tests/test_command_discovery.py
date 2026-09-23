"""Targeted command discovery stays static and matches the parser."""

from __future__ import annotations

import json

from agentself.cli.parser import _parser
from agentself.cli.registry import commands_payload, lookup_command

from tests.support import cli_env, run_cli


def test_group_and_verb_schemas_are_smaller_than_the_catalog(tmp_path, capsys):
    env = cli_env(tmp_path / "missing")
    full = json.loads(run_cli(["commands"], env).stdout)
    group = json.loads(run_cli(["commands", "wallet"], env).stdout)
    verb = json.loads(run_cli(["commands", "wallet", "authorize"], env).stdout)
    assert group["ok"] is True
    assert verb["ok"] is True
    assert group["group"] == "wallet"
    assert verb["command"] == "agentself wallet authorize"
    full_bytes = len(json.dumps(full).encode("utf-8"))
    verb_bytes = len(json.dumps(verb).encode("utf-8"))
    group_bytes = len(json.dumps(group).encode("utf-8"))
    assert verb_bytes < group_bytes < full_bytes
    assert "authorization" in json.dumps(verb["params"])
    blob = json.dumps(verb)
    assert "AGE-SECRET-KEY" not in blob
    assert "age1" not in blob
    names = {item["name"] for item in verb["params"]}
    assert {"MESSAGE", "--file", "--out", "--force"} <= names
    alternatives = [tuple(item["params"]) for item in verb["alternatives"]]
    assert ("MESSAGE", "--file") in alternatives
    assert any("--out cannot be -" in item for item in verb["constraints"])
    global_names = {item["name"] for item in verb["globals"]}
    assert "--identity-dir" in global_names
    assert "--raw" in global_names
    assert "--json" not in global_names
    capsys.readouterr()


def test_secret_get_conflicts_match_the_parser(tmp_path):
    env = cli_env(tmp_path / "missing")
    data = json.loads(run_cli(["commands", "secret", "get"], env).stdout)
    conflicts = [tuple(item["params"]) for item in data["conflicts"]]
    assert ("--file", "--meta") in conflicts
    refused = run_cli(["secret", "get", "NAME", "--file", "out", "--meta"], env)
    assert refused.returncode == 2
    parsed = _parser().parse_args(["secret", "get", "NAME", "--file", "kept"])
    assert parsed.to_file == "kept"
    assert parsed.meta is False


def test_file_and_positional_are_alternatives_the_parser_still_accepts(tmp_path):
    env = cli_env(tmp_path / "missing")
    found = lookup_command("wallet", "authorize")
    assert found.payload is not None
    parsed = _parser().parse_args(
        ["wallet", "authorize", "hello", "--file", "statement.txt"]
    )
    assert parsed.message == "hello"
    assert parsed.from_file == "statement.txt"
    refused = run_cli(["wallet", "authorize", "hello", "--file", "statement.txt"], env)
    assert refused.returncode == 2
    assert json.loads(refused.stdout)["reason"] == "message and --file"


def test_unknown_command_path_is_a_structured_error(tmp_path):
    env = cli_env(tmp_path / "missing")
    missing_group = run_cli(["commands", "nope"], env)
    assert missing_group.returncode == 2
    body = json.loads(missing_group.stdout)
    assert body["error"] == "refused"
    assert body["reason"] == "unknown command"
    assert body["next"] == "agentself commands"
    missing_verb = run_cli(["commands", "wallet", "nope"], env)
    assert missing_verb.returncode == 2
    verb_body = json.loads(missing_verb.stdout)
    assert verb_body["reason"] == "unknown command"
    assert verb_body["next"] == "agentself commands wallet"


def test_targeted_discovery_skips_identity_runtime(tmp_path):
    from tests.test_startup_imports import _runtime

    env = cli_env(tmp_path / "vault")
    started = run_cli(["init"], env)
    assert started.returncode == 0, started.stderr
    script = (
        "from agentself.cli.app import main\n"
        "assert main(['commands', 'wallet', 'authorize']) == 0\n"
    )
    assert _runtime(script, env) == []


def test_unfiltered_catalog_shape_is_unchanged():
    data = commands_payload()
    for item in data["commands"]:
        assert {"name", "args", "next"} <= set(item)
        extra = set(item) - {"name", "args", "next", "params", "verbs"}
        assert not extra
    wallet = next(item for item in data["commands"] if item["name"] == "wallet")
    authorize = next(verb for verb in wallet["verbs"] if verb["name"] == "authorize")
    assert "constraints" not in authorize
    assert "globals" not in authorize
    commands = next(item for item in data["commands"] if item["name"] == "commands")
    assert [item["name"] for item in commands["params"]] == ["GROUP", "VERB"]
