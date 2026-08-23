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
`format_version` 1 for its saved identity files. The public JSON schema is
`cli: 2`.

Internal Python modules, private imports, backend implementation details, and
undocumented filenames or fields are not compatibility promises.
