# Changelog

## Unreleased

- README has a short copy-paste prompt so an agent can install the CLI,
  install the skill, and create a local identity.

- `wallet authorize` and `wallet verify` accept a typed statement
  (`domain`, `types`, `message`) as typed data. Other files stay a
  personal signature. Login text uses the same verb. The JSON `scheme`
  names the encoding used for that statement.
- `--identity-dir PATH` selects the identity for one invocation. It is
  not persisted. Precedence is the flag, then `AGENTSELF_IDENTITY_DIR`,
  then `~/.agentself`.
- `wallet authorize --out PATH` writes the exact token privately and
  returns metadata without `authorization`. `--out -` is refused; use
  `--raw`. Prefer `wallet verify --authorization-file PATH`. Positional
  MESSAGE and JSON `authorization` remain for CLI 2.
- `email receive` without a ref is a repeatable, non-consuming
  new-header check. Explicit refs keep the consuming receive.
- Invalid `init --id` names the identity-id character rule.
- Failed email setup adds `message`, `retryable`, and optional `option`.
  AgentMail signup failures recover with `agentself email connect`.

## 0.2.1 - 2026-08-25

- Backend discovery names each canonical command group and derives its verbs
  from the command registry. Mistaken backend commands and known legacy terms
  return safe exact `next` steps without suggesting mutating commands.
- Unexpected CLI failures stay generic (`next: agentself diagnose`). Diagnostics
  record only a bounded operation label and exception type.

## 0.2.0 - 2026-08-25

0.2.0 is a CLI break (`cli: 2`). Commands print one JSON object. Exit 0
success, 1 error, 2 refused, 3 missing. Only `--help` is text. `--json` is
a hidden no-op. Identity `format_version` stays 1.

- `--raw` writes exact command output bytes for `wallet address`,
  `wallet show`, `wallet authorize`, `secret get`, `note get`, and
  `email receive`. Unsupported `--raw` is a JSON refusal, exit 2.
- Stdin is never implicit. Use `--file -`, `--result-file -`, or
  `--wallet-key-file -`. Missing explicit input is JSON, exit 3.
- `email connect` never prompts. Continue with
  `--continue --state STATE --result-file PATH`.
- `init --wallet-key-file PATH` seals an existing hex key on first init.
- `secret create --from-dir DIR` and repeated `--from-files NAME=PATH`
  import many secrets. `wallet.key` is refused without `--unsafe`. Named
  `secret create NAME` refuses if the name exists; use `secret update`.
- `email mark` refuses unknown compact refs and provider IDs that
  `list`/`receive` have not stored.
- `backends` and `commands` are compact catalogs. Option essays are on
  `backends CHANNEL BACKEND`.

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
