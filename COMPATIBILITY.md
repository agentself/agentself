# Compatibility

0.2.0 is a clean break from 0.1.0. Users can rely on:

- documented CLI commands and flags;
- documented public `AGENTSELF_*` environment variables;
- machine-readable JSON output, identified by the `cli` schema version in
  the version response; and
- saved identities and their versioned on-disk format.

Stable releases continue to read, or provide a migration for, identities
created by earlier stable releases. This release writes `format_version` 1
and uses public JSON schema `cli: 2`. Identity `format_version` remains 1;
there is no migration.

JSON schema 2 is versioned through `agentself --version`, not a `"cli"`
field on every command payload. Default success and failure emit one compact
JSON object on stdout. Exit 0 success, 1 error, 2 refused, 3 missing. stderr
is empty for handled outcomes. Only `--help` emits text.

`--json` is a hidden accepted no-op and may be removed in a later major
contract change.

Internal Python modules, private imports, backend implementation details, and
undocumented filenames or fields are not compatibility promises.

Within a CLI schema version, JSON consumers should ignore unknown object keys.
For example, mail header objects may gain additive metadata such as local task
state while retaining provider read state in `status`.

`wallet authorize` and `wallet verify` report `scheme` for the statement
that was authorized. Chain wallets may use `eip191` or `eip712`. Consumers
that assumed a single backend-wide scheme should read the per-command
value.

Mail items retain the provider `id`; compact identity-local `ref` values are an
additive convenience. Their private mapping is copied by backup/restore, while
its on-disk filename layout is not a compatibility promise.

`note` is a public CLI command group. Note values are deliberately
non-secret and may appear in JSON output. The identity-local notes
directory is copied by whole-identity backup/restore, but its undocumented
filename layout is not a compatibility promise.
