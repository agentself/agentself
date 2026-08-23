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

Keep values out of logs. Store exact UTF-8 files with `secret create NAME
--file PATH`. Retrieve with `secret get NAME --file PATH` or `--meta`;
plaintext stdout requires explicit `--print`.

`email list` and `email receive` expose message IDs and `new`/`seen` status.
Receive output omits bodies by default. Fetch one safely with `email receive ID
--file PATH`; only use `--print` when stdout exposure is intended. Looking for
wallet signing: use `wallet authorize`; its JSON reports the scheme.

Prefer `--json`. Success and failure are one JSON object on stdout with `"ok"`, and failures include `"error"`, `"reason"`, and `"next"`. Human errors may include a `next:` line on stderr. `agentself --json --version` includes `cli` (machine schema id, currently 1). Ignore unknown keys.

Start by checking `agentself --json --version`. This skill requires `cli: 1`. A different schema or an unexpected `package` / `executable` path means the installation is stale: stop, install the intended CLI, reinstall its packaged skill, and check again. Never mix instructions from one schema with an executable from another.

No command prints the current identity (`show`). `--json show` includes `recipient` (age pubkey) and email readiness. Unconfigured `email show` is ready false, exit 0.

Identity directory is `AGENTSELF_IDENTITY_DIR` (default `~/.agentself`).

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

When a setup response has `human_action_required: true`, first check the approved automated credential sources named by the generic option metadata (`source`, the isolated vault, or an already-authorized tool). Source code, tests, browser sessions, personal inboxes, and provider signup endpoints are not approved credential sources. Keep secret values out of logs, arguments, and chat. If no approved automated value is available, run the generic interactive flow:

```bash
agentself email connect
```

Use the link and instructions rendered by that flow. Give the human one simple instruction: open the displayed link, copy the requested value, and paste it into the secure prompt. Then wait for the command to finish and verify the result with `agentself --json email show`. One validated credential completes discovery; continue setup with it and stop looking for alternatives. The human does not create a result file, type a vault path, copy opaque state, or run a continuation command. The option's `type`, `sensitive`, `prompt`, `help`, and optional `action` metadata are backend-provided; keep the procedure provider-neutral.

Provider help describes available setup paths; it does not authorize external account creation. AgentMail signup requires the user's explicit approval to create a new organization. When approved, use the exact approved email identity for one first-time, unclaimed signup and capture the key from its HTTP response. A claimed, forbidden, or unavailable response ends that attempt: ask the user rather than trying aliases or disposable email providers. Without signup authorization, use the interactive flow and wait while the human creates or copies a key under API Keys in the console. The OTP or confirmation email has no key. Keys are shown once; create another in the console if one is lost. Treat console attachment to an already-claimed organization as unsupported unless AgentMail documents it.

## Fresh sandbox

Choose one mode:

- Transient: provide `AGENTSELF_EMAIL_CREDENTIAL` or the backend alias on every email invocation. When the key owns multiple inboxes, also provide `AGENTSELF_EMAIL_ADDRESS` with a provider-listed address.
- Durable: continue `email connect` with `--result-file`; this stores the credential in the current isolated identity.
- Same-identity migration: use `backup` and `restore`; this clones the wallet and every secret.

Give distinct agents distinct `AGENTSELF_IDENTITY_DIR` directories and inject only the credentials each needs.

## Skills

```bash
agentself install --skills
agentself install --skills -g
agentself install --skills=agents
```

Skills install into the current directory unless `-g`.
