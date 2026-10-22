# Revisionist

[![ci](https://github.com/kedartech/revisionist/actions/workflows/ci.yml/badge.svg)](https://github.com/kedartech/revisionist/actions/workflows/ci.yml)

## TL;DR

- **What:** an AI-run multiplayer statecraft game. Players pick a moment in history, take on a real person from it, and pursue a secret goal. An AI game master builds the world from the record and runs everything nobody is playing.
- **How it plays:** you give orders to your own agent in plain language. A deterministic referee decides what succeeds. A model narrates the result.
- **Status:** pre-release. The engine, Postgres/scheduler and Discord play are built. The first live two-human Discord run has not happened, and there is no public release.
- **Try it in five minutes:** `uv sync --frozen`, then `uv run revisionist new salt-strait` and `uv run revisionist tick --offline`. No Discord, Postgres or model calls needed. See [Quickstart](#quickstart).
- **Read more:** the rules and decisions are in [`docs/game-spec.md`](docs/game-spec.md).

Think of a gamified Model UN run like a D&D campaign. The event log is the one record nobody can rewrite. Everything else is open to revision: who did what, what it meant, and what the history books will say.

> **Status:** pre-release. The engine, the Postgres/scheduler layer and Discord play are built and tested. The first live two-human run on Discord has not happened yet, and there is no public release.

## Features

- **Historical Session Zero.** The host gives a pitch of up to 200 characters, as loose as "WWII" or as exact as a place, date and person. The reply is a private setting card with 2-3 start points, each with the history it would follow. The host confirms one, strikes history items, and sets an end date and a real-life length. Then each player chooses a real character and a secret goal. A world bible is drafted from the historical record and validated before play begins.
- **Secret goals and a win state.** Goals are checked in code against the world, not by a model. Players can investigate or forge rivals' goal information. A season ends with a full reveal.
- **Information opacity.** Each seat sees only a projection of the world. Leak tests (type-level, canary, counterfactual and human-boundary) run on every change.
- **Deterministic core.** `revisionist.core` is pure: no I/O, clock, network or unseeded randomness. Replaying the log always rebuilds the same state.
- **Model-agnostic pipeline.** Models propose, a closed set of validated deltas applies. A model never writes state directly.
- **Solo or multiplayer.** Run it from the CLI, or on Discord with one or more humans, AI factions filling the other seats.
- **Sealed at rest.** Postgres campaigns can be encrypted with a campaign key plus a key per seat for that player's own words, with audited admin reads and player erasure by key deletion.

## Quickstart

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/kedartech/revisionist.git
cd revisionist
uv sync --frozen

uv run revisionist new salt-strait     # start a campaign in a bundled world
uv run revisionist status              # where it stands, as your seat sees it
uv run revisionist tick --offline      # resolve a tick with fixtures, no model calls
uv run revisionist leaks               # confirm no secret reached a reader
```

The campaign is a local SQLite file, so you can delete it and start over. Anything that uses a model (`order`, `briefing`, and `tick` without `--offline`) calls the [Claude CLI](https://docs.anthropic.com/en/docs/claude-code) as a subprocess, so `claude` must be on your `PATH` and logged in.

```sh
uv run revisionist briefing            # a summary, a dilemma and three suggested moves
uv run revisionist order "Open talks with the Lune Isles on the salt tariff"
uv run revisionist tick                # live tick: real model calls
uv run revisionist why                 # trace an order through the roll, deltas and narration
```

Run `uv run revisionist --help` for the full command list, including Session Zero (`establish pitch`, `establish confirm`, `character`, `goal`, `begin`), `season`, `replay`, `cost`, `db`, `admin` and `scheduler`.

## Documentation

| Document | Contents |
|---|---|
| [`docs/game-spec.md`](docs/game-spec.md) | Game rules, parameters and decisions |
| [`docs/design.md`](docs/design.md) | Event log, visibility, pipeline and the rules the code must keep |
| [`docs/build-plan.md`](docs/build-plan.md) | Layer-by-layer build plan and what is built |
| [`docs/deploy.md`](docs/deploy.md) | Running on a server: Postgres, systemd, Discord, backups |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Rules and gates for contributors |

## Layout

```
src/revisionist/core/     pure game logic: events, state, projection, referee, goals
src/revisionist/engine/   tick orchestrator, model adapters, Session Zero, scheduler, Discord
worlds/                   bundled world bibles (salt-strait, salt-strait-duo)
schema/                   exported JSON Schema for the event log
deploy/                   systemd units and backup scripts
tests/                    unit, property, leak and recorded-fixture tests
```

## Development

```sh
uv sync --frozen
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy
uv run --frozen pytest --cov=revisionist.core
```

CI runs these gates on every pull request, with no live model calls. Postgres tests need a database; see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT, as declared in `pyproject.toml`.
