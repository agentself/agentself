# Changelog

## Unreleased

- Internal Python names match the public CLI (`Client`, `init`, `create`,
  `get`, `update`, `wallet authorize`, `email receive`). Operations always
  use the bound identity.

## 0.1.0a3

First public alpha.

- The CLI is provider-neutral: discover available backends and setup inputs
  with `agentself backends` while keeping the command tree stable.
- `--json` is the first public machine-readable contract (`cli: 1`). It emits
  one JSON object per command; consumers should ignore unknown fields.
- A persistent identity contains the wallet, encrypted secrets, optional email
  state, configuration, and versioned saved metadata. The current saved format
  is `format_version` 1.
- `backup` and `restore` copy a complete identity for same-identity migration.
- The package and release metadata use the PEP 440 version `0.1.0a3`.
