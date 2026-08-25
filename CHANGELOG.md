# Changelog

## Unreleased

- Pull-request CI skips macOS and the extra Windows Python, cancels superseded
  runs, and caches pip. Host-tool checksums must be exactly 64 hex digits so a
  macOS digest typo fails on Linux. The full OS matrix still runs on `main`
  and tags.
- `init --wallet-key-file PATH` seals an existing hex key as `wallet.key`
  on first init. Replacing it on an existing identity still needs `--unsafe`.
- `secret create --from-dir DIR` and repeated `--from-files NAME=PATH`
  import many secrets. JSON reports `created`, `unchanged`, and `refused`
  names only. `wallet.key` is refused without `--unsafe`.
- Default `--json backends` is a compact catalog. Option essays are on
  `backends CHANNEL BACKEND`. `backends CHANNEL` lists backends without
  option help.
- `agentself --json commands` lists featured verbs with `name`, `args`,
  and `next`.
- `email connect --json` next-step objects are compact: `status`, option
  `name`, `choices` when present, and `next`.
- Skill and `wallet authorize --help` state that signing is
  `wallet authorize --file PATH` and that the output is the signature
  to attach, not a send.

## 0.1.0 - 2026-08-24

First stable release. Public CLI commands and flags, documented
`AGENTSELF_*` variables, `--json` schema `cli: 1`, and identity
`format_version` 1 are now compatibility promises (see
COMPATIBILITY.md).

- `install --skills` JSON `paths` and human `installed` lines list every
  copied skill file.
- Unknown compact mail refs point `next` at `agentself email list`.
- Human `secret get --print` and `note get` write a trailing LF;
  `--file` stays byte-exact.
- `email connect` does not persist continuation until setup needs opaque
  resume state.

## 0.1.0a5 - 2026-08-24

- Added resumable AgentMail signup, acted mail state, header-only search, and
  stable compact message refs.
- Added printable identity notes and workflow-focused skill references for
  cross-agent handoff.
- Preserved safe secret/mail defaults and normalized cross-platform plaintext
  line endings.

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
