# Revisionist design: log, state, boundary, pipeline

Build plan Layer 1. Written 2026-10-19 against [README.md](../README.md) v2. The models in `src/revisionist/core/` are the schema; this document explains them and names the rules the code must keep. Where the two disagree, the code is wrong and this doc wins until the code is fixed.

Status legend: **[decided]**, **[proposed]**, **[open]**.

Module map:

| Module | Holds |
|---|---|
| `core/ids.py` | typed IDs |
| `core/visibility.py` | `Visibility`, `Scope` |
| `core/settings.py` | Session Zero settings the engine reads |
| `core/ontology.py` | Entity, Asset, Relationship, Meter, Clock, Fact, ScheduledEvent, Commitment, Seat |
| `core/intent.py` | `Verb`, `Intent` |
| `core/goals.py` | secret goals: the `WinCondition` union, `goal_met`, goal kinds, winners (Layer 10) |
| `core/intel.py` | goal pieces from investigations, decoys from forgeries (Layer 10) |
| `core/delta.py` | the `Delta` union |
| `core/resolution.py` | bands, tiers, stages, rejection reasons, starting odds |
| `core/events.py` | every event kind, the `Event` envelope |
| `core/state.py` | `State` (truth) and `View` (one seat's knowledge) |
| `core/fold.py` | `apply`, `replay`, `replay_to`, `state_hash` (Layer 2) |
| `core/project.py` | `project(state, seat) -> View` (Layer 2) |
| `core/referee.py` | `validate`, `rate`, `roll`, `clamp`, `cap_meters`, `reveal`, `slots` (Layer 3) |
| `engine/tick.py` | `run_tick`, `file_intent`; provider protocols `Resolver`, `Narrator`, `Deliverer` (Layer 4) |
| `engine/providers.py` | fixture providers: scripted/failing resolver, template narrator, recording deliverer, keyword safety (Layer 4) |
| `engine/llm.py` | `ClaudeResolver`, `ClaudeNarrator`, `ClaudeCli` transport, `RecordedTransport` (Layer 5) |
| `engine/agent.py` | `Agent`: `structure`, `brief`, `perceived_odds`, `deputy_turn`; `receive_order` (Layer 6) |
| `engine/prompts/*.md` | versioned prompt files; content hash in `Versions.prompt` (Layers 5-6) |
| `core/canary.py` | canary scan over narrations and deliveries (Layer 5); the human-boundary scan (Layer 9b) |
| `engine/store.py` | `EventStore` protocol, `MemoryStore`, `SqliteStore`, `load_state` (Layer 2) |
| `engine/pg_store.py`, `engine/migrations/*.sql` | `PostgresEventStore`, `migrate` (Layer 8) |
| `engine/maintenance.py` | `copy_campaign`, `verify_campaign` (Layer 8) |
| `engine/runner.py` | `play_tick`: deputy turns, `run_tick`, leak scan; shared by `revisionist tick` and the scheduler (Layer 8) |
| `engine/scheduler.py` | the tick scheduler: pure `due` / `next_due_after` / `all_submitted`, the `schedule` table, `run` (Layer 8) |
| `engine/play.py` | `give_order`, `get_briefing`, `status_lines`: what a seat does between ticks, shared by the CLI and the bot (Layer 9a) |
| `engine/discord/` | Discord transport: REST deliverer and outbox, routing deliverer and public poster, operational tables (seats, invites, channels), command handlers, the gateway bot, the server template (Layers 9a-9b) |
| `core/sealing.py`, `engine/seal.py` | which key seals an event and what needs an admin read (pure); AES-GCM sealing, keyrings, the master key (Layer 9b) |
| `schema/revisionist.schema.json` | exported JSON Schema; CI fails on drift |

---

## 1. The log is canon **[decided]**

README #19. There is no state table. There is an append-only sequence of `Event` records per campaign, and `State` is what you get by folding them. Anything the game knows, it knows because an event said so.

Consequences the code keeps:

- `Event.seq` is dense per campaign. The store (Layer 2) refuses gaps, repeats, updates and deletes.
- Nothing is ever deleted from the ontology. Entities, assets, clocks and commitments change `status`; they do not vanish. A dead minister is `Entity(status=dead)`, still in the log, still in the state, still citable by a journalist.
- Every event carries `versions` (engine, schema, prompt, model) so a replay can tell which rules produced it.
- Two classes of event: **mutating** kinds that the fold reads, and **record** kinds (narration, rolls, proposals, rejections, admin reads) that exist for `/why`, replay and audit but change no state. `events.MUTATING_KINDS` is the list; a test keeps it a subset of the real kinds.
- `payload_enc` is reserved on every envelope. Since Layer 9b it holds, at rest only, the ciphertext of a sealed event (§16.6); every event a reader sees has it empty.

### Event kinds in v1

| Group | Kinds |
|---|---|
| Creation | `campaign_created`, `settings_set`, `entity_created`, `asset_created`, `relationship_set`, `meter_created`, `clock_created`, `fact_asserted`, `commitment_created`, `seat_created`, `agent_assigned`, `seat_status_set` |
| Orders | `order_received`, `intent_filed`, `intent_rejected`, `clarification_asked` |
| Resolution | `feasibility_rated`, `rolled`, `delta_proposed`, `delta_rejected`, `delta_applied`, `operation_opened`, `operation_resolved`, `scheduled_event_fired` |
| Output | `narration_written`, `briefing_delivered` |
| Tick | `tick_opened`, `tick_stage_completed`, `tick_closed` |
| Cost | `cost_logged` (model calls outside a tick; schema v2) |
| Admin | `admin_read` |
| Goals | `goal_set`, `goal_progressed`, `goal_met`, `goal_undone` (schema v6) |
| Session Zero | `session_zero_opened`, `character_listed` (schema v7) |

Later layers add `vote_*`, `director_event_proposed`, `agent_harmed`, `seat_fallen`, `player_joined`, `key_attached`, `safety_refused`. Each addition bumps `core.SCHEMA_VERSION`.

---

## 2. Visibility: the knowledge ACL **[decided]**

README §7. Every event, fact, asset, clock, commitment and intent carries a `Scope`:

```
Scope(visibility: public | restricted | private | covert, known_by: frozenset[SeatId])
```

- `public`: everyone. `known_by` is ignored.
- `restricted`, `private`, `covert`: visible to `known_by` only. A non-public scope with an empty `known_by` is a validation error; nothing can be known by nobody.
- `covert` additionally means "actively concealed", which is what gives it an exposure roll.

`Scope.visible_to(seat)` is the single predicate `project()` uses. `Scope.grant(*seats)` is the single way knowledge widens; `KnowledgeGrant` deltas, exposure rolls, leaks and `reveal()` all call it. There is no second mechanism.

The ACL is **per seat**, not per human. README §7's human boundary (one person must never see two seats' secrets) is a Layer 9 concern enforced at the Discord edge, and the human-boundary canary tests it.

---

## 3. The ontology **[decided]**

README §12. Fixed and world-agnostic. Session Zero maps a setting's nouns onto these types and never extends them. If a concept does not fit, it is a Meter, a Fact or a Clock; it is not a new type.

