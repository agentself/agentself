---
name: agentself
description: Manage a local agent identity with a wallet, encrypted secrets, notes, optional email, and recovery. Use for agentself setup, identity handoff, secret or note storage, wallet authorization, email setup or processing, and backup or restore.
allowed-tools: Bash(agentself:*)
---

# agentself

Use the `agentself` CLI to keep one local identity across agent tasks. It stores a
wallet, encrypted secrets, non-secret notes, optional email credentials, and
local mail state. It has no MCP server.

## Check the CLI first

```bash
agentself --version
agentself diagnose
```

Continue only when the version JSON includes `"ok": true` and `"cli": 2`.
Check the `package` and `executable` paths too. A different CLI schema or an
unexpected path means the install is stale. Stop, install the intended CLI,
reinstall its packaged skill, and run the check again. Never mix instructions
from one schema with another executable.

If `agentself` is missing, install it with `uv tool install agentself` or
`pipx install agentself`. If it is still missing from PATH, run
`uv tool update-shell` and open a new shell. Then run
`agentself install --tools` before `init`, and run both checks again.

The CLI writes one JSON object to stdout by default. Handled outcomes write
nothing to stderr. Exit codes are:

- `0`: success
- `1`: error, including missing host tools
- `2`: refusal
- `3`: missing input or resource

Failures include `"error"`, `"reason"`, and `"next"`. Ignore unknown JSON keys.
When a host tool is missing, run `agentself install --tools`.

Prefer `--identity-dir PATH` for one invocation. The CLI uses the flag, then
`AGENTSELF_IDENTITY_DIR`, then `~/.agentself`. The flag is not persisted and
does not create a current-identity pointer.

Use `--raw` only when a caller needs exact bytes from `wallet address`,
`wallet show`, `wallet authorize`, `secret get NAME`, `note get NAME`, or
`email receive REF`. Other `--raw` uses return a JSON refusal with exit code 2.

When a flag or next step is unclear, run `agentself commands` or open the
matching reference below. Run `agentself backends CHANNEL BACKEND` when you
need backend setup options.

## Pass input safely

Pass input explicitly with `--file -`, `--result-file -`, or
`--wallet-key-file -` when the input must come from stdin. Stdin is never
implicit. A missing explicit input returns JSON with exit code 3.

Keep credentials, secret values, OTPs, private keys, and mail bodies out of
command arguments, logs, chat, and `*.notes` files.

## Common path

```bash
agentself install --tools
agentself --identity-dir PATH init
agentself --identity-dir PATH show
```

The setup is complete when `show` JSON includes `id`, `address`, and
`recipient`. The first wallet demo is `wallet authorize --file` /
`wallet verify` on this identity; do not send. `init` and `diagnose` do not
fetch binaries. Repeating `init` is safe. Use `--force` to change an existing
identity or its backends.
`AGENTSELF_FETCH_TOOLS=0` refuses a fetch even for `--tools`.

`show` includes the age recipient and email readiness. For wallet work, inspect
`agentself backends wallet` first. The default Base wallet is live and can move
real funds. `wallet balance` reports the current amount. It does not identify
who paid or when.

Authorize with the existing identity:

```bash
agentself wallet authorize --file PATH --out PATH
```

Write the exact message bytes to `PATH`. Statement files keep trailing
newlines. Never put the message on argv. The JSON field `message_sha256`
identifies the SHA-256 of that exact decoded statement.

The CLI treats a typed statement with `domain`, `types`, and `message` as typed
data. It treats other files, including login text, as personal signatures.
Login text uses this same verb and identity. Do not create another wallet.

Prefer this command when you need to check an authorization:

```bash
agentself wallet verify --file PATH --authorization-file PATH
```

This keeps the authorization token out of command arguments. CLI 2 also accepts
positional `MESSAGE` and JSON `authorization`. The CLI refuses `--out -`. Use
`--raw` for stdout transport. Output is JSON by default. Keep signatures and
secrets out of chat and logs. There is no Python compose or SDK path.

List secret names with:

```bash
agentself secret list
```

The list contains names, not values. `store` names the configured store
backend, such as `sops` or `pass`. It is not a command.

Use files for secrets:

```bash
agentself secret create NAME --file PATH
agentself secret get NAME --file PATH
```

Default `secret get NAME` returns JSON with the value. Use `--raw` for exact
bytes. `wallet.key` also needs `--unsafe`.

Use notes for printable, non-secret context:

```bash
agentself note set NAME --file PATH
agentself note get NAME
```

Use `--raw` when a caller needs exact note bytes. Never put credentials, OTPs,
private keys, secret values, or mail bodies in a note.

## Open the relevant workflow

- Email setup or recovery: [references/email-connect.md](references/email-connect.md)
- Identity continuity, handoff, backup, or restore: [references/handoff.md](references/handoff.md)
- Mail listing, reading, filtering, or completion: [references/mail.md](references/mail.md)

Open the matching reference before a multi-step task.

## Install the skill

```bash
agentself install --skills
agentself install --skills -g
agentself install --skills=agents
```

`install --skills` copies this complete skill tree into the current workspace.
`-g` copies it into the user skill directory. Neither path is the identity
directory.
