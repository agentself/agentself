# Changelog

## Unreleased

- `backup`/`restore` copy to a staging directory first, take the identity lock, refuse a destination that contains the live identity, skip plaintext `*.tmp` leftovers and sockets, and require a `config.json` source so `--force` cannot wipe an identity on a failed or empty copy.
- `secret get --file` writes `0o600` without chmod'ing the parent directory.
- `secret update` of `wallet.key` (and other protected material) requires `--unsafe`.
- `--json init` no longer prompts for an identity name on a TTY.

## 0.1.0a3 - 2026-08-23

First public alpha.

- Linux, macOS, and Windows. Python 3.11+.
- Default backends: wallet `base`, store `sops`, email `agentmail`.
- The CLI is provider-neutral: discover available backends and setup inputs
  with `agentself backends` while keeping the command tree stable.
- `--json` is the first public machine-readable contract (`cli: 1`). It emits
  one JSON object per command; consumers should ignore unknown fields.
- A persistent identity contains the wallet, encrypted secrets, optional email
  state, configuration, and versioned saved metadata. The current saved format
  is `format_version` 1.
- `backup` and `restore` copy a complete identity for same-identity migration.
- The package and release metadata use the PEP 440 version `0.1.0a3`.
