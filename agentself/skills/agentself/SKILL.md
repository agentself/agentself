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

Prefer `--json`. Success and failure are one JSON object on stdout with `"ok"`, and failures include `"error"`, `"reason"`, and `"next"`. Human errors may include a `next:` line on stderr. `agentself --json --version` includes `cli` (machine schema id, currently 3). Ignore unknown keys.

No command prints the current identity (`show`). `--json show` includes `recipient` (age pubkey) and email readiness. Unconfigured `email show` is ready false, exit 0.

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

`agentself backends` lists shipped backends and their setup options. Default wallet is live Base (`--wallet base`) and can move real funds. Email is optional.

`--json email connect` never prompts. On exit 3, read `option.help` — that text is the procedure. If `message` is present, read it too. Obtain the value with other tools or ask the operator. Continue with `agentself --json email connect --continue --state STATE --result-file PATH`. Sensitive answers go in the file, not argv. If you cannot obtain the value, `agentself backends email` then `init --force --email OTHER`, or stop. Do not invent `{id}@domain`. Do not switch backends with `--continue`.

## Human assistance

When a setup response has `human_action_required: true`, first check the approved automated credential sources named by the generic option metadata (`source`, the isolated vault, or an already-authorized tool). Keep secret values out of logs, arguments, and chat. If no approved automated value is available, run the generic interactive flow:

```bash
agentself email connect
```

Use the link and instructions rendered by that flow. Give the human one simple instruction: open the displayed link, copy the requested value, and paste it into the secure prompt. Then wait for the command to finish and verify the result with `agentself --json email show`. The human does not create a result file, type a vault path, copy opaque state, or run a continuation command. The option's `type`, `sensitive`, `prompt`, `help`, and optional `action` metadata are backend-provided; keep the procedure provider-neutral.

For AgentMail, use <https://docs.agentmail.to/api-reference/agent/sign-up> only for a first-time, unclaimed signup and capture the key from its HTTP response. This is not claimed-organization recovery. Otherwise, create a key under API Keys in the console. The OTP or confirmation email has no key. Keys are shown once; create another if one is lost. Treat console attachment to an already-claimed organization as unsupported unless AgentMail documents it.

## Fresh sandbox

Choose one mode:

- Transient: provide `AGENTSELF_EMAIL_CREDENTIAL` or the backend alias on every email invocation. When the key owns multiple inboxes, also provide `AGENTSELF_EMAIL_ADDRESS` with a provider-listed address.
- Durable: continue `email connect` with `--result-file`; this stores the credential in the current isolated identity.
- Same-identity migration: use `backup` and `restore`; this clones the wallet and every secret.

Give distinct agents distinct `AGENTSELF_VAULT_ROOT` directories and inject only the credentials each needs.

## Skills

```bash
agentself install --skills
agentself install --skills -g
agentself install --skills=agents
```

Skills install into the current directory unless `-g`.
