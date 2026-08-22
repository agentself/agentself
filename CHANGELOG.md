# Changelog

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
