# Compatibility

0.1.0 is the first stable release. Users can rely on:

- documented CLI commands and flags;
- documented public `AGENTSELF_*` environment variables;
- machine-readable JSON output, identified by its `cli` schema version; and
- saved identities and their versioned on-disk format.

Stable releases continue to read, or provide a migration for, identities
created by earlier stable releases. This release writes `format_version` 1
and uses public JSON schema `cli: 1`.

Internal Python modules, private imports, backend implementation details, and
undocumented filenames or fields are not compatibility promises.

Within a CLI schema version, JSON consumers should ignore unknown object keys.
For example, mail header objects may gain additive metadata such as local task
state while retaining provider read state in `status`.

Mail items retain the provider `id`; compact identity-local `ref` values are an
additive convenience. Their private mapping is copied by backup/restore, while
its on-disk filename layout is not a compatibility promise.

`note` is a public CLI command group in schema 1. Note values are deliberately
non-secret and may appear in human or JSON output. The identity-local notes
directory is copied by whole-identity backup/restore, but its undocumented
filename layout is not a compatibility promise.
