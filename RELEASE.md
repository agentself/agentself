# Release checklist

1. Confirm `main` is clean and CI is green.
2. Choose the release version and update the single package version.
3. Finalize the matching section in `CHANGELOG.md`.
4. Build the wheel and source distribution; run `twine check --strict`.
5. Run pytest, Ruff, mypy, and an installed-wheel CLI smoke test.
6. Create the matching `vVERSION` tag and push it.
7. Let the tag workflow publish to TestPyPI, then PyPI, through Trusted Publishing (OIDC).
8. Verify the published package, version, CLI smoke test, and GitHub release.

Do not replace Trusted Publishing with a long-lived PyPI API token. Keep the
release tag and package version identical.
