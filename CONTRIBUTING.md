# Contributing

Pull requests are appreciated.

## In this repo

- One folder per backend under `agentself/backends/<channel>/<name>/`.
- The CLI does not import backends. Backends do not import each other or the manager.
- If a backend, tool, or secret is missing, fail. Do not invent an inbox or fail over.
- Same commands on every backend. No new manager methods or Gateway verbs for a new vendor.
- Backends ship in this package. No plugin loader and no MCP server.
- Logs and `--json` errors do not print secret values.

## Add a backend

You can add a new backend for any channel.

1. Copy an existing folder (`ethereum` or `imap`) to `agentself/backends/<channel>/<name>/`. Implement the channel contract.
2. Add a `for_binding` case in `factory.py` for that channel. Import the adapter there.
3. Add a `CHANNELS` row in `agentself/host.py`. Set `live`, `verbs`, `custody`, `network`, and `asset`.

`CHANNELS` lists shipped backends only.
