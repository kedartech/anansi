# Contributing

Builders: Josh and Vera (spec §3 #17).

## Rules

- Josh owns commits and pushes. Edit and stage freely; commit or push only when he says so.
- Commit messages: imperative, no trailing period, prefixed `docs:` / `feat:` / `bug:` / `chore:`.
- Small, reviewable diffs. Comments only for a non-obvious *why*.
- `revisionist.core` is pure: no I/O, clock, network, model calls or unseeded randomness. `tests/test_purity.py` enforces it and CI runs it on every PR.
- No live model calls in CI. Model-backed layers ship recorded fixtures.
- Any change to the schema's shape (events, deltas, ontology, State, View) bumps `SCHEMA_VERSION` in `src/revisionist/core/__init__.py` by one and adds the new fingerprint to `tests/test_schema_version.py`, in the same change as the regenerated `schema/revisionist.schema.json`. CI fails a shape change without a bump.
- Leak tests (type-level projection, canary, counterfactual, human-boundary) are never skipped or marked xfail.

## Gates

```
uv lock --check
uv sync --frozen            # the dev group carries every extra: postgres, discord, seal
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy
uv run --frozen pytest --cov=revisionist.core
```

Run each one on its own; a pipe can hide a failing exit code.

**Postgres tests** run against a real database when `REVISIONIST_TEST_PG_ADMIN_DSN` is a superuser `postgresql://` URL (for example a throwaway local cluster or `docker run -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:16`). The suite creates and drops its own scratch databases and app role. Without the variable they skip, unless `REVISIONIST_REQUIRE_PG=1`, which CI sets so a missing database fails instead. The backup and restore-drill test also needs `pg_dump`, `pg_restore`, `psql` and `rsync`.

The build order and the tests each layer must ship are in `docs/build-plan.md`.
