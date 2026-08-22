# agentself

Persistent local identity for an agent.

Give your agent the ability to hold funds, store credentials, and send email.
Configure different providers and the commands stay the same.

Run init once and every later process reuses the same identity.

Beta / pre-1.0. Linux, macOS, and Windows. Python 3.11+.

## Install

```bash
uv tool install agentself
```

Or with pipx:

```bash
pipx install agentself
```

## Quick start

```bash
agentself install --tools
agentself init

# Wallet
agentself wallet address
agentself wallet balance

# Secrets
agentself secret create API_TOKEN
agentself secret get API_TOKEN

# Email
agentself secret create email.send.token YOUR_TOKEN
agentself email connect
agentself email show
```

Run `agentself show` anytime to see the current identity.

`agentself install --tools` installs the required `age` and `sops` host tools. Email is optional; the default AgentMail backend needs `email.send.token` before `email connect`.

## Why agentself?

- Give an agent a persistent wallet, secrets, and email identity.
- Use the same commands even when the underlying provider changes.
- Built for unattended use with encrypted secrets, structured output, and safe failures.

## Commands

| Area | Commands |
|---|---|
| Identity | `init`, `show`, `diagnose` |
| Secrets | `secret create`, `get`, `update`, `list`, `delete` |
| Wallet | `wallet show`, `address`, `balance`, `authorize`, `send` |
| Email | `email connect`, `show`, `send`, `receive`, `list` |
| Backends | `backends [CHANNEL]` |
| Recovery | `backup`, `restore` |
| Setup | `install --tools`, `install --skills` |

Discover exact arguments and flags from the CLI:

```bash
agentself --help
agentself wallet --help
agentself wallet send --help
agentself email --help
```

## Backends & configuration

See the available implementations and what each supports:

```bash
agentself backends
agentself backends wallet
agentself backends email
agentself backends store
```

| Capability | Default | Alternatives |
|---|---|---|
| Wallet | `base` | `ethereum` |
| Email | `agentmail` | `imap` |
| Secrets | `sops` | `pass` |

Choose backends when initializing:

```bash
agentself init --wallet ethereum --email imap --store pass
```

Wallet and email can also be selected with environment variables:

```bash
export AGENTSELF_WALLET_BACKEND=ethereum
export AGENTSELF_EMAIL_BACKEND=imap
agentself init
```

Configuration precedence is:

```text
CLI flag > environment variable > saved configuration > default
```

There is no automatic backend failover. `agentself backends` is the authoritative source for backend-specific requirements and configuration knobs.

One identity lives in one directory, `~/.agentself` by default. Use `AGENTSELF_VAULT_ROOT` to isolate identities:

```bash
AGENTSELF_VAULT_ROOT=~/.agent-a agentself init
AGENTSELF_VAULT_ROOT=~/.agent-b agentself init
```

## Automation and agents

The CLI is designed to be discovered directly from the shell. An agent does not need an MCP server or skill to use it.

```bash
agentself --help
agentself <command> --help
agentself backends
agentself diagnose
```

For automation, prefer `--json`.

Success is one JSON object on stdout with `"ok": true`. Failure is one JSON object on stderr with `ok`, `error`, `reason`, and `next`.

```json
{
  "ok": false,
  "error": "...",
  "reason": "...",
  "next": "..."
}
```

Exit codes are `0` success, `1` error, `2` refused, and `3` missing dependency or resource.

Consumers should ignore unknown JSON keys. `agentself --json --version` includes `cli`, the machine schema identifier.

An optional bundled skill gives compatible coding agents the same discovery guidance:

```bash
agentself install --skills
agentself install --skills -g
agentself install --skills=agents
```

`--help` and `--json` are sufficient without the skill.

## Security

- Secret values are encrypted at rest by the configured store.
- `secret list` returns names, not values.
- Logs and structured errors do not print secret values.
- Missing backends, credentials, or tools fail instead of silently switching implementations.
- Wallet backends are live and `wallet send` can move real assets.
- Identity files are replaced atomically, and wallet-send retries do not construct a second payment.

Back up the identity with `agentself backup PATH`. Report vulnerabilities through [SECURITY.md](SECURITY.md).

## Extending agentself

To add a backend, implement the existing channel contract rather than adding vendor-specific CLI verbs. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Development

```bash
git clone https://github.com/agentself/agentself
cd agentself
uv sync
uv run pytest
uv run agentself --help
uv run agentself init
```

## License

[MIT](LICENSE)
