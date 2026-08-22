# Changelog

## Unreleased

Provider-neutral CLI schema 3. Public commands describe resources and operations; provider signup, OTP, OAuth, presets, and environment aliases stay adapter details.

### Commands

- `email connect` is a generic, resumable setup. `--json` never prompts and exits 3 with a setup object (`setup_id`, `status`, `options`, `human_action_required`, `continue`). Continue with `--continue SETUP_ID --result-file PATH`.
- `email connect --import-env` persists validated environment credentials. Ordinary connect does not copy them into the vault.
- Unconfigured `email show` exits 3. `--json show` includes email readiness.
- `init --force` is required to change identity or backends. Repeating the same init is unchanged.
- `secret exists`, `secret get --file`, `secret get --meta`. Same-value `secret create` returns `unchanged: true`.
- `wallet.key` is protected in listings and requires `--unsafe` to export.
- Encrypted `note create|get|update|list|delete` for process handoff.
- `wallet authorize --file` JSON includes `address`, `scheme`, `network`, `message_sha256`, and `authorization`.
- Provider-neutral `wallet verify`.
- `install --skills` is user-global by default. `--local` writes into the current repository.
- `--version` and `diagnose` report `package` and `executable` paths.

### Contract

- `--json` prints one object on stdout for success and failure. Human errors stay on stderr.
- Exit codes: 0 ok, 1 error, 2 refused, 3 missing.
- Machine schema `cli` is `3`.
- Credential resolution: `AGENTSELF_EMAIL_ADDRESS` / `AGENTSELF_EMAIL_CREDENTIAL`, then vault, then backend-defined environment alias, then setup answer.
- `backends email --json` publishes backend options with `name`, `type`, `required`, `sensitive`, `default`, `choices`, `source`, and `help`.
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
