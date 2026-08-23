# Changelog

## Unreleased

- `sops` encrypt no longer inherits parent `SOPS_AGE_RECIPIENTS` / `SOPS_AGE_KEY` / `AGE_SECRET_KEY` (or other sops master-key env vars), so secrets are not encrypted to extra recipients and parent keys stay out of the child environment.
- POSIX `pass` GNUPGHOME short-links under `/tmp` are reused only when owned by the current user, so an attacker-owned `/tmp/as-gpg-*` symlink is not trusted.
- Identity `config.json` / `registry.json` (and wallet pending JSON) ignore a UTF-8 BOM so Windows Notepad edits remain readable.
- Identity ids and secret names reject Windows reserved device names (`CON`, `NUL`, `COM1`, …) so the same identity is portable.
- On Windows, `age-keygen`, `gpg`, and `pass` are started with
  `NoDefaultCurrentDirectoryInExePath=1`. `diagnose` and `init` do not treat a
  current-directory binary as an installed host tool.
- Tag publish runs the test workflow for that commit and publishes the
  already-tested `dist/`. It does not rebuild an untested wheel. Trusted
  Publishing (OIDC) is unchanged.
- TestPyPI publish no longer uses `skip-existing`, so a tag rebuild cannot keep an old TestPyPI artifact while uploading a new wheel to PyPI.
- After a confirmed `wallet send`, a later send of the same destination and
  amount is a new payment. Crash and timeout retries before confirmation still
  reuse the pending transaction. Successful send output includes the
  transaction hash.

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
