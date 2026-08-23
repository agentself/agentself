# agentself

`agentself` is a Python 3.11+ CLI that gives an agent a persistent local
identity (wallet, encrypted secrets, and optional email). The entry point is
`agentself.__main__:run`; backends live under
`agentself/backends/<channel>/<name>/`.

The package, tests, and release metadata live together in this repository.
Use `pyproject.toml`, `README.md`, `CONTRIBUTING.md`, and
`.github/workflows/` as the source of truth for project commands and CI.

## Development environment

- The CLI and dev tools can be installed with `uv sync`, or with a user pip
  install when `uv` is unavailable.
- `age` and `sops` are installed by `agentself install --tools`.
- Use `python -m pytest` (or `pytest`) for the test suite.
- Match CI lint checks with `python -m ruff check .`,
  `python -m ruff format --check .`, and `python -m mypy agentself`.

The tests set `AGENTSELF_FETCH_TOOLS=0`; host tools therefore need to be
installed before running tests. The `pass` backend also needs `gpg` and
`pass`. On Windows, use the project-provided test setup or a short temporary
`GNUPGHOME` when gpg-agent socket paths would otherwise be too long.

## Running the CLI

Use an isolated identity directory so local runs do not touch the default
`~/.agentself`:

```text
AGENTSELF_VAULT_ROOT=/tmp/demo agentself init
```

The default backends are wallet `base`, store `sops`, and email `agentmail`.
Use `agentself backends` for the current catalog and setup requirements.
