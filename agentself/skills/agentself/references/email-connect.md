# Connect email

Use this workflow only after `agentself init`. Email is optional and does not
block identity initialization.

## Choose the authorized branch

Start with:

```bash
agentself --json email connect
```

On exit 3, read `status`, `option.name`, `option.choices` if present, and
`next`. Optional `message` and `human_action_required` may also be present.
Follow the offered choices instead of inventing an address or provider
procedure. For option essays, run `agentself --json backends email BACKEND`.

- **Existing key:** choose `existing_credential` when the user already has a
  key or will create one in the provider console. This does not authorize a
  signup.
- **New signup:** choose `create_account` only after the user explicitly
  authorizes creating a new provider organization with the exact email
  identity to use. Provider help describes a path; it is not authorization.

For AgentMail, make one signup attempt with that exact approved identity. A
claimed, forbidden, or unavailable result ends the attempt. Ask the user; do
not try aliases, disposable addresses, or additional organizations. An OTP or
confirmation email is not an API key. A key may be shown only once; if it is
lost, the human creates another under API Keys. Do not assume an existing
claimed organization can be attached unless the provider documents it.

## Continue without leaking answers

`--json` never prompts. Write the one requested answer to a private temporary
file, then use the exact `state` from the latest response:

```bash
agentself --json email connect --continue --state STATE --result-file PATH
```

Never put credentials, OTPs, or other sensitive answers in argv, logs, a
`*.notes` file, or an agent/user chat prompt. Delete temporary answer files
after use. Do not switch email backends during a continuation.

For a human login handoff, run:

```bash
agentself email connect
```

Relay only the displayed link and one instruction: open it and enter or paste
the requested value into the command's secure prompt. The human should not
send the value back in chat, create a result file, copy opaque state, or run
the continuation command. Wait for the command, then verify:

```bash
agentself --json email show
```

One validated credential completes discovery; stop searching for alternatives.

## Resume an interrupted setup

Continuation is stored with the identity only when setup needs opaque resume
state (secrets). Inspecting the connect menu does not write identity secrets.
The latest opaque `state` token is still required to resume. Keep the same
`AGENTSELF_IDENTITY_DIR` and continue only that latest state. Never reconstruct
state or reuse an older token after a later step.

If the latest state was lost, or the provider reports a failed or unknown setup,
restart with
`agentself --json email connect`. If the credential cannot be obtained, inspect
`agentself backends email`; change backend only by intentionally reinitializing
with `init --force --email OTHER`, or stop.

## Credential scope

For a transient sandbox, inject `AGENTSELF_EMAIL_CREDENTIAL` (or the backend
alias) on every email command. If a key owns multiple inboxes, also inject a
provider-listed `AGENTSELF_EMAIL_ADDRESS`. For durable use, complete
`email connect`; it stores the validated credential in this identity.

Credential injection provides email access only. It does not preserve the
wallet or make a new identity directory equivalent to the old one. See
[handoff.md](handoff.md) before moving work between agents or directories.
