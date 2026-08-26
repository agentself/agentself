---
name: agentself
description: Local agent identity — wallet, secrets, optional email. Use when asked to initialize or hand off an identity, use its wallet or secrets, connect email, or process mail. Open the matching reference for multi-step work.
allowed-tools: Bash(agentself:*)
---

# agentself

CLI identity for an agent. No MCP server.

Requires the `agentself` CLI. If unavailable, install it with `uv tool install agentself` or `pipx install agentself`, then run the version check below.

## Start safely

Do not guess flags or expose values in logs, arguments, or chat.

```bash
agentself --version
agentself diagnose
```

Done when `--version` JSON has `"ok": true` and `"cli": 2`. A different `cli` or an unexpected `package` / `executable` path means the install is stale: stop, install the intended CLI, reinstall its packaged skill, and check again. Never mix instructions from one schema with an executable from another.

Default output is one JSON object on stdout. Exit 0 success, 1 error (including missing host tools; recover with `agentself install --tools`), 2 refused, 3 missing input or resource. stderr is empty for handled outcomes. Failures include `"error"`, `"reason"`, and `"next"`. Ignore unknown keys.

Use `--raw` when a caller needs exact bytes from `wallet address`, `wallet show`, `wallet authorize`, `secret get NAME`, `note get NAME`, or `email receive REF`. Unsupported `--raw` is JSON refusal, exit 2.

When a flag or next step is unclear, run `agentself commands` or open the matching reference below. Drill in with `agentself backends CHANNEL BACKEND` only when you need setup options.

Identity directory is `AGENTSELF_IDENTITY_DIR` (default `~/.agentself`).

Stdin is never implicit. Use `--file -`, `--result-file -`, or `--wallet-key-file -`. Missing explicit input is JSON, exit 3.

## Common path

```bash
agentself install --tools
agentself init
agentself show
```

Done when `show` JSON includes `id`, `address`, and `recipient`. `init` and `diagnose` do not fetch binaries. Missing host tools: `next: agentself install --tools`. `AGENTSELF_FETCH_TOOLS=0` refuses a fetch even for `--tools`. Repeating init is safe; identity or backend changes need `--force`.

`show` includes the age recipient and email readiness. For wallet work, inspect `agentself backends wallet`; the default Base wallet is live and can move real funds. `wallet balance` is the current amount. It does not name who paid or when.

Signing is `agentself wallet authorize --file PATH`. Put the message bytes in that file; never put the message on argv. A typed statement (`domain`, `types`, `message`) is authorized as typed data; other files stay a personal signature. Login text uses this same verb and this identity. Do not create another wallet. Output is JSON by default; `--raw` emits the signature or authorization token. Do not dump signatures or secrets in chat or logs. Existing identities use this same verb; there is no Python compose or SDK path.

List secret names with `agentself secret list`. Names only; never values. `store` is the backend (sops/pass), not a verb.

For secrets, use files: `secret create NAME --file PATH` and `secret get NAME --file PATH`. Default `secret get NAME` is JSON with the value. Use `--raw` when exact bytes are required. `wallet.key` also needs `--unsafe`.

For printable cross-agent context, use `note set NAME --file PATH` and
`note get NAME` (JSON) or `--raw`. Notes are non-secret: never put credentials, OTPs, private
keys, secret values, or mail bodies in them.

## Open the relevant workflow

- Connecting or recovering email setup: [references/email-connect.md](references/email-connect.md)
- Identity sharing, isolation, continuity, interruption, backup, or restore: [references/handoff.md](references/handoff.md)
- Listing, fetching, filtering, and completing mail work: [references/mail.md](references/mail.md)

## Skills

```bash
agentself install --skills
agentself install --skills -g
agentself install --skills=agents
```

Skills install this complete skill tree into the current directory unless `-g`.
