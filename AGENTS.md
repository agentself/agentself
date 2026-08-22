# agentself

`agentself` is a Python 3.11+ CLI that gives an agent a persistent local identity
(wallet, encrypted secrets, and optional email). Entry point is
`agentself.__main__:run`; backends live under `agentself/backends/<channel>/<name>/`.

Standard commands and config live in `pyproject.toml`, `README.md` (see the
"Development" section), `CONTRIBUTING.md`, and `.github/workflows/test.yml`.
Prefer those sources for anything not called out below.

## Cursor Cloud specific instructions

### Layout note
The real project (the `agentself` package, `tests/`, `pyproject.toml`) lives on the
`test-ci` branch and branches based on it. The `main` branch is only a GitHub
profile `README.md` and has no application code, so environment/dev work must be
done on a branch that contains the package.

### Where things are installed / PATH
- The `agentself` CLI and dev tools (`pytest`, `mypy`, `ruff`) are installed as a
  pip user install into `~/.local/bin`.
- Host tools `age` and `sops` are installed by `agentself install --tools` into
  `~/.local/share/agentself/bin`.
- Both directories are added to `PATH` in `~/.bashrc`. If a tool is "not found",
  re-source `~/.bashrc` or invoke via `python3 -m` (e.g. `python3 -m pytest`,
  `python3 -m ruff`, `python3 -m mypy`, `python3 -m agentself`).

### Running the test suite (important gotchas)
- Run with `python3 -m pytest` (or `pytest`).
- The suite sets `AGENTSELF_FETCH_TOOLS=0`, so it does NOT download host tools;
  `age`, `sops`, `pass`, and `gpg` must already be on `PATH`. `pass` and `gnupg`
  come from apt; `age`/`sops` come from `agentself install --tools`.
- The `pass` backend keeps keys in the vault `gnupg/` dir but points
  `GNUPGHOME` at a short `/tmp/as-gpg-*` symlink so gpg-agent sockets fit the
  unix sockaddr limit. Long `AGENTSELF_VAULT_ROOT` / pytest temp paths should
  work without a `--basetemp` workaround.

### Lint (matches the CI `lint` job)
`ruff` is in `[dependency-groups] dev` alongside `pytest` and `mypy`.
- `python3 -m ruff check .`
- `python3 -m ruff format --check .`
- `python3 -m mypy agentself`

### Running the CLI
Use an isolated identity directory via `AGENTSELF_VAULT_ROOT` so runs do not touch
`~/.agentself`, e.g. `AGENTSELF_VAULT_ROOT=/tmp/demo agentself init`. Default
backends are wallet `base`, store `sops`, email `agentmail`. `sops` needs `age`;
`--store pass` needs `gpg` + `pass`.

### uv note
`README.md` documents a `uv sync` / `uv run` dev flow. This environment uses a
pip user install instead; both are valid. There is no committed `uv.lock`.
