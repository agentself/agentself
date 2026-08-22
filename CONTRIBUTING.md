# Contributing

Pull requests are appreciated.

## In this repo

- One folder per backend under `agentself/backends/<channel>/<name>/`.
- The CLI does not import backends. Backends do not import each other or the manager.
- Public commands describe resources and operations. They never grow provider verbs, flags, or top-level JSON fields.
- Setup option `help` and connect `message` must be enough for an agent that has only `--json`, `backends`, and that text to choose the next action: obtain the value, continue with `--result-file`, ask a human, or `init --force` to another backend. Provider signup, OTP, URLs, and env aliases belong in `help` / `message`, not in the parser.
- If a backend, tool, or secret is missing, fail. Do not invent an inbox or fail over.
- Same commands on every backend. No new manager methods or Gateway verbs for a new vendor.
- Backends ship in this package. No plugin loader and no MCP server.
- Logs and `--json` errors do not print secret values.
- `--json` writes one object to stdout for success and failure. Human errors stay on stderr.

## Backend setup rules

- Keep provider details in the backend. Prompts, help text, URLs, validation, and API calls belong in the adapter; the core only handles generic options, actions, states, and results.
- Return the same setup result for every caller. Agents consume it as JSON, while the CLI turns it into guided terminal prompts for people.
- Do not report a connection until the provider has validated it. Never guess an address, resource, or credential, and ask for only one missing value at a time with an accurate next step.
- Test the boundary, not just the adapter. A test backend must be able to finish setup without parser changes or new core orchestration methods. Also check that a fresh agent can find its way using CLI help and backend discovery, either completing the flow automatically or asking a person for one clear action.

## Add a backend

You can add a new backend for any channel.

1. Copy an existing folder (`ethereum` or `imap`) to `agentself/backends/<channel>/<name>/`. Implement the channel contract.
2. Email backends implement `connect()` with the generic setup helpers (`setup_needed`, `setup_failed`, `mailbox_view`). Provider signup, OTP, OAuth, presets, and URLs stay inside the adapter and in option `help` / `message`.
3. Add a `for_binding` case in `factory.py` for that channel. Import the adapter there.
4. Add a `CHANNELS` row in `agentself/host.py`. Set `live`, `verbs`, `custody`, `network`, `asset`, and `options`. Option fields are `name`, `type`, `required`, `sensitive`, `default`, `choices`, `source`, `prompt`, `help`, and the optional `action`.

`CHANNELS` lists shipped backends only. `agentself backends` is how callers discover backend-specific options. A new backend must not require parser, Gateway, or public command changes.
