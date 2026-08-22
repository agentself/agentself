# Changelog

## Unreleased

Provider-neutral CLI schema 3. Public commands describe resources and operations; provider signup, OTP, OAuth, presets, and environment aliases stay adapter details.

### Commands

- `email connect` is a generic, resumable setup. `--json` never prompts and exits 3 with one outstanding option plus opaque `state`. Continue with `--json --continue --state STATE --result-file PATH`. HTTP 401/403 is `invalid credentials`, not `rpc`.
- Non-JSON `email connect` renders the same backend-provided setup protocol as a guided terminal flow: external actions are displayed as links, secrets use hidden input, and choices use numbered prompts. Agents check approved automated sources first, then use this interactive flow and wait for completion when human action is required.
- Environment credentials are used at runtime and are not copied into the vault.
- Fresh sandboxes can use `AGENTSELF_EMAIL_CREDENTIAL` or a backend alias on every email invocation, plus `AGENTSELF_EMAIL_ADDRESS` to select a verified-owned inbox. `--result-file` stores the credential in the current isolated identity.
- Unconfigured `email show` is ready false, exit 0. `--json show` includes email readiness. `email connect` still exits 3 when input is required.
- `init --force` is required to change identity or backends. Repeating the same init is unchanged.
- `secret exists`, `secret get --file`, `secret get --meta`. Same-value `secret create` returns `unchanged: true`. Secret values from argv, `--file`, or stdin. Setup answers stay off argv (`--result-file`).
- `wallet.key` is protected in listings and requires `--unsafe` to export.
- `wallet authorize --file` JSON includes `address`, `scheme`, `network`, `message_sha256`, and `authorization`. Scheme comes from the wallet backend.
- Provider-neutral `wallet verify`.
- `install --skills` writes into the current directory. `-g` writes under the user home directory.
- `--json --version` and `diagnose` report `package` and `executable` paths. Human `--version` is one line.

### Contract

- `--json` prints one object on stdout for success and failure. Human errors stay on stderr.
- Exit codes: 0 ok, 1 error, 2 refused, 3 missing.
- Machine schema `cli` is `3`.
- Credential resolution: `AGENTSELF_EMAIL_ADDRESS` / `AGENTSELF_EMAIL_CREDENTIAL`, then vault, then backend-defined environment alias, then setup answer.
- AgentMail keys come from the initial signup HTTP response or the console, not the OTP or confirmation email. Keys are shown once; create another if one is lost. Console attachment to an already-claimed organization is not a supported assumption.
- `backup` / `restore` clones the whole identity, including the wallet and every secret. Use it for same-identity migration; distinct agents use isolated vaults.
- `agentself backends --json` publishes backend options with `name`, `type`, `required`, `sensitive`, `default`, `choices`, `source`, `prompt`, `help`, and an optional generic `action` such as `open_url`. Option `help` is the agent procedure (how to obtain the value, env alias, what to do if you cannot); `prompt` and action labels/URLs are backend-provided renderer content. Switching backends is `init --force`, not continue.
- UTF-8 stdin/file handling strips a BOM and one trailing newline (`\n` or `\r\n`). Byte counts and SHA-256 are UTF-8.

## 0.1.0a1

First published alpha.

Local identity for an agent: wallet, encrypted secrets, and optional email. One CLI.

### Commands

- `init`: create the local identity
- `show`: print the current identity
- `backends`: list shipped backends
- `diagnose`: check that this host can run
- `secret`: create, get, update, list, delete
- `email`: connect, show, send, receive, list
- `wallet`: show, address, balance, authorize, send
- `backup` / `restore`: copy the identity directory
- `install --tools`: fetch pinned age and sops
- `install --skills`: copy the optional agent skill

### Backends

- Wallet: `base` (default), `ethereum`
- Email: `agentmail` (default), `imap`
- Store: `sops` (default), `pass`

Flag, then env, then identity-directory config, then default. No failover.

### Contract

- `--json` prints one object. Success is stdout with `ok: true`. Failure is stderr with `ok: false`, `error`, `reason`, and `next`.
- Exit codes: 0 ok, 1 error, 2 refused, 3 missing.
- Identity directory default `~/.agentself`. Isolate with `AGENTSELF_VAULT_ROOT`.
- Vault files stamp `format_version` 2. Keys: `identity_id`, `wallet_backend`, `email_backend`. Unknown or missing versions fail closed.
- `install --tools` fetches pinned age and sops. `init` and `diagnose` do not fetch.
- `diagnose` is green after `init`. Email not connected is `ready.email: false`, not a failure.
- `wallet.key` cannot be deleted.
- No plugin loader. No third-party backend discovery.
