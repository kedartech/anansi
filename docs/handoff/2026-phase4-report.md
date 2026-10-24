# Realism Phase 4 report: prose, counsel and the aide

Branch `feat/realism-phase4`, base `2468cba34ef3b55941777c8314781e4a5220826f` (recorded with `git rev-parse HEAD` after checkout). Built on 2026-10-23 in a Claude Code cloud session.

## 1. Changes

- `34a89c4` chore: move the tick emitter and log tail to engine/emit.py (item 1)
- `a9ea23d` feat: a human's final step resolves its order in the same batch (item 2)
- `883694a` feat: document_written, the person projection and the prose number check (schema 13) (item 3)
- `6559c4b` feat: the PROSE stage writes each author's pages once per wake, from views only (item 4)
- `1311aa0` feat: consult a person on your card, from their projection only (item 5)
- `50cb1ca` feat: the aide briefs a seat from its view and drafts orders it never files (item 6)
- `3a659ee` feat: wiring gate for prose, counsel and the aide; cost per in-world day (item 7)
- `693770f` feat: P0 prose A/B harness that refuses to run without --live (item 8)
- `27b90c6` docs: consult, aide and the PROSE stage in the entry-point inventory (item 9)
- `b391846` chore: price hand-written recordings at each model's list rate (fixture fix-up)
- this report (last commit)

## 2. Frozen decisions applied

- **One prose call per author batch per wake.** `realism_tick._prose` groups pages without prose by author and calls the writer once per author. Tested in `test_one_call_per_author_per_wake`.
- **The model sees only the page.** `ProseWriter.write(author: Author, pages: Sequence[DocumentView])`; `Author` holds the name, office and card voice only. Passing a `State` or a `DocumentTruth` fails mypy and raises `TypeError` at runtime. The number post-check (`core/realism/prose.invented_numbers`) drops prose that states a figure not on the page and logs why.
- **Prose lands as an event.** `DocumentWritten(doc_id, prose)` is scoped like the document and folded into its view. A second write for the same page is a `FoldError`. Pages with no prose keep their body lines.
- **Cached prefixes.** The prompt file comes first as the system prompt, then the author or person block (voice), with the batch-specific pages or question last. `prompt_version` for `document_prose` and `counsel` is stamped into `Versions.prompt` on the events those calls produce.
- **Prose model.** `PROSE_MODEL = "claude-sonnet-5-5"` [proposed]. `REVISIONIST_MODEL_PROSE` overrides it through the existing `_model_for`.
- **Offline provider.** `tick --offline` uses `TemplateProseWriter`, which costs nothing and adds no numbers.
- **Counsel.** `revisionist consult <person> "<question>" [--seat]` uses the card's channel to that person and the pure `core/realism/person.project_person`. The result is a `CounselHeld` private to the seat (sealed with the seat key under the existing rules) plus a `CostLogged`. It costs no in-world days, changes no state apart from `last_seq`, and files no orders.
- **The aide.** `revisionist aide [--seat] [--offline]` returns a summary of unread pages, the decisions due and up to 3 drafts. Drafts are printed as `revisionist lever ...` commands and never filed. Drafts that do not fit the lever specs are dropped. A model summary that states a number not on the unread pages falls back to the template summary.
- **Costs.** PROSE costs go on that stage's `TickStageCompleted.costs` and are summed into `TickClosed.costs`. Counsel and aide costs go on `CostLogged`. `revisionist cost` adds `$X per in-world day over N days (start to today)` for realism campaigns.
- **Crash safety.** PROSE has its own marker. Stopping after FEEDS and then resuming reaches the same state hash as an uninterrupted run, and each page is written once. A `TransportError` leaves the affected pages bare, and the tick still closes.

## 3. Deviations

