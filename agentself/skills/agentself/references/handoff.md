# Identity continuity and handoff

Choose the next identity before you run `init`.

## Choose continuity or isolation

`--identity-dir PATH` selects an identity for one invocation. If you omit the
flag, the CLI uses `AGENTSELF_IDENTITY_DIR`, then `~/.agentself`. The flag is
not persisted.

- Use the same directory for sequential work that must keep the same wallet,
  encrypted secrets, notes, email setup, and acted-mail state. Avoid concurrent
  writers to one directory. One directory is one identity; `init --id NAME`
  cannot switch the person in that folder.
- Use a distinct directory and `agentself --identity-dir PATH init` when agents
  or tasks need separate custody. This creates a different wallet. There is no
  `identity use` command.
- Use `backup` and `restore` to move or clone the same identity into another
  directory. A clone contains the wallet key and every secret. Protect it as
  strongly as the source.

Email credentials do not give wallet continuity. A new directory with the
same email access can still have a different wallet.

Before and after a handoff, record and compare public identity data:

```bash
agentself --identity-dir PATH show
agentself --identity-dir PATH wallet address
```

Done when both JSON `address` values match the intended identity. Stop if the
wallet address changes when you need continuity. Use
`agentself wallet address --raw` only when a caller needs exact address bytes.

## Resume interrupted work

Keep the same identity directory. Inspect its state before you retry:

```bash
agentself show
agentself diagnose
```

Commands report `next`. Use that field instead of guessing. For interrupted
email setup, read [email-connect.md](email-connect.md). For mail task state,
read [mail.md](mail.md).

Use notes for non-secret handoff context:

```bash
agentself note set handoff --file PATH
agentself note list
agentself note get handoff
```

`note set` creates or replaces the note, so a retry is safe. Default
`note get` returns JSON. `--raw` writes stored note bytes. Notes are printable
and can contain public addresses, message IDs, command outcomes, and the next
action. Keep credentials, OTPs, private keys, secret values, and mail bodies
out of notes. Delete completed context with `agentself note delete handoff`.

## Back up and restore

Back up the complete identity to an empty, protected destination:

```bash
agentself backup PATH
```

The backup contains config, wallet and age key material, encrypted secrets,
non-secret notes, mail cache, and acted-mail state. The age key remains plain
text on the host. `--force` replaces a non-empty destination. Check the path
before you use it.

Restore into the identity directory selected by `--identity-dir` or
`AGENTSELF_IDENTITY_DIR`:

```bash
agentself --identity-dir PATH restore SOURCE
agentself --identity-dir PATH show
agentself --identity-dir PATH wallet address
```

Restore refuses a non-empty destination unless you use `--force`. Before you
force a restore, check both source and destination. Replacement changes the
destination wallet. After restore, compare its wallet address with the
pre-handoff address and check email readiness. Keep key and secret files out of
chat and do not copy them by hand.
