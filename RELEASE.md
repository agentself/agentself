# Release checklist

Version, changelog, classifier, and docs live in a PR. Merge to `main`, then
wait for `test.yml` green on that merge commit (including wheel and sdist).

1. Confirm `main` is clean and CI is green on that merge commit, including
   macOS, both Windows Python versions, and the wheel smoke.
2. Confirm the single package version in `agentself/__init__.py` matches the
   changelog section.
3. Confirm the matching section in `CHANGELOG.md` is final.
4. Do not rebuild dist and do not `twine upload`. The tag workflow reuses the
   tested `dist/` from `test.yml`.
5. Confirm pytest, Ruff, mypy, and the installed-wheel CLI smoke test were
   green on that merge commit.
6. Tag **only** the merge commit on `main`, never the PR branch: annotated tag
   `vVERSION` matching `__version__`, then `git push origin vVERSION`. For
   0.2.1: `git tag -a v0.2.1 <merge-sha>` and `git push origin v0.2.1`.
7. Let the tag workflow publish to TestPyPI, then PyPI, through Trusted
   Publishing (OIDC). No API token.
8. After publish is green, create the GitHub Release from the matching
   changelog section. For 0.2.1, use `## 0.2.1`. **Do not mark 0.2.1 as
   pre-release.** Verify `agentself --version` JSON has the package version
   and `"cli": 2`.

Create the GitHub release from a notes file
(`gh release create vVERSION --notes-file PATH`), not an interpolated
PowerShell or cmd string.

If notes include a fenced install snippet, the closing fence must be on its
own line. Put a blank line between the closing fence and the next sentence.
Do not write a closing fence of backtick-backtick-backtick followed by n.

Example:

```bash
uv tool install agentself
```

Then verify the published CLI.

Do not re-register Trusted Publishing unless the workflow path or repo
identity changed. Do not replace Trusted Publishing with a long-lived PyPI
API token. Keep the release tag and package version identical.
