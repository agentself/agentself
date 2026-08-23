# Compatibility

agentself is an alpha. Before stable releases begin, commands and details may
change.

Once stable releases begin, users should be able to rely on:

- documented CLI commands and flags;
- documented public `AGENTSELF_*` environment variables;
- machine-readable JSON output, identified by its `cli` schema version; and
- saved identities and their versioned on-disk format.

Stable releases should continue to read, or provide a migration for,
identities created by earlier stable releases. The current alpha writes
`format_version` 1 for its saved identity files and starts the public JSON
schema at `cli: 1`.

Internal Python modules, private imports, backend implementation details, and
undocumented filenames or fields are not compatibility promises.

Within a CLI schema version, JSON consumers should ignore unknown object keys.
For example, mail header objects may gain additive metadata such as local task
state while retaining provider read state in `status`.