- **Branch (environment).** The session's harness named the branch `ccr-0a33ffda-49qqnz`. Per hard rule 1, I pushed only `feat/realism-phase4`. Nothing was pushed to the harness branch.
- **`Proposal` moved to `engine/emit.py` (agent choice).** `_Tail` holds `Proposal`, so `Proposal` had to move with it to avoid a circular import. `engine/tick.py` re-imports it and keeps `_Emitter`, `_Tail` and `_tail` as aliases, because `tests/test_tick.py` uses them and had to pass unedited.
- **How the tick avoids resolving twice (agent choice).** `take_step` now stamps its batch with `actor=<issuer>`. `_unresolved` only picks up enacted settlements with no actor, which are logs written before this change. No schema change was needed.
- **PROSE runs only when a writer is passed (agent choice).** `run_realism_tick(..., prose=None)` keeps the old four-stage tick, so the existing realism tests pass unedited. `play_tick` (CLI and scheduler) always passes a writer. One consequence: a realism tick run by `revisionist tick` without `--offline`, or by the scheduler, now makes live prose calls.
- **Which pages PROSE covers (agent choice).** It covers pages issued since the last `tick_closed`, so pages issued between ticks by a human's step are also written.
- **What the post-check allows (agent choice) [proposed].** Allowed numbers are the body lines (values, labels, units), the title, and the day, month and year of the issue and arrival dates. The brief said "in the body". Letters naturally date themselves, so without this every dated letter would be rejected. Numbers written as words are not checked.
- **What a person holds (agent choice) [proposed].** A person holds the pages they wrote, pages from the feeds on their own card, and pages scoped to a seat that embodies them, each once it has arrived. A person without a seat therefore holds only what code issued for someone else's seat. For example, Hopkins after two ticks holds one Soviet request letter, which has no figures.
- **Aide inputs (agent choice).** Besides the `View`, the aide takes `state.pipelines` (to tell which step an order is at, as the web desk already does) and the seat's `document_read` marks.
- **Counsel text (agent choice).** `CounselHeld.text` stores "You asked: <question>" followed by the reply.
- **"Saved as an in-tray item".** The `counsel_held` record is that item. `engine/counsel.counsel_records()` lists them. No in-tray UI exists yet, since that is Phase 5.
- **Hand-written recordings.** These are `tests/recordings/realism-{prose-toy,counsel-autumn,aide-autumn,prose-ab}.json`, generated by `python -m tests.handwritten` from hand-written replies. Every entry carries `hand_written: true`. The A/B replies are 10 hand-written texts, one per model and page. Token counts and `usd` are estimates (characters ÷ 4, at list price).
- **Cost per in-world day lives in the wiring commit.** The brief listed it under Frozen decisions with no work item of its own, so it went into the item 7 commit.
- **`docs/build-plan.md` unchanged.** It does not track realism phases.
- **Test helpers touched:** `tests/strategies.py` (a `DocumentWritten` strategy) and `tests/test_schema_version.py` (fingerprint "13"). I also added tests to `tests/test_realism_wiring.py`. No existing test was edited.
- **New test-only modules:** `tests/prose_scenario.py`, `tests/counsel_scenario.py`, `tests/handwritten.py`.

## 4. Gate results

Each gate was run on its own on `b391846`:

- `uv lock --check`: `Resolved 48 packages in 1ms` (exit 0)
- `uv sync --frozen --all-extras`: `Checked 45 packages` (exit 0)
- `uv run --frozen ruff check .`: `All checks passed!`
- `uv run --frozen ruff format --check .`: `221 files already formatted`
- `uv run --frozen mypy`: `Success: no issues found in 190 source files`
- `env -u REVISIONIST_TEST_PG_ADMIN_DSN -u REVISIONIST_REQUIRE_PG uv run --frozen pytest -q -p no:cacheprovider -o addopts=`: `895 passed, 271 skipped, 2 xfailed, 23 warnings in 47.28s`
- `uv run --frozen revisionist world check autumn-1941`: `0 problems, 37 gaps, 40 assumed`
- `uv run --frozen revisionist world check realism-toy`: `0 problems, 0 gaps`

Baseline on `2468cba` in this container: `860 passed, 271 skipped, 2 xfailed`, the same as the brief's figure. The 271 skips are the Postgres-backed tests, which skip without `REVISIONIST_TEST_PG_ADMIN_DSN`. `SCHEMA_VERSION` went from "12" to "13", with one bump in `883694a`.

## 5. NOT RUN

- **The live P0 A/B run.** It needs live model calls, and the owner reads the sheet blind.
- **Any live prose, counsel or aide call.** Every model path ran against hand-written recordings only.
- **Postgres-backed tests (271 skipped).** No database was set up. `pg_dump`, `pg_restore` and `psql` are present in the container; `rsync` is not.
- **The browser smoke run** (`web/test/e2e/smoke.py`).

## 6. Open decisions for the owner

- The default prose model: `claude-sonnet-5-5` for now [proposed]. The A/B decides.
- Counsel model `claude-sonnet-5-5` and aide model `claude-haiku-5-5` [proposed].
- Counsel costs no in-world days [proposed].
- What the post-check allows: body, title and date parts. Numbers written as words are not checked [proposed].
- What a person holds, which determines what counsel can draw on [proposed]. Should an adviser such as Hopkins see the President's in-tray?
- A realism tick run by the scheduler or by `tick` without `--offline` now calls the prose model. Should the scheduler default to the template writer [proposed]?
- Per-call budgets: prose $0.25, counsel $0.25, aide $0.10 (`max_usd`) [proposed].

## 7. The live A/B command and its expected cost

```
uv run --frozen python scripts/prose_ab.py --live --out prose-ab
```

This writes `prose-ab/sheet.md` (blind) and `prose-ab/key.json` (open after reading). It makes 10 calls (5 pages × 2 models) through `claude -p`.

Expected cost at list price (Haiku 5.5 $0.10/$0.50, Sonnet 5.5 $2/$10 per million tokens), using about 440 input and 50–120 output tokens per call from the hand-written fixtures:

- Haiku: about $0.0003 for 5 calls.
- Sonnet: about $0.008 for 5 calls.

Allow up to $0.05 in total for longer replies and CLI overhead. On the host's Claude subscription the actual charge is $0. These are estimates; the run's `key.json` records the real per-call cost.
