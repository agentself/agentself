If you are an LLM, install the CLI, then run `agentself install --skills`.

# agentself

`agentself` gives an agent a persistent local identity: a wallet, encrypted
secrets, non-secret notes, and optional email. It is a CLI first, with one
command tree across the supported backends.

Linux, macOS, and Windows. Python 3.11+. 0.2.1 is a patch of the agent-native
CLI (`cli: 2`). Review [COMPATIBILITY.md](COMPATIBILITY.md) before automating
against the CLI.

## Install

```bash
uv tool install agentself
```

If `uv tool install` succeeds but `agentself` is not on PATH, run
`uv tool update-shell` and open a new shell.

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
agentself --identity-dir PATH init
agentself --identity-dir PATH wallet address
agentself --identity-dir PATH secret create API_TOKEN --file ./api-token.txt
agentself --identity-dir PATH note set handoff --file ./handoff.txt
agentself --identity-dir PATH email connect
```

Those commands emit JSON. `init` creates one identity directory. Run
`agentself show` to inspect it; repeat `init` is safe, and `--force` is
required to change an existing identity or its backends. Email setup is
optional and does not block init.
For AgentMail, `email connect` offers two explicit routes: connect an existing
API key, or create an account when that external action has been authorized.
Continue with
`agentself email connect --continue --state STATE --result-file PATH`.
The account route uses the approved human email and its six-digit OTP, and
keeps the generated API key encrypted while setup continues. If JSON includes
`human_action_required`, `action` is an external step a person may need to
perform; the CLI never solicits input.

## Commands

| Area | Commands |
| --- | --- |
| Identity | `init`, `show`, `diagnose` |
| Secrets | `secret create`, `get`, `update`, `list`, `delete`, `exists` |
| Non-secret notes | `note set`, `get`, `list`, `delete`, `exists` |
| Wallet | `wallet show`, `address`, `balance`, `authorize`, `verify`, `send` |
| Email | `email connect`, `show`, `send`, `receive`, `list`, `find`, `mark` |
| Backends | `backends [CHANNEL [BACKEND]]` |
| Commands | `commands` |
| Recovery | `backup`, `restore` |
| Setup | `install --tools`, `install --skills` |

`agentself commands` lists featured verbs and grouped raw capabilities.
Use `--help` for the exact current flags and input rules:

```bash
agentself --help
agentself wallet --help
agentself email --help
```

Secret file input drops a leading UTF-8 BOM and keeps a trailing newline.
`wallet.key` must be a hex private key after that decode. Default
`secret get NAME` is JSON with the value. Prefer `--file PATH`. `--raw`
writes stored secret bytes with no JSON and no added newline; protected
names (`wallet.key`) still need `--unsafe`.

`note set` is an idempotent upsert for printable, non-secret handoff context.
It accepts `VALUE` or `--file PATH`; file input drops a leading UTF-8 BOM and
otherwise preserves UTF-8 bytes and newlines. Default `note get` is JSON.
`--raw` writes stored note bytes with no JSON and no added newline.
Notes are included in identity backup/restore. Never put credentials,
OTPs, private keys, secret values, or mail bodies in notes.

`email list` and `email find QUERY` return headers and `new`/`seen` status
without bodies. `email receive` without a ref is a repeatable, non-consuming
new-header check: it uses the same list path, does not fetch bodies, and does
not change provider or local seen state. Surfaced messages include the raw
provider `id` and a stable compact identity-local `ref` such as `m1`.
Use the ref with `email receive REF --file PATH` or
`email mark REF acted|unacted`; raw provider IDs remain accepted after
`list` or `receive` has stored them. An explicit ref keeps the existing
consuming receive. `acted` is
independent local task state. The compact syntax is reserved: an unknown
matching ref is refused instead of being sent to a provider. Acted state can be
filtered with `email list --acted|--unacted` or the same flags on `email find`.
Bodies can contain API keys and login links; write them with `--file PATH`,
or `--raw` when a caller needs exact body bytes (one ref or provider ID).

`--raw` is only for `wallet address`, `wallet show`, `wallet authorize`,
`secret get`, `note get`, and `email receive`. `wallet address` and
`wallet show` write the address; `wallet authorize` writes the signature
or authorization token. Unsupported `--raw` is a JSON refusal, exit 2.

`wallet authorize --file PATH --out PATH` authorizes the host's exact
statement with this identity and writes the token to a private file.
Statement files keep trailing newlines; `message_sha256` is the SHA-256 of
that exact decoded statement. A typed statement (`domain`, `types`,
`message`) is authorized as typed data; other files, including login text,
stay a personal signature. The JSON `scheme` names that encoding. Prefer
`wallet verify --file PATH --authorization-file PATH` so the token is never
placed on argv. Positional MESSAGE and JSON `authorization` remain for
CLI 2 compatibility. Do not create another wallet to sign. `--out -` is
refused; stdout transport is `--raw`.

Stdin is never implicit. Pass `--file -`, `--result-file -`, or
`--wallet-key-file -`. Missing explicit input is JSON, exit 3.

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

The identity directory is `~/.agentself` by default. Prefer
`--identity-dir PATH` for one invocation. Precedence is the flag, then
`AGENTSELF_IDENTITY_DIR`, then `~/.agentself`. The flag is not persisted.

```bash
agentself --identity-dir ~/.agent-a init
agentself --identity-dir ~/.agent-b init
```

Use `AGENTSELF_EMAIL_ADDRESS` and `AGENTSELF_EMAIL_CREDENTIAL` for transient
email configuration when appropriate. Runtime environment credentials are
not copied into the identity; complete `email connect` with its
`--result-file` when the credential should be encrypted into the identity.
If authorized AgentMail signup reports that the requested identity is claimed,
forbidden, or unavailable, stop and use the existing-key route. The CLI does
not probe alternate aliases.

## Automation and agents

The CLI is designed to be discovered from a shell. No MCP server or Python
import is required:

```bash
agentself --version
agentself commands
agentself diagnose
```

Default success and failure emit one compact JSON object on stdout. stderr
is empty for handled outcomes. Exit codes are `0` for success, `1` for an
error (including missing host tools; recover with `agentself install --tools`),
`2` for a refusal, and `3` when an input or resource is missing.
`agentself --version` reports the package version and machine-output schema
(`cli`: 2). Consumers should ignore unknown JSON keys.

An optional bundled skill provides the same discovery guidance to compatible
coding agents:

```bash
agentself install --skills
agentself install --skills -g
```

`install --skills` copies the skill into the current workspace. `-g` copies
it into the user skill directory. Neither path is the identity directory.

## Security

- Secret values are encrypted at rest by the configured store.
- Notes are non-secret and printable; private file modes are defense in depth, not encryption.
- Secret listings return names, not values. Prefer `secret get NAME --file PATH`. Default JSON includes the value; `--raw` writes stored bytes. `wallet.key` also requires `--unsafe`.
- Email bodies stay off default JSON; use `email receive REF --file PATH` or `--raw`.
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
