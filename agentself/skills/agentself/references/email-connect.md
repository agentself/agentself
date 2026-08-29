# Connect email

Use this reference after `agentself init`. Email is optional and does not
block identity setup.

## Choose a connection

Run:

```bash
agentself email connect
```

Continue when JSON has a connected `address`. Exit code 3 returns `status`,
`option.name`, and `next`. The response can also include `message` and
`human_action_required`.

Use the offered `choices`. Do not invent an address or provider procedure. Run
`agentself backends email BACKEND` for option details. Read `help`, `choices`,
`sensitive`, `action`, `message`, and `human_action_required` in the response.

### Existing key

Choose `existing_credential` when the user has a key or will create one in the
provider console. This choice does not authorize signup.

### New signup

Choose `create_account` only after the user authorizes a new provider
organization with the exact email identity to use. Provider help describes a
path. It does not authorize signup.

For AgentMail, make one signup attempt with the exact approved identity. Stop
when the provider reports a claimed, forbidden, or unavailable identity. Ask the
user what to do next. Do not try aliases, disposable addresses, or other
organizations.

An OTP or confirmation email is not an API key. The provider can show a key
only once. If the key is lost, the user must create another under API Keys. Do
not attach an existing claimed organization unless the provider documents that
path.

## Continue without exposing answers

Email connect does not prompt for input. Write the requested answer to a
private temporary file. Then use the exact `state` from the latest response:

```bash
agentself email connect --continue --state STATE --result-file PATH
```

Use `--result-file -` when the answer must come from stdin. Keep credentials,
OTPs, and other sensitive answers out of command arguments, logs, `*.notes`
files, and chat. Delete temporary answer files after use. Keep the same email
backend during continuation.

If JSON includes `human_action_required`, relay the `action` URL and label when
an `action` object exists. Otherwise relay `message`. After the user completes
that external step, continue with `--result-file`, or poll with
`--continue --state STATE --interval 5` when no new file is needed. The first
`email connect` still returns immediately so you can show the URL. Follow
`_next.command` when present. Do not run an interactive prompt or ask the
user to paste a secret into chat. Then check the connection:

```bash
agentself email show
```

The setup is complete when `email show` JSON reports ready. One validated
credential completes discovery. Stop looking for alternatives.

## Resume interrupted setup

The identity stores continuation only when setup needs opaque resume state,
which can contain secrets. Inspecting the connect menu does not write identity
secrets. Resume only with the latest opaque `state` token. Keep the same
`--identity-dir PATH` or `AGENTSELF_IDENTITY_DIR` setting. Do not reconstruct
state or reuse an older token after a later step.

If the latest state is lost, or the provider reports failed or unknown setup,
restart with `agentself email connect`. If you cannot get the credential,
inspect `agentself backends email`. Change the backend only by intentionally
reinitializing with `init --force --email OTHER`, or stop.

## Limit credential scope

For a transient sandbox, inject `AGENTSELF_EMAIL_CREDENTIAL` or the backend
alias on every email command. If one key owns multiple inboxes, also inject
the provider-listed `AGENTSELF_EMAIL_ADDRESS`. For durable use, complete
`email connect`. It stores the validated credential in this identity.

Credential injection gives email access only. It does not preserve the wallet
or make a new identity directory equivalent to the old one. Read
[handoff.md](handoff.md) before moving work between agents or directories.
