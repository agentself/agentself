# Changelog

## Unreleased

## 0.1.0a5 - 2026-08-24

- Mail list/receive results now include stable compact refs backed by private,
  identity-scoped state; receive and mark accept either a ref or provider ID.
- `email find` searches From, To, and Subject using header-only mailbox listing,
  with the existing read-state and acted-state filters.
- Added identity-local, non-secret notes for printable cross-agent handoff
  context. `note set` is an idempotent upsert, and backup/restore carries notes.
- AgentMail setup now offers an existing-key route and an explicitly authorized,
  resumable signup/OTP route without exposing the generated key.
- Email messages have independent local acted state, with mark/unmark and safe
  list filters; provider `new`/`seen` status is unchanged.
- The installed agent skill uses workflow references for email setup, mail
  processing, and cross-agent identity handoff.
- Human `secret get --print` emits one trailing newline instead of adding a
  second newline to values that already end with one.

## 0.1.0a4 - 2026-08-23

- Safer backup/restore, secret files, `wallet.key` updates, sops encrypt, and Windows host-tool lookup.
- Confirmed wallet sends are new payments. Output includes the hash. Amounts and RPC results are checked.
- `email receive` is headers-only by default. Remote mailbox reads are bounded.
- Plaintext secret stdout requires `--print`. Secret `--file` input drops a UTF-8 BOM.
- Tag publish reuses the tested `dist/` and fails if TestPyPI already has that version.

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
