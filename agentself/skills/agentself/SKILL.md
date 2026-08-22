---
name: agentself
description: Local agent identity — wallet, secrets, optional email. Use when asked to init identity, show the wallet address, store or get a secret, send USDC, connect email, diagnose the host, or copy the identity directory. Discover with agentself --help. Prefer --json.
allowed-tools: Bash(agentself:*)
---

# agentself

CLI identity for an agent. No MCP server. Use the shell.

## When to use

Use this CLI when a task needs a local wallet, named secrets, or optional email on the host. `--help` and `--json` are enough without installing a skill.

## Discover

Do not guess flags.

```bash
agentself --help
agentself <command> --help
agentself backends
agentself diagnose
```

Public commands: `init`, `show`, `backends`, `diagnose`, `secret`, `email`, `wallet`, `backup`, `restore`, `install`.

Prefer `--json`. Success is one JSON object on stdout with `"ok": true`. Failure is one JSON object on stderr with `"ok": false`, `"error"`, `"reason"`, and `"next"`. Human errors may include a `next:` line. `agentself --json --version` includes `cli` (machine schema id, currently 2). Ignore unknown keys.

No command prints the current identity (`show`). `--json show` includes `recipient` (age pubkey). Email without a token is not ready, not broken.

Identity directory is `AGENTSELF_VAULT_ROOT` (default `~/.agentself`).

## Host tools, then init

```bash
agentself install --tools
agentself init
agentself --json show
agentself backends wallet
```

`init` and `diagnose` do not fetch binaries. Missing host tools: `next: agentself install --tools`. `AGENTSELF_FETCH_TOOLS=0` refuses a fetch even for `--tools`.

## Live vs not

`agentself backends` lists shipped backends. Default wallet is live Base (`--wallet base`). Default email is AgentMail (`--email agentmail`): store `email.send.token`, then `email connect`. IMAP uses stored `email.address`. Do not invent `{id}@domain`.

## Skills

```bash
agentself install --skills
agentself install --skills -g
agentself install --skills=agents
```
