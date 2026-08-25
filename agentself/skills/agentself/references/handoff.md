# Identity continuity and handoff

Decide whether the next worker needs the same identity or an isolated one
before running `init`.

## Same identity versus isolation

`AGENTSELF_IDENTITY_DIR` is the identity boundary.

- Use the same directory for sequential work that must keep the same wallet,
  encrypted secrets, non-secret notes, email setup, and local acted-mail state.
  Avoid concurrent writers to one directory.
- Use a distinct directory and `agentself init` when agents or tasks should
  have separate custody. This creates a different wallet.
- Use `backup` and `restore` to move or clone the same identity into a distinct
  directory. A clone carries the wallet key and every secret; protect it as
  strongly as the source.

Email credentials alone do not provide wallet continuity. Never initialize a
fresh directory and assume that matching email access means it has the same
wallet.

Before and after a handoff, record and compare public identity data:

```bash
agentself show
agentself wallet address
```

Done when both JSON `address` values match the intended identity. Stop if the
wallet address changes when continuity was required. Use
`agentself wallet address --raw` only when a caller needs the exact address
bytes.

## Interrupted work

Keep the same identity directory and inspect current state before retrying:

```bash
agentself show
agentself diagnose
```

Commands are designed to report `next`; follow that field rather than guessing.
For interrupted email setup, use [email-connect.md](email-connect.md). For mail
task state, use [mail.md](mail.md).

Use the identity's first-class note channel for non-secret handoff context:

```bash
agentself note set handoff --file PATH
agentself note list
agentself note get handoff
```

`note set` creates or replaces the note, so retrying a handoff write is safe.
Default `note get` is JSON; `--raw` writes stored note bytes. Notes are
printable by default and may contain public addresses, message IDs,
command outcomes, and the next action. Never put credentials, OTPs, private
keys, secret values, or mail bodies in a note. Delete completed context with
`agentself note delete handoff`.

## Backup and restore

Back up the complete identity to an empty protected destination:

```bash
agentself backup PATH
```

The copy includes config, wallet/age key material, encrypted secrets,
non-secret notes, mail cache, and acted-mail state. The age key remains
plaintext on the host.
`--force` replaces a non-empty destination, so use it only after verifying the
path.

To restore into the identity directory selected by
`AGENTSELF_IDENTITY_DIR`:

```bash
agentself restore PATH
agentself show
agentself wallet address
```

Restore refuses a non-empty destination unless `--force`. Before forcing,
verify both source and destination; replacement is intentional and changes
the destination's wallet. After restore, compare the wallet address with the
pre-handoff public address and check email readiness. Do not print or manually
copy key and secret files.
