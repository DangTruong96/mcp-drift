## What this changes

<!-- One change per PR. Link the issue if there is one. -->

## Checklist

- [ ] Tests added/updated and `python -m unittest discover -s tests` passes
- [ ] No new runtime dependencies (stdlib only)
- [ ] No path for secrets to enter a lockfile was introduced
- [ ] Exit-code contract preserved (0 clean / 1 drift / 2 error)
- [ ] CHANGELOG.md updated; lockfile format version bumped + migration
      note if the format changed
- [ ] Docs (README / docs/USAGE.md) updated if behaviour changed
