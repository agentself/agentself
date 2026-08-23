If you are an LLM, install the CLI, then run `agentself install --skills`.

# agentself

`agentself` gives an agent a persistent local identity: a wallet, encrypted
secrets, and optional email. It is a CLI first, with one command tree across
the supported backends.

Linux, macOS, and Windows. Python 3.11+. This is the first public alpha, so
review [COMPATIBILITY.md](COMPATIBILITY.md) before automating against it.

## Install

```bash
uv tool install agentself
```

Or:

```bash
pipx install agentself
```

If neither tool is available:

```bash
python -m pip install --user agentself
```

## Quick start

```bash
agentself install --skills
agentself install --tools
agentself init
agentself wallet address
agentself secret create API_TOKEN --file ./api-token.txt
agentself email connect
```

`init` creates one identity directory. Run `agentself show` to inspect it;
repeat `init` is safe, and `--force` is required to change an existing
identity or its backends. Email setup is optional and does not block init.
For AgentMail, `email connect` offers two explicit routes: connect an existing
API key, or create an account when that external action has been authorized.
The account route asks for the approved human email, sends its six-digit OTP,
and keeps the generated API key encrypted while setup continues.

## Commands

| Area | Commands |
| --- | --- |
| Identity | `init`, `show`, `diagnose` |
| Secrets | `secret create`, `get`, `update`, `list`, `delete`, `exists` |
| Wallet | `wallet show`, `address`, `balance`, `authorize`, `verify`, `send` |
| Email | `email connect`, `show`, `send`, `receive`, `list`, `mark` |
| Backends | `backends [CHANNEL]` |
| Recovery | `backup`, `restore` |
| Setup | `install --tools`, `install --skills` |

Use the CLI for the exact current flags and input rules:

```bash
agentself --help
agentself wallet --help
agentself email --help
```

Secret file input drops a leading UTF-8 BOM and keeps a trailing newline.
`wallet.key` must be a hex private key after that decode. Retrieval is safe
by default: use `secret get NAME --file PATH` or `--meta`; plaintext stdout
requires `--print`.

`email list`, `email find QUERY`, and default `email receive` print headers and
`new`/`seen` status without bodies. Surfaced messages include the raw provider
`id` and a stable compact identity-local `ref` such as `m1`.
Use the ref with `email receive REF --file PATH` or
`email mark REF acted|unacted`; raw provider IDs remain accepted. `acted` is
independent local task state. The compact syntax is reserved: an unknown
matching ref is refused instead of being sent to a provider. Acted state can be
filtered with `email list --acted|--unacted` or the same flags on `email find`.
Bodies can contain API keys and login links; `--print` is an explicit unsafe
choice.

## Backends & configuration

Backends keep the public commands stable while allowing the implementation to
change:

```bash
agentself backends
agentself backends wallet
agentself backends email
agentself backends store
```

| Capability | Default | Alternatives |
| --- | --- | --- |
| Wallet | `base` | `ethereum` |
| Email | `agentmail` | `imap` |
| Secrets | `sops` | `pass` |

Choose a backend during initialization:

```bash
agentself init --wallet ethereum --email imap --store pass
```

Wallet and email backends can also be selected with
`AGENTSELF_WALLET_BACKEND` and `AGENTSELF_EMAIL_BACKEND`. The precedence is
CLI flag, environment variable, saved identity configuration, then default;
there is no automatic failover. Backend-specific setup, credential sources,
and required host settings are exposed by `agentself backends` and
`agentself email connect`.

The identity directory is `~/.agentself` by default. Set
`AGENTSELF_IDENTITY_DIR` to isolate identities, for example:

```bash
AGENTSELF_IDENTITY_DIR=~/.agent-a agentself init
AGENTSELF_IDENTITY_DIR=~/.agent-b agentself init
```

Use `AGENTSELF_EMAIL_ADDRESS` and `AGENTSELF_EMAIL_CREDENTIAL` for transient
email configuration when appropriate. Runtime environment credentials are
not copied into the identity; complete `email connect` with its secure result
file when the credential should be encrypted into the identity.
If authorized AgentMail signup reports that the requested identity is claimed,
forbidden, or unavailable, stop and use the existing-key route. The CLI does
not probe alternate aliases.

## Automation and agents

The CLI is designed to be discovered from a shell. No MCP server or Python
import is required:

```bash
agentself --help
agentself backends
agentself diagnose
```

For automation, prefer `--json`. Successful and failed commands emit one JSON
object; human-readable errors stay on stderr. Exit codes are `0` for success,
`1` for an error, `2` for a refusal, and `3` when an input, dependency, or
resource is missing. `agentself --json --version` reports the package version
and machine-output schema version. Consumers should ignore unknown JSON keys.

An optional bundled skill provides the same discovery guidance to compatible
coding agents:

```bash
agentself install --skills
agentself install --skills -g
```

## Security

- Secret values are encrypted at rest by the configured store.
- Secret listings return names, not values; plaintext needs `--print`, and `wallet.key` also requires `--unsafe`.
- Email bodies stay off stdout unless `email receive --print` is explicit; prefer `ID --file PATH`.
- Logs and structured errors redact secret values.
- Missing credentials and host tools fail instead of silently switching backends.
- Wallet backends are live; `wallet send` can move real assets.
- Identity files are replaced atomically, and wallet-send retries do not create a second payment.

Back up an identity with `agentself backup PATH`. Report vulnerabilities
through [SECURITY.md](SECURITY.md).

## Extending agentself

New backends implement the existing channel contract and do not add
provider-specific CLI verbs. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Development

```bash
git clone https://github.com/agentself/agentself
cd agentself
uv sync
uv run pytest
uv run ruff check .
uv run mypy agentself
```

See [RELEASE.md](RELEASE.md) for the small-project release checklist.

## License

[MIT](LICENSE)
