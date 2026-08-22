# Contributing

Pull requests are appreciated.

## In this repo

- One folder per backend under `agentself/backends/<channel>/<name>/`.
- The CLI does not import backends. Backends do not import each other or the manager.
- Public commands describe resources and operations. They never grow provider verbs, flags, or top-level JSON fields.
- If a backend, tool, or secret is missing, fail. Do not invent an inbox or fail over.
- Same commands on every backend. No new manager methods or Gateway verbs for a new vendor.
- Backends ship in this package. No plugin loader and no MCP server.
- Logs and `--json` errors do not print secret values.
- `--json` writes one object to stdout for success and failure. Human errors stay on stderr.

## Add a backend

You can add a new backend for any channel.

1. Copy an existing folder (`ethereum` or `imap`) to `agentself/backends/<channel>/<name>/`. Implement the channel contract.
2. Email backends implement `connect()` with the generic setup helpers (`setup_needed`, `setup_failed`, `mailbox_view`). Provider signup, OTP, OAuth, presets, and URLs stay inside the adapter.
3. Add a `for_binding` case in `factory.py` for that channel. Import the adapter there.
4. Add a `CHANNELS` row in `agentself/host.py`. Set `live`, `verbs`, `custody`, `network`, `asset`, and `options`. Option fields are `name`, `type`, `required`, `sensitive`, `default`, `choices`, `source`, and `help`.

`CHANNELS` lists shipped backends only. `agentself backends` is how callers discover backend-specific knobs. A new backend must not require parser, Gateway, or public command changes.
