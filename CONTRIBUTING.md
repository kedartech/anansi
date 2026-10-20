# Contributing

Builders: Josh and Vera (README §3 #17).

## Rules

- Josh owns commits and pushes. Edit and stage freely; commit or push only when he says so.
- Commit messages: imperative, no trailing period, prefixed `docs:` / `feat:` / `bug:` / `chore:`.
- Small, reviewable diffs. Comments only for a non-obvious *why*.
- `anansi.core` is pure: no I/O, clock, network, model calls or unseeded randomness. `tests/test_purity.py` enforces it and CI runs it on every PR.
- No live model calls in CI. Model-backed layers ship recorded fixtures.
- Leak tests (type-level projection, canary, counterfactual, human-boundary) are never skipped or marked xfail.

## Gates

```
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest --cov=anansi.core
```

The build order and the tests each layer must ship are in `docs/build-plan.md`.
