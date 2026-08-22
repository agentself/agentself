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

Public commands: `init`, `show`, `backends`, `diagnose`, `secret`, `note`, `email`, `wallet`, `backup`, `restore`, `install`.

Prefer `--json`. Success and failure are one JSON object on stdout with `"ok"`, and failures include `"error"`, `"reason"`, and `"next"`. Human errors may include a `next:` line on stderr. `agentself --json --version` includes `cli` (machine schema id, currently 3). Ignore unknown keys.

No command prints the current identity (`show`). `--json show` includes `recipient` (age pubkey) and email readiness. Unconfigured `email show` exits 3.

Identity directory is `AGENTSELF_VAULT_ROOT` (default `~/.agentself`).

## Host tools, then init

```bash
agentself install --tools
agentself init
agentself --json show
agentself backends wallet
```

`init` and `diagnose` do not fetch binaries. Missing host tools: `next: agentself install --tools`. `AGENTSELF_FETCH_TOOLS=0` refuses a fetch even for `--tools`. Repeating init is safe; identity or backend changes need `--force`.

## Live vs not

`agentself backends` lists shipped backends and their setup options. Default wallet is live Base (`--wallet base`) and can move real funds. Email is optional: `email connect` is a generic, resumable setup. `--json email connect` never prompts. Continue with `agentself email connect --continue SETUP_ID --result-file PATH`. Do not invent `{id}@domain`. See `agentself backends email` for inputs. Sensitive answers are never passed on argv.

## Skills

```bash
agentself install --skills
agentself install --skills --local
agentself install --skills=agents --local
```

Skills install under the user home directory unless `--local`.