| Type | What it is | Key fields |
|---|---|---|
| `Entity` | polity, faction, person, org, location | `kind`, `status`, `parent`, `location` |
| `Asset` | Force, Wealth, Network, Office, Info, Facility | `tier` 0-5, `controller`, `loyalty` -5..+5, `scope` |
| `Relationship` | a disposition between two entities | `disposition` -5..+5, `price_tags`, `leverage_tags` |
| `Meter` | a bounded number on an entity | `value`, `lo`, `hi`, `max_delta_per_tick` |
| `Clock` | a thing in progress | `kind`, `segments_total` 1-12, `segments_filled`, `exposure` 0-5, `holds_slot` |
| `Fact` | a structured truth with an ACL | `subject`, `predicate`, `obj`, `truth`, `exposure`, `scope` |
| `ScheduledEvent` | something that fires at a tick | `fire_at_tick` |
| `Commitment` | a deal with typed clauses | `parties`, `clauses`, `status` |
| `Seat` | anything with a view of the world | `kind`, `controller`, `slots`, `agent`, `persona`, `objectives` |

Points that matter:

- **Assets carry a scope.** A secret army is a Covert `Force`. "Who knows this exists" is on the asset, not a separate fact.
- **Facts are structured, never prose.** `predicate` comes from the world bible's closed vocabulary; `obj` is an ID or a capped `Label`. A forgery is a `Fact(truth=False)` placed in someone's view, so disinformation is first-class and the resolver can rate against what a seat believes.
- **An Operation is a Clock** with `kind=operation`, an `owner` seat and `holds_slot=True`. Slots (README #25) are counted by summing open operation clocks per seat; there is no separate slot ledger.
- **Objectives are Clocks** too (`kind=objective`), so Goals mode (README #22) scores clock state and needs no second mechanism.
- **Capability clocks** (`kind=capability`) are the abstraction for the dangerous-capability list (README §19): the engine tracks progress; narration never describes procedure.
- **Seats are not people.** Human players, AI factions, press outlets and NPCs are all `Seat`s because they all need `project()`. Only `kind=player` seats hold slots and a persona. A faction is a seat with `controller=ai`, run by an agent in permanent deputy mode (build plan decision 1).
- **The player's agent is an Entity** (`kind=person`) referenced by `Seat.agent`. `agent=None` on a player seat means blinded (README §10): public feed only, one slot, no deputy.
- **`Fact.canary`** is a test-only field: fixtures put a unique string there and the leak scan looks for it in outbound text. Play never sets it.

---

## 4. The agent boundary and `project()` **[decided]**

README §7 and §10. The one rule that makes secrets possible:

> Anything that writes prose, suggests a move, or acts for a seat receives a `View`, never a `State`.

`State` and `View` are different classes (`core/state.py`). A narrator, agent, faction, outlet or NPC function is typed to take `View`; passing a `State` is a `mypy --strict` error before it is a leak.

```
project(state: State, seat: SeatId) -> View     # Layer 2, pure
```

`project` keeps an item when `item.scope.visible_to(seat)`. For intents, a view holds only the viewer's own pending intents (`View.own_pending`); other seats' intents are never in any view. Settings and the tick counter are always visible. Every seat appears in a view only as a `SeatFace` (ID, kind, name, the entity it embodies); the viewer's own seat is `View.me`, in full. How any other seat is run (human or AI, its agent, persona, status, slots, orders) is unreachable by type (Layer 9b, §16.2).

The layering:

```
player text ──> Agent.structure(view, text) ──> Intent | ClarifyingQuestion
                        │
                        └── holds View only; never State

Intent ──> referee (rate / validate / roll / clamp) ──> resolver (State + intents) ──> Deltas
                                                                 │
                                                                 └── the one place truth meets a model

applied Deltas ──> project(state, seat) ──> Narrator(view, deltas-in-view) ──> text for that seat
```

Properties the tests keep:

- **Projection soundness** (Layer 2 property test): nothing in `project(state, s)` has a scope that excludes `s`.
- **Perceived odds are the same function on a different input**: `rate(project(state, seat), intent)` versus `rate(state, intent)`. `FeasibilityRated` records both so `/why` can show the gap.
- **The resolver never sees raw text.** `OrderReceived` is logged Private to the seat and is read by the agent only. The resolver's input type is `tuple[Intent, ...]`.
- **Counterfactual leak test** (Layer 6): remove one covert fact, re-run every other seat's deputy with the same seeds, and their intents must not change.

---

## 5. Intents: the closed verb list **[decided]**

`core/intent.py`. An `Intent` is `(actor, verb, targets, means, counterparty, duration, scope, spin, condition)`. Nothing else. The verb list is closed:

| Group | Verbs |
|---|---|
| Means | acquire, transfer, fund, recruit |
| Deals | offer, accept, withdraw, negotiate |
| Force | attack, defend, sabotage |
| Information | investigate, leak, forge, conceal |
| Politics | lobby, legislate, appoint, dismiss, declare |
| Movement | travel, wait |

- `FREE_VERBS` (negotiate, offer, accept, withdraw, declare, wait) never hold a slot: talk is free (README #25).
- `IRREVERSIBLE_VERBS` (declare, transfer, dismiss) are what a deputy will not do without a standing order naming them (README §9).
- `means` lists assets the actor is committing. Validation fails with `means_not_held` when the actor's entity does not control them. This is the "claim an army" defence (README §10) and it lives in the referee, not in a prompt.
- `spin` is the one free-text field, capped at 280 characters, and it is always Public: it is a statement to the world, never an instruction to the engine.

Adding a verb is a schema change: bump `SCHEMA_VERSION`, add a fold case if it needs one, add it to the strategies, note it in the changelog.

---

## 6. Deltas: the only thing a model may propose **[decided]**

`core/delta.py`. A discriminated union of 18 typed changes, each against an existing ID (or creating a new object with an ID the engine minted). Every delta carries a `reason` capped at 200 characters for `/why`.

| Delta | Changes | Hard bound in the type |
|---|---|---|
| `asset_tier` | an asset's tier | ±2 |
| `asset_status` | active / degraded / lost | enum |
| `asset_controller` | who holds an asset | ID |
| `asset_create` | a new asset | tier 0-1 only |
| `meter` | a meter value | clamped per meter at apply |
| `disposition` | a relationship | ±2 |
| `relationship_tags` | price and leverage tags | capped labels |
| `clock_advance` | segments | ±2 |
| `clock_create` | a new clock | 1-12 segments |
| `clock_status` | failed / abandoned; resolved only by filling | enum |
| `clock_exposure` | exposure | ±2 |
| `fact_assert` | a new fact | structured; capped predicate |
| `knowledge_grant` | widen a scope | IDs only |
| `entity_status` | active / captured / turned / dead / dissolved | enum |
| `commitment_create` | a new commitment | 1-6 clauses |
| `commitment_status` | accepted / broken / expired / withdrawn | enum |
| `schedule` | queue a future event | 1-24 ticks ahead |
| `seat_slots` | a seat's slot count | ±2 |

The type bounds are generous on purpose; the **clamp table** (build plan Layer 3, accepted as starting numbers) is tighter and is applied in code per outcome tier. A delta that parses but exceeds the clamp is cut to the limit and logged as `delta_rejected(reason=clamped)` alongside the applied remainder.

A test (`test_deltas_have_no_free_form_fields`) walks the JSON schema of every delta and fails on any object-typed field or any uncapped non-ID string. That test is the mechanical form of README §12's "typed deltas, no free-form state writes".

---

## 7. `reveal()` **[decided]** (build plan decision 4)

Chapter breaks and season ends (README §15) widen the ACL on past covert facts. There is no reveal event kind. `reveal()` is a pure function:

```
reveal(state: State, policy: RevealPolicy, tick: int) -> tuple[KnowledgeGrant, ...]

RevealPolicy = Partial(fraction: float, min_age_ticks: int) | Full
```

It selects covert facts and clocks older than `min_age_ticks`, picks `fraction` of them deterministically (seeded from `campaign_id` and `tick`), and emits one `KnowledgeGrant(make_public=True)` per item. The orchestrator applies those grants like any other delta, so the log shows exactly what was revealed, when and why. `Partial` at chapter breaks, `Full` at season end, per README §15. Lands in Layer 3 with the rest of the pure core.

---

## 8. The four-stage pipeline **[decided]**

README §13, revised under H3/H15: **Structure lives in the agent**, so the referee's pipeline starts from an `Intent`.

```
0. Structure   (agent, Layer 6)      text -> Intent | ClarifyingQuestion        logs intent_filed / clarification_asked
1. Validate    (referee, pure)       means held? target exists? slot free? safety floor?   logs intent_rejected
2. Rate        (referee, pure)       FeasibilityBand from means, reach, disposition, price, plausibility   logs feasibility_rated (true + perceived)
3. Roll        (referee, pure)       seed = blake2b(campaign_id, tick, intent_id) -> OutcomeTier + exposure roll   logs rolled
4. Propose     (resolver, LLM)       State + intents + outcomes -> Deltas       logs delta_proposed
5. Clamp+Apply (referee, pure)       cut to the clamp table; fold into state    logs delta_rejected / delta_applied
6. Narrate     (narrator, LLM)       View + deltas-in-view -> text per seat     logs narration_written
```

Starting odds (`core/resolution.py`): Impossible 0%, Long shot 20%, Contested 50%, Likely 75%, Assured 95% chance of a success tier; within success, Partial 40 / Success 45 / Opportunity 15. Exposure rolls independently against the clock's `exposure` (0 / 5 / 10 / 20 / 35 / 50 percent for exposure 0-5, `EXPOSURE_CHANCE`); a hit grants knowledge to one investigator seat, not to the public. Escalation to public only through further investigate or leak intents.

### 8.1 The referee in code (`core/referee.py`) **[decided]**

- **`validate(state, intent, safety=None) -> Rejection | None`.** In order: actor is a seat and not fallen; not a duplicate ID; outlets may only investigate, leak, declare, negotiate, wait; blinded seats may not use irreversible verbs; every target exists and is not dead, dissolved, lost or closed; every `means` asset is controlled by the actor's entity or a direct child (the "claim an army" case, README §10); transfer names an asset; accept names an offered commitment; a slot is free (operations plus queued intents, cap from the seat, 1 when blinded); finally the safety hook. The hook runs last so a classifier never sees an intent the rules already refused.
- **`rate(world, intent) -> FeasibilityBand`** takes `State` or `View`. Impossible only for structural reasons (unknown target, means not held). Free verbs are Assured. Otherwise a score from 0: committed means tier minus opposition tier (clamped ±2; Force verbs count only Force assets), counterparty disposition ±1 at |disposition| ≥ 2, plausibility ±1 on force and information verbs. Score ≤ −2 Long shot, ≤ 0 Contested, 1 Likely, ≥ 2 Assured. A means verb with no means is capped at Long shot. The fixture test: South attacking North looks **Likely** from South's view and is **Contested** in truth, because the hill irregulars are covert.
- **`roll(seed, band, exposure) -> Roll`.** `seed = blake2b(campaign_id, tick, intent_id)`; the outcome and exposure draws are bytes of `blake2b(seed)`, so the core never imports `random`. 2,000-seed tests hold every table within ±4 points.
- **`clamp(state, intent, proposal, outcome) -> Clamped`.** Returns `kept` (applied as-is or trimmed to the limit) and `cut` (one entry per trim or drop, for `delta_rejected`). The table: tier ±1 on at most 2 assets; a failure only lowers the actor's own assets and touches no one else's; meter changes **summed per meter per tick** against `max_delta_per_tick`; disposition ±1, ±2 when the intent is public and names the pair; clock segments 0/1/2/2 by outcome with a new clock allowed only on failure (complication) or opportunity; operation clocks are opened by the engine, never proposed; `dead`/`dissolved` only under hard or brutal lethality and only when a threat clock targets the entity; captured/turned/dead on a seat's agent only when `agent_harm` is on; `make_public` grants only from leak or declare; slots ±1; at most 6 objects per intent. Property test: idempotent and never widens a magnitude.
- **`reveal(state, policy, tick)`**: see §7. Candidates are covert facts and clocks older than `min_age_ticks`; `Partial` keeps `ceil(fraction · n)` of them ordered by a hash of (campaign, tick, id), so replay reveals the same set. Grant IDs are `derived_id(campaign, tick, "reveal", target)`.
- **`slots(world, seat) -> Slots(used, queued, cap)`.** Operations and objectives hold slots by owner; outlets and NPCs have cap 0.

Rules the referee keeps regardless of what the model proposes:

- A proposal is a request. Only `delta_applied` events change state, and only after clamp.
- `entity_status=dead|dissolved` is applied only when `Settings.lethality` allows it and the target had a clock at risk (`lethality_disallows` otherwise).
- Fail-with-complication may only lower the actor's own assets.
- A vote outcome can never produce a `cancelled` operation (`vote_cannot_cancel`), per README #14.
- Nothing for an absent seat is irreversible outside a formal session (Layer 10 item 2).

---

## 9. The tick state machine **[decided]**

README §13. Six stages, each persisted as `tick_stage_completed(stage)`, so a crash resumes from the last completed stage and never re-runs a model call that already landed.

```
FREEZE   tick_opened(intents_frozen)   pending intents move to frozen; late orders go to the next tick
PROPOSE  validate, rate, roll, resolver call; one retry; on failure the tick is quiet and intents carry
VALIDATE parse and clamp every delta; drop and log the rest
APPLY    one transaction: every delta_applied for the tick lands or none does; scheduled events fire
NARRATE  one narration per human seat and per outlet, from its own View
DELIVER  outbox with idempotency keys; briefing_delivered only after the send succeeds
CLOSE    tick_closed(costs, state_hash)   snapshot written; replay must reproduce state_hash
```

`state_hash` is blake2b-128 over the canonical JSON of `State` (sorted keys, compact separators, set fields serialised sorted). The fold checks it at every `tick_closed` and raises `FoldError` on a mismatch, so a corrupted or edited log fails loudly on replay. The Layer 8 SQLite-to-Postgres migration test compares it too.

The fold (`core/fold.py`) is total over `MUTATING_KINDS` and refuses: sequence gaps, wrong campaign, duplicate creation, unknown IDs, and a hash mismatch. Type bounds (tier 0-5, meter lo-hi, disposition -5..+5, slots 0-cap) are clamped per step in the fold so a replay can never produce an invalid object; the Layer 3 clamp table is a tighter policy applied before a delta is ever logged. A clock that fills becomes `resolved` and releases its slot; `clock_status` handles failed and abandoned.

Stores (`engine/store.py`) enforce append-only: `seq` must be exactly the next integer, batches are atomic, and the SQLite schema carries `BEFORE UPDATE` and `BEFORE DELETE` triggers that abort. `load_state` is the newest snapshot plus the tail of the log; a test proves it equals a full replay.

### 9.1 The orchestrator in code (`engine/tick.py`) **[decided]**

`run_tick(store, campaign_id, providers)` is the only function that both reads the store and calls a provider. Each stage is one atomic batch that ends in `tick_stage_completed(stage, costs)`; resuming means reading the tail of the log since the last `tick_closed` and skipping every stage already marked. What a later stage needs from an earlier one is either in the log (`delta_proposed`, `narration_written`, `briefing_delivered`) or a pure function of state (rolls, clamps), so a resumed run reaches the same `state_hash` as an uninterrupted one. Tested by stopping after every stage and comparing against a clean run, and by a 10-tick campaign with two injected crashes whose every recorded hash replays.

- **FREEZE** writes `tick_opened` with the sorted pending IDs, re-validates each frozen intent against the frozen state (`intent_rejected` on failure; the safety hook runs here), then logs `feasibility_rated` (true and perceived band) and `rolled` per survivor. Seeds come from `referee.seed_for`, so the roll is recomputable and never stored anywhere the resolver could change it.
- **PROPOSE** calls the resolver once with the full `State`, the frozen intents and the rolls; one retry, then the tick closes quiet with `quiet_reason` and every intent stays pending. `delta_proposed` per intent carries the proposal verbatim.
- **VALIDATE** recomputes `clamp` per intent and `cap_meters` across the tick, logging a `delta_rejected` per trim or drop. Nothing here needs a model, so a resume recomputes rather than reads.
- **APPLY** logs `delta_applied` for every kept delta, opens an operation clock for each slot-holding intent with `duration_ticks > 1` whose roll was not a failure (`operation_opened`, clock ID derived from the intent), advances every open operation by one segment (`operation_resolved` when it fills), and fires due scheduled events. One batch; all or nothing.
- **NARRATE** builds `project(state, seat)` per human seat and passes the view plus only those applied deltas whose object is in that view. The narrator's signature takes `View`; a `State` is a type error. `narration_written` is logged `private_to(seat)`.
- **DELIVER** sends each narration with key `derived_id(campaign, tick, "deliver", seat)` and logs `briefing_delivered` right after each send, so a crash between two sends resumes with exactly the undelivered seats.
- **close** sums the stage costs into `tick_closed(costs, state_hash)` and writes a snapshot.

`file_intent(store, campaign_id, intent, safety=)` is the filing path: `referee.validate` then `intent_filed` or `intent_rejected`. Both outcomes are logged under the intent's scope. Providers are protocols (`Resolver`, `Narrator`, `Deliverer`, plus the `SafetyCheck` hook); `engine/providers.py` ships the fixture implementations used by the tests and by Layer 7's dry run. A running operation is re-rated and re-rolled each tick (see §9.3), and outlets are narrated from their own view.

### 9.2 Claude adapters (`engine/llm.py`) **[decided]**

- **Transport is the Claude Code CLI** (`claude -p`), not the Anthropic SDK. It runs on the host's Claude subscription, so no API key exists in the repo, its environment, or CI. Every call strips setting sources, tools and hooks (`--setting-sources "" --tools "" --no-session-persistence`); `--bare` is avoided because it also drops the login. The CLI's JSON result carries `structured_output`, the model, token counts and a list-price `total_cost_usd`; those become one `CallCost` on the stage marker. On a subscription the real charge is zero, so `usd` is the list-price shadow, which is what README #32 wants measured. Swapping to the SDK later is one new `Transport` class; the `Request`/`Reply` dataclasses do not change.
- **Resolver** (`ClaudeResolver`, Sonnet tier): the system prompt is `prompts/resolver.md`; the user turn is the full `State` as JSON (truth) plus each frozen intent with its rolled outcome and band. Output is constrained by `--json-schema` to `{proposals: [{intent_id, deltas: [Delta]}]}` where the Delta schema is the union's own JSON Schema with `delta_id` removed; the engine mints IDs as `derived_id(campaign, tick, intent, n)` so a re-run gives the same IDs. Each proposal is validated against `list[Delta]`; a reply with any invalid proposal is retried once with the errors appended, then the still-invalid proposals are dropped and recorded on `resolver.dropped`. Malformed output never raises (property-tested).
- **Narrator** (`ClaudeNarrator`, Haiku tier): takes `View` and the deltas visible in it; the type is the boundary and a test asserts the signature. Output schema is `{text}`.
- **Recordings** (`RecordedTransport`): request key is blake2b over model, system prompt, user prompt and schema; replies are stored under `tests/recordings/*.json`. A missing key fails the test (`MissingRecordingError`) unless `REVISIONIST_RECORD=1`, which records through the live CLI. CI never calls a model. Recordings are reviewed in the PR like any fixture.
- **Prompt versions**: `prompt_versions()` hashes both prompt files; the caller passes it as `Versions.prompt` to `run_tick` (the CLI does this at Layer 7), so every envelope in a tick names the exact prompt text that produced it.
- **Canary harness** (`core/canary.py`, pure): `canaries_hidden_from(state, seat)` is the set of canary strings on facts whose scope excludes the seat; `scan` runs over every `narration_written` in the log and `scan_deliveries` over what the deliverer actually sent. Tests run it over 50 fixture ticks with an echo narrator (so the projection alone must hold) and over the recorded live tick. One hit fails the suite.

### 9.3 The agent and the deputy (`engine/agent.py`) **[decided]**

- **`Agent` holds a view, never a state.** `Agent.for_seat(state, seat, transport)` calls `project` once and keeps the result. The constructor's type is the boundary: a test asserts it takes `View` and has no `state` parameter. AI factions are the same class in permanent deputy mode (decided pre-Layer-1).
- **`structure(text)`** returns an `Intent` or a `ClarifyingQuestion`, never a delta. The schema constrains the verb to the closed enum and every ID to those in the view; the parser refuses unseen IDs itself, so a recording or a model that ignored the schema cannot smuggle one in. `receive_order` logs `order_received` (private to the seat) before the model call, then files what validates or logs `clarification_asked`.
- **`perceived_odds(intent)`** is `rate(view, intent)`. It differs from truth when the view lacks an asset the rating weighs: South sabotaging the covert army reads impossible, and in truth it is assured.
- **`deputy_turn()`** is pure over the view plus the seat's standing orders. It returns nothing when there are no orders. An irreversible verb (declare, transfer, dismiss) is dropped unless an order names it. Because it is pure, the counterfactual leak test can remove a fact the seat cannot see and assert the intents are identical.
- **`brief()`** returns prose, one dilemma on an open clock, and exactly three moves.
- **Operation re-rolls.** A running operation is re-rated and re-rolled each tick from its own seed. A failure abandons the clock and frees the slot; a success that fills it logs `operation_resolved` with the outcome rolled. The fold keeps an intent pending while its operation clock is open, and the slot check counts that intent through the clock only, so a seat is never charged twice. An intent opens at most one operation, so a crash and resume cannot open a second.
- **Outlet narration.** `_narration_seats` adds outlet seats to the human ones. An outlet's narration is public and built from its own view, so the press learns a secret exactly when someone leaked it. The canary scan covers it.

---

## 10. Settings the engine reads **[decided]**

`core/settings.py`, from README §5 and the H19 decisions. Defaults are the README defaults: plausible, tendencies, **hard** lethality, **cutthroat** betrayal, elimination on, agent harm on, Goals mode, 24-hour ticks, 7 world-days per tick, 3 starting slots, cap 5, max 10 human seats. `lines` and `veils` are sorted unique tuples so the exported schema is stable; `content_rating` is `None` until Session Zero sets one (README #30).

---

## 11. Safety-floor hooks **[decided where they plug in; classifiers open]**

Two call sites, both in the referee, both pure protocols with a fixture implementation until Layer 10:

- `check_intent(view, intent, settings) -> ok | Refusal` at Validate. Lines and veils from `Settings`, the real-person rules from README §19, and the capability-clock abstraction. A refusal logs `intent_rejected(reason=safety_line)` and returns the reason to the agent so the player gets an in-world answer.
- `check_text(seat, text) -> ok | Refusal` after Narrate. Outbound prose scanned before delivery: canaries in test, classifiers in production. A hit logs `safety_refused` (Layer 10 kind) and the narration is regenerated once, then dropped.

---

## 12. Tests shipped with this layer

`tests/test_schema.py` and `tests/strategies.py`:

- Every core model has a Hypothesis strategy (reused by Layers 2 and 3).
- Every `Event` and every `Delta` round-trips through JSON (200 examples each).
- Models are frozen and closed (`extra=forbid`).
- Non-public scopes need a holder.
- No delta has a free-form field.
- `MUTATING_KINDS` is a subset of the real kinds; `delta_applied` mutates, `narration_written` does not.
- The committed `schema/revisionist.schema.json` matches a fresh export (drift fails CI).

`tests/test_purity.py` keeps `revisionist.core` free of I/O, clock, network, model and unseeded-random imports.

---

## 13. Open after this layer

- **[proposed]** Exposure mapping 0 / 5 / 10 / 20 / 35 / 50 percent for `Clock.exposure` 0-5 (`EXPOSURE_CHANCE`). In code as a starting number; retune after the solo slice.
- **[decided]** `Meter.max_delta_per_tick` is summed per meter per intent in `clamp`, so two small deltas cannot beat the cap. Cross-intent summing within one tick is the orchestrator's job (Layer 4) via the same `meter_total` rule.
- **[proposed]** The `rate` scoring weights (means−opposition ±2, disposition ±1, plausibility ±1) are first numbers. The shape (one function over `State` or `View`) is decided; the weights are not.
- **[open]** `Label` cap (60 chars) and `reason` cap (200) are guesses; retune when the first recorded fixtures show what the resolver actually writes. Layer 5.
- **[open]** Co-GM event lead time and season length default remain from README §21.

---

## 14. Postgres and the scheduler (Layer 8) **[decided]**

Build plan Layer 8. Vera's design, decided 2026-10-20 (Josh deferred the three open calls to Vera: TEXT bodies, hand-rolled SQL migrations, and the 10-minute poll with its failure policy); the code and tests follow it. No new event kinds, no schema bump: the log, the fold, the referee and the tick state machine are unchanged.

### 14.1 Storage

- **`PostgresEventStore`** (`engine/pg_store.py`) implements `EventStore` with the same rules as `SqliteStore`: `seq` must be exactly the next integer, `append_all` is one transaction, and a gap, a repeat, a mixed-campaign batch or a unique violation raises `StoreError("append-only: ...")` and leaves nothing written. Two writers racing on one campaign both compute the same next `seq`; the second blocks on the first's row and then fails on the primary key, so one wins and the other gets `StoreError` (tested with two connections). Snapshots are insert-if-absent; the latest by `seq` wins.
- **Tables** (migration `0001_init`): `events(campaign_id, seq, event_id UNIQUE, tick, kind, body)` with `PRIMARY KEY (campaign_id, seq)`, and `snapshots(campaign_id, seq, body)` keyed on both.
- **`body` is `TEXT`, not `JSONB`.** JSONB refuses the escape `\u0000`, and a player can type a NUL into an order, so a JSONB log could refuse a legal event. TEXT stores the exact bytes SQLite stores; the contract suite checks a NUL, an emoji and escapes round-trip to an equal `Event`. Nothing queries inside bodies today; `kind` and `tick` are columns for the queries that matter.
- **`campaign_ids()`** is now on the protocol (all three stores), oldest first by first event ID.
- **Backend selection.** `$REVISIONIST_DB` is a `postgresql://` (or `postgres://`) URL for Postgres, anything else a SQLite path as before. `open_store()` returns an `EventStore`; the CLI and campaign helpers are typed to the protocol. `psycopg` is the optional `postgres` extra and is imported only when the URL says Postgres, so SQLite play needs nothing new (tested by importing the CLI with `psycopg` blocked). Briefings go to `$REVISIONIST_BRIEFINGS_DIR` when set, else beside a SQLite file as before, else `./revisionist-briefings`.

### 14.2 Roles, grants and migrations

- **Two roles.** An admin role owns the database and tables and runs migrations (`$REVISIONIST_ADMIN_DSN`). The runtime connects as an app role holding `SELECT, INSERT` on `events` and `snapshots`, and `SELECT, INSERT, UPDATE` on `schedule`. It cannot `UPDATE`, `DELETE` or `TRUNCATE` the log and cannot read `schema_migrations` (tested as the app role). The app role does not own the tables, because an owner bypasses grants.
- **Triggers as a second layer**, mirroring SQLite: `BEFORE UPDATE OR DELETE` and `BEFORE TRUNCATE` on `events` and `snapshots` raise `append-only` for every role, the owner included (tested as admin).
- **Migrations** are hand-rolled numbered SQL files in `engine/migrations/` (shipped in the wheel), applied by `migrate(admin_dsn, app_role)` / `revisionist db migrate`. A `schema_migrations(version, applied_at)` table records what ran; each file runs in its own transaction with its version row, under an advisory lock, so a rerun is a no-op and a failure stops at the last good version. The app role's name is passed in and quoted as an identifier; creating the roles is a documented manual step (`docs/deploy.md`), never hidden in a migration. Alembic was not used: it brings SQLAlchemy for two tables. This resolves the build plan's migration-tool item **[decided]**.
- **Applied migrations are never edited.** `0001_init` still creates the trigger function under the project's old name; `0004_rename` renames it to `revisionist_append_only`, so fresh and upgraded databases converge (tested).

### 14.3 Copy and verify

- **`revisionist db copy --from <sqlite> --to <postgres url> [--campaign ID]`** appends the events the target lacks, in order, then the latest snapshot, then checks the target event for event and by `state_hash`. Rerunnable: a target holding a prefix gets the rest; a target that diverges is refused.
- **`revisionist db verify [--campaign ID]`** replays each campaign from event 0 through the fold, which already checks `state_hash` at every `tick_closed` and raises `FoldError` on a mismatch, then checks the latest snapshot equals the replay at its `seq`. Non-zero exit on any failure. The restore drill runs it against a restored dump.

### 14.4 The scheduler

- **Trigger.** A systemd user timer starts `revisionist scheduler run` every 10 minutes. It is a poll, not a daemon. The tick cadence is each campaign's `Settings.tick_hours`; the timer only sets how promptly a due tick is noticed. This refines build plan decision 6 (the timer calls `revisionist scheduler run`, which runs the same tick path as `revisionist tick`). User units because the Claude CLI login lives in the user's home.
- **Shared tick path.** `engine/runner.py` `play_tick` is the deputy turns, `run_tick` and the leak scan that `revisionist tick` always did. Both the CLI and the scheduler call it.
- **Operational state is not game state.** The `schedule` table (migration `0002_schedule`) holds `campaign_id, next_due_at, advance_requested, paused, paused_reason, consecutive_failures, last_started_at, last_finished_at, last_status`. It is never in the event log. A campaign gets a row the first time the scheduler sees it, first due one tick later.
- **When a campaign is due** (`due(row, now, in_flight=, submitted=)`, pure): never while paused; always when a tick is open (resume it); else when the host asked to advance; else when `now >= next_due_at`; else when every human seat has filed an intent since the last `tick_closed` that is still pending (a Wait counts; intents carried by a quiet tick or held by a running operation do not, so they cannot make a campaign tick every poll). At most one tick per campaign per run.
- **No drift, no backfill.** After a tick closes, `next_due_at` becomes the previous `next_due_at` plus `tick_hours`, repeated until it is in the future (`next_due_after`, pure). A late tick does not shift the schedule; missed ticks are skipped. An early tick (advance or all-submitted) spends the current slot.
- **Overlap.** Each campaign is ticked under a session advisory lock taken with `pg_try_advisory_lock`; a run that cannot take it skips the campaign silently. The store's primary key is the backstop.
- **Failures.** A `TransportError` or any crash inside the tick is recorded (`last_status`, `consecutive_failures + 1`) and the tick stays resumable for the next poll. At 3 consecutive failures the campaign is paused with a reason and the run exits 1, so systemd shows the unit failed. A canary leak (the `revisionist tick` exit-3 path) pauses the campaign at once and the run exits 3. A process killed mid-tick leaves `last_status` at `running (...)`; the next run resumes the tick.
- **Host controls.** `revisionist scheduler advance | pause [--reason] | resume | status`, each with `--campaign ID` (default all). The host's writes touch only their own columns and the scheduler only ever sets `paused`, never clears it, so a pause issued mid-tick survives the tick (tested).
- **Clock.** Only `run` reads the clock, through an injected callable (`revisionist.cli.clock`). Tests step a fake one; no test sleeps.
- **Postgres only.** The scheduler needs the advisory lock and the `schedule` table. SQLite campaigns tick by hand with `revisionist tick`.

### 14.5 Deployment and backups

`deploy/systemd/` holds the scheduler and backup user units and timers; `deploy/backup/` holds `revisionist-backup.sh` (nightly `pg_dump -Fc` to a local directory with N-day retention, then `rsync` or `rclone` to `$REVISIONIST_BACKUP_DEST`, failing loudly when it is unset) and `revisionist-restore-drill.sh` (newest dump into a scratch database, `revisionist db verify`, drop, non-zero on any failure). The runbook is `docs/deploy.md`.

### 14.6 What the tests cover, and what they do not

Covered, against a real Postgres when `$REVISIONIST_TEST_PG_ADMIN_DSN` is set (CI sets it and `REVISIONIST_REQUIRE_PG=1`, so a missing database fails instead of skipping):

- The store contract suite over memory, SQLite and Postgres; app-role grants and owner triggers; concurrent writers; idempotent migrations.
- The migration test: an offline SQLite campaign with three closed ticks and a fourth left open after APPLY is copied to Postgres, matches event for event and by `state_hash`, then resumes that tick and plays another on Postgres.
- The accelerated stand-in for the three-day run: 72 simulated hours of 12-hour ticks, the scheduler invoked every 10 simulated minutes, one tick crashed after NARRATE and resumed by the next poll, one poll contended by a held lock. Exactly 6 ticks open and close, the late tick does not drift the schedule, and `revisionist db verify` passes.
- Advance, all-submitted, pause and resume, three-failure pause, leak pause, a pause landing mid-tick.
- The unit files parse and their `ExecStart` is a real `revisionist scheduler run`; the backup script and restore drill run end to end against a scratch database with a local directory as the off-box target, and the drill fails on a corrupt dump.

Not covered: a real systemd run of the units, real linger and reboot behaviour, an actual off-box host or rclone remote, the real three-day soak on the home server, and GitHub Actions itself. Those are Josh's steps (`docs/deploy.md` §8).

---

## 15. The Discord transport (Layer 9a) **[proposed]**

Build plan Layer 9a: the solo slice played from Discord. Written 2026-10-20 with the code; not yet run against the real Discord API. No new event kinds, no schema bump, `revisionist.core` unchanged.

### 15.1 The bot is a transport, not a GM

The bot turns a slash command or a DM into the call the CLI makes and sends back what the engine returns. It has no tools, no shell, no memory and no model call of its own. `engine/play.py` holds `give_order`, `get_briefing` (with the cost log and the `scan_deliveries` withhold) and `status_lines`; `revisionist order | briefing | status` and the bot both call them, so the two paths cannot drift. A test sends the same order text through `/order` and through `revisionist order` on two copies of one campaign and asserts the same pending intent and the same `state_hash`.

### 15.2 Identity

- **Seat from the link, never from text.** `discord_seats(campaign_id, seat_id, discord_user_id, guild_id, linked_at)` binds a human seat to a Discord user. The primary key on `(campaign_id, seat_id)` stops two users sharing a seat; `UNIQUE (campaign_id, discord_user_id)` stops one user holding two. The host binds with `revisionist discord link --user <id>`, which refuses a non-human seat. The bot looks the acting seat up from the interaction's (or DM author's) user ID, newest binding in its one guild. No command takes a seat argument; an unlinked user gets a refusal and nothing is written.
- **Host commands** (`/advance`, `/pause`, `/resume`) check the user ID against `$REVISIONIST_DISCORD_HOST_ID` and act on the host's own bound campaign, else the newest.
- **Default intents only.** No Message Content, Server Members or Presence. Slash commands and DMs need none of them (Discord exempts DMs from Message Content). Guild messages are ignored, as are bots, the bot itself included.

### 15.3 Commands

`/order text`, `/briefing`, `/status`, `/advance`, `/pause`, `/resume`, `/bug notes`, and `/report`, `/key` (stubs). Synced to the one guild on startup, so they exist only in that server; a plain DM to the bot is an order. Every interaction is deferred at once (ephemeral) and answered by ephemeral followups once the handler returns. Handlers run in `asyncio.to_thread`, each with its own store connection, never on the event loop. Replies are out of character and one line on failure: never a stack trace, a model error's text or the token. Errors are logged in full for the host.

`/bug` writes a `discord_bugs` row with the campaign's current `seq` and `state_hash`, so the report pins the exact state. `/advance` sets `advance_requested` and then runs `scheduler.run(..., campaign=<this one>)` in the thread, so the per-campaign advisory lock still keeps it from ticking alongside the timer (tested: an `/advance` landing mid-tick gets "a tick is already running" and the campaign ticks once). The briefing itself arrives by DM through the deliverer.

### 15.4 Delivery

- **Routing.** When `$REVISIONIST_DISCORD_TOKEN` is set and the store is Postgres, `revisionist tick`, `revisionist scheduler run` and `/advance` deliver through `RoutingDeliverer`: seats bound in `discord_seats` go to `DiscordDeliverer`, every other seat (AI factions, the outlet) to the Layer 7 `FileDeliverer`. Without the token, or on SQLite, nothing changes. `play_tick` and `scheduler.run` take an optional deliverer factory for this; the default is the file deliverer.
- **REST, stdlib only.** The scheduler is a one-shot process with no gateway, so `DiscordDeliverer` uses `urllib` against `https://discord.com/api/v10`: open the DM with `POST /users/@me/channels`, send with `POST /channels/{id}/messages` (`allowed_mentions` empty, so a narration can never ping anyone). A 429 is waited out for its `retry_after`, at most 3 attempts, then the call raises. The token comes from the environment and never appears in logs, exception text or `repr` (tested, including a network error whose own text carries the token).
- **Parts.** Discord caps `content` at 2000 characters. A briefing is cut on paragraph boundaries, then lines, then anywhere, each part at most 1900, and the parts concatenate back to the exact text (property-tested).
- **Idempotency, two layers.** The real guarantee is `discord_deliveries(idempotency_key, part, channel_id, message_id, sent_at)`: a part recorded there is never resent, so a second DELIVER with the same key sends nothing. For a crash after Discord accepts a part but before the row lands, every send carries `nonce` = the first 25 characters of base32 blake2b of `key:part`, with `enforce_nonce: true`. Discord checks that uniqueness only for a few minutes, so it covers a quick retry, not a resend hours later.
- **The log stays free of Discord IDs.** `BriefingDelivered.channel` is the constant `discord`; message IDs live only in `discord_deliveries`.
- **Closed DMs.** Superseded in 9b (§16.1): if Discord refuses the DM (code 50007) the briefing is `held`, sent nowhere, and the player reads it with `/dispatch`. The 9a fallback channel is gone; with two humans it would have shown one player's briefing to the other. Any other error propagates: `BriefingDelivered` is not written, the tick stays resumable and the scheduler counts a failure.
- **Last canary check before text leaves the host.** The routing deliverer scans a bound seat's narration against the canaries hidden from that seat before sending. A hit is written to the host's briefings file instead, `BriefingDelivered.channel` is `withheld`, and the tick's leak scan then fails the tick (exit 3; the scheduler pauses the campaign). The bot's handlers run the same scan over every reply they send. The projection should make both checks dead code; they exist so a narrator or agent bug cannot reach a player.

### 15.5 Operational tables

Migration `0003_discord`: `discord_seats`, `discord_deliveries`, `discord_bugs`. Operational state, not game state, so none enters the event log (same reasoning as the `schedule` table, §14.4). The app role holds `SELECT, INSERT` on `discord_deliveries` and `discord_bugs` (plus `USAGE` on the bug ID sequence), and `SELECT, INSERT, UPDATE, DELETE` on `discord_seats` so the host can unlink. Nothing new on `events` or `snapshots`.

### 15.6 Packaging and deployment

`discord.py>=2.7` is the optional `discord` extra and is imported only by `engine/discord/bot.py`, which only `revisionist discord run` loads; a test runs the CLI and an offline tick with `discord` blocked from import. The REST deliverer needs nothing beyond the stdlib. The bot runs as `deploy/systemd/revisionist-discord.service`, a long-running user unit with `Restart=on-failure` and the scheduler unit's hardening and environment file. Runbook: `docs/deploy.md` §9.

### 15.7 Not covered by the tests

Everything against the real Discord API: the gateway connection, command sync, real DM delivery, closed-DM behaviour, rate limits and nonce deduplication as Discord actually applies them. The tests use fake gateway objects (interactions, messages) and a fake REST API that mimics the documented behaviour; the real HTTP client is blocked for every test.

---

## 16. Two humans (Layer 9b) **[decided 2026-10-20]**

Josh chose the dispatch model, the encryption design, the recordings handling and the spin event on 2026-10-20; the rest follows from them. Nothing here changes the fold of an existing kind, the referee or the tick state machine.

### 16.1 Seats, invites and dispatch

- `worlds/salt-strait-duo.yaml` is salt-strait with Ostrel as a second human seat with its own agent; a test keeps the two files in step. The CLI's `order`, `briefing` and `status` take `--seat` when a campaign has several humans.
- **Invites.** The host issues a single-use code bound to a seat (`revisionist discord invite`), valid 72 hours. Only its blake2b hash is stored (`discord_invites`, migration 0005). A player redeems it with `/join`: one transaction binds them and spends the code, and a refused link leaves it unspent. Unknown, spent and expired codes get the same answer. On success the bot sends a test DM, the README §17 onboarding check.
- **Dispatch is the DM.** The server owner can read every channel and private thread, and the owner also plays, so per-player channels or threads would protect nothing. Briefings go by DM only. A closed DM holds the briefing (`BriefingDelivered(channel="held")`, nothing sent) and `/dispatch` reads the latest one back from the log. A human seat nobody holds yet is also held, never written to the host's briefings directory.

### 16.2 What a view shows about other seats

README §17: how a seat is run is never shown. `View.seats` holds a `SeatFace` (ID, kind, name, embodied entity) for every seat and `View.me` the viewer's own seat in full; `referee.own_seat` reads a seat in full only from `State` or as `View.me`. A property test changes another seat's controller, status, agent, persona, slots and orders and checks the view is byte-identical. `SCHEMA_VERSION` 3. The structure, brief and narrator prompts embed the View, so 4 recorded entries were re-keyed offline, each marked `rekeyed_from`; they are to be re-recorded live.

### 16.3 Visibility and spin

The player chooses an order's visibility (private by default, public or covert; restricted waits for alliances) and its spin, on the CLI (`--visibility`, `--spin`) and on `/order` alike; a DM order is private with no spin. Spin is the player's alone: whatever the model or the deputy writes there is dropped. When an order first freezes, its spin becomes `statement_issued` (a record kind, logged Public whatever the order's scope, once per intent even when an operation is frozen again; `SCHEMA_VERSION` 4).

### 16.4 The public channel

One channel, `#chronicle` (`engine/discord/template.py`), set with `revisionist discord channel public <id>` (`discord_channels`, migration 0006). The press outlet's narration is posted there instead of filed, and after each leak-free tick every statement and every admin read not yet posted goes up once, idempotent through the outbox. A statement is checked against canaries its author cannot know, not against everyone: a player may disclose their own secrets. No per-player channels, roles or threads, and the bot never gets Manage Channels, Roles or Threads.

### 16.5 The human boundary

README §7: the opacity boundary is the person. `core/canary.py` scans everything a person can read (their seats' narrations and questions, the press, every statement; in the test, every DM, reply and channel post they received) against the union of what all their seats may know. `revisionist leaks --humans` runs it from the Discord seat links, counting the host as holder of every unlinked human seat. `tests/test_human_boundary.py` plays 12 ticks of the duo world with a transport that echoes each prompt, View JSON and all, and also scans the host's briefings directory and the bot's log; a negative control shows it fails on a planted leak.

The host's own surfaces were closed to match: a withheld narration is no longer written to a file; the CLI refuses `order`, `briefing` and `status` for a seat another player holds; the `claude` child gets no `REVISIONIST_*` variable; and transport errors quote neither the prompt nor the model's output, since they reach logs and `schedule.last_status`.

### 16.6 Encryption at rest

Two tiers of key per sealed campaign:

- the **campaign key** seals every event and snapshot, so the fold can always read the game;
- a **seat key** seals that seat's own words: what its player typed (`order_received`), its agent's questions, its private briefings, and its bug notes. None of these is folded.

Each event is sealed whole with AES-256-GCM, bound to its campaign, seq, ID and key ID. At rest it is a stub (`Sealed` payload, no actor, Public scope; `SCHEMA_VERSION` 5) whose `payload_enc` is the nonce and ciphertext, so even its kind is hidden. The stores take a sealer and unseal on read, so the tick, the fold and every reader see plaintext; without the key a sealed record is refused with an error naming the setting. Data keys live in `seal_keys` (migration 0007), wrapped by a host master key that never enters the database or the environment: `REVISIONIST_MASTER_KEY_FILE` names a 0600 file. The app role can read and add keys and never destroy one. Sealing is per campaign and forward only, because plaintext snapshots already written can never be deleted.

**Erasure, redefined.** "Erasure = key deletion" cannot hold for game data: the fold and every `state_hash` depend on covert facts and intents. So:

- **Player erasure** (`revisionist admin erase`, destroys a seat key): the player's own words become unreadable at once in the live database, and in backups once the backup retention window has passed. What their play did to the world stays, and replay and `db verify` still work.
- **Campaign erasure** (destroy the campaign key, at retirement; Layer 10 item 15): everything is gone and the campaign can no longer be replayed.

**Admin reads.** Showing a person a record that is not public (`core/sealing.needs_admin_read`, which looks inside payloads because `delta_applied` is logged Public even for a covert fact) first appends a Public `admin_read` with a reason; if that fails nothing is shown. `revisionist why` hides such records unless `--break-glass --reason`; `revisionist admin show` reads one. Each admin read is announced in `#chronicle` without content.

None of this protects players from the host, who holds the master key and the bot token. It protects data at rest and in backups, and makes the host's reads visible.

### 16.7 Perceived odds

README §7: shown only once an order is filed and locked. `/status` and `revisionist status` show the perceived band of each locked pending order, from `feasibility_rated` at the cutoff, and a briefing ends with how the agent read last tick's orders. Never the true band, never before the cutoff (so odds cannot be shopped for by re-filing), nothing for a blinded seat.

### 16.8 Not covered by the tests

Everything against the real Discord API: invites redeemed by a real second account, a real closed DM, the `#chronicle` posts, permissions as Discord applies them. The re-keyed recordings have not been re-recorded live. Master-key custody, a restore onto a new host with the off-box key, and the 10-tick two-person play.


---

## 17. Secret goals and winning (Layer 10 item 1) [decided 2026-10-20]

README §15 and decisions 23 and 36-38. Built in PRs 1a-1e; this section grows with them.

### 17.1 Win conditions (1a)

A goal is a `Goal` in `State.goals`: the player's words (`text`, at most 280 characters), a `WinCondition`, a kind, and its objective clock. It is Private to its seat, so `project()` gives a seat its own goal and nobody else's, and `SeatFace` says nothing about goals.

`WinCondition` is a closed union over State, checked by the pure `core/goals.goal_met`:

| Check | Met when |
|---|---|
| `holds_office(holder, office)` | the asset is an Office, controlled by `holder`, not lost |
| `controls_asset(holder, asset)` | the asset is controlled by `holder`, not lost |
| `entity_is(entity, status)` | the entity has that status |
| `meter_at(meter, ge\|le, value)` | the meter compares true |
| `commitment_in_force(commitment)` | the commitment is accepted |
| `disposition_at(relationship, ge\|le, value)` | the disposition compares true |
| `all_of(...)`, `any_of(...)` | and, or; at most 8 checks, 3 deep |

An ID the state does not hold is unmet, so nothing is met in an empty world. Progress is the number of top-level parts of an `all_of` that hold (one part otherwise), shown on the goal's objective clock as "2 of 3".

**Kinds, from a fixed table:** `entity_is` dead or dissolved is **Lasting**; every other check is **Undoable**; an all-of or any-of is Lasting only if every part is. A Lasting goal, once met, stays met and is no longer checked. An Undoable goal that stops holding logs `goal_undone` and counts as unmet until it holds again.

**When.** At the end of APPLY each tick, `goals.check` compares every goal with the state and logs only the changes, each Private to the owner: `goal_progressed` (the clock), `goal_met` (first time or again; `met_tick` keeps the first) and `goal_undone`. `season_winners` is every seat whose goal holds: Lasting goals ever met plus Undoable goals holding now.

**World files** declare goals with keys (`goals:` in the YAML). The compiler gives each goal its own private objective clock, adds it to the seat's objectives, and computes the kind. It refuses a goal on a non-player seat, a second goal for one seat, a key of the wrong type, an office that is not an Office, a duplicate canary, and a goal already met at tick 0.

**Compatibility.** `State.goals` is a late field: it is left out of serialisation while empty (`Model.late_fields`), so every older state hashes, and every view serialises, byte for byte as before. Old logs replay and the recordings keep their keys. Goal records are sealed with the campaign key, never a seat key, because the fold needs them; erasing a player keeps their goal. The resolver's truth leaves goals out: no model weighs whether, or how, a goal is met. The player's own agent does see its seat's goal, through the view.

**Deferred.** README §15 lists "exiled" as an entity status. Adding it changes the resolver's output schema and so every resolver recording key; it waits for the next live re-record.

### 17.2 Goal intelligence (1b)

README §7 and decision 37: a rival's goal is learned by working for it, and the engine, not a model, decides what is learned.

- **Pieces.** An `investigate` intent whose target is another player seat's entity, and whose roll is a partial or better, earns that seat's next goal piece. A piece is an ordinary Fact about the target entity, Private to the seeker, applied in APPLY as `delta_applied` against the intent. The pieces come in a fixed order: `goal_exists` ("has a secret aim"), `goal_domain` (office, an asset kind, a person, standing, a treaty, a relationship), `goal_object` (the name of the thing), `goal_condition` (the whole condition in a few words, from `intel.render`). The next piece is the first the seeker does not already hold; once all four are held, nothing more is learned. One piece per intent per tick, so a multi-tick investigation earns one a tick.
- **Misleading partials.** On a partial success the object and the condition may be false (`truth=False` in canon): the target's planted decoy if there is one, otherwise, on a seeded roll, another object of the same kind. The domain is never the lie. A full success always tells the truth.
- **Forgery.** A `forge` whose targets name the actor's own entity and one other asset, entity or meter plants two decoy Facts (`goal_domain`, `goal_object`) about that bait, Covert and known only to the actor. They are what a rival's partial success finds.
- **Pieces travel like facts.** A seeker can share, sell or lie about a piece with any knowledge grant; `intel.held` counts what a seat knows, however it came to know it. Only the pieces travel: the goal's own words never leave the owner's view.
- **The narrator** of the seeker is given the piece among the tick's applied deltas, so the briefing can report what the spies found.
- **Tests.** The counterfactual test removes a piece: deputies without it file the same intents, and the holder's changes, so the test can fail. The duo test runs four ticks of Varda investigating Ostrel with every narration echoing its whole view: Varda learns all four pieces, and Ostrel's goal text and canary never reach Varda's person.
- **Not yet.** `conceal` (raising the bar on your own goal), interception and capture of agents carrying pieces, and other seats' AI investigations aimed at goals on purpose, come with later items.

### 17.3 `/establish` and the world compiler (1c)

README §5 step 2. `revisionist establish "<place, date>"` (with `--plausibility`, `--inertia`, `--lethality`, `--betrayal`, `--tone`, `--tick-hours`) and the host's `/establish` lock a point in history:

1. `engine/session_zero.draft_world` asks the frontier model (`SESSION_ZERO_MODEL`, call class `session_zero`, `prompts/establish.md`) for a whole world file in the fixed schema, given the place, the date and the table's settings for tone.
2. The draft is checked: it must parse as a `WorldFile`, pass the Layer 7 compiler, and pass `world_compile.check_history`: a start, 8-12 roster characters, no human seats, no goals. The roster is checked like everything else: each character is a person, listed once, and controls the means it is given; `composite` has no default, so every character is labelled real or composite.
3. On any problem the model gets one retry with the problems listed; a second failure is refused and **nothing is written**.
4. The table's settings replace the draft's, the slug is derived from the place, and the compiled events are written, with `character_listed` for each roster entry and a Public `session_zero_opened(start, notes)` last. `notes` is the GM's log of readings it chose where the record is disputed or silent. The cost lands as `cost_logged(purpose=session_zero)`.

**Phase.** `State.phase` is `play` for every campaign from a hand-written world (a late field, so older states are unchanged); `session_zero_opened` sets `session_zero`. `run_tick` and `play_tick` raise `NotInPlayError` outside play, before any faction spends a model call; `revisionist tick` exits 2, and the scheduler and `/advance` report `waiting: session zero`.

**Who may establish.** The host only, for now. README §5 has `/establish` pass by the co-GM vote at three or more humans; that comes with votes (Layer 10 item 7). The new campaign becomes the host's current one; Discord's `/establish` uses the default settings.

**Tests** use two hand-written drafts (Florence, April 1478; Washington, October 1962) under `tests/drafts/`, labelled as stand-ins to be re-recorded live; no recording exists for this prompt yet.
