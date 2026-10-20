# Anansi design: log, state, boundary, pipeline

Build plan Layer 1. Written 2026-10-19 against [README.md](../README.md) v2. The models in `src/anansi/core/` are the schema; this document explains them and names the rules the code must keep. Where the two disagree, the code is wrong and this doc wins until the code is fixed.

Status legend: **[decided]**, **[proposed]**, **[open]**.

Module map:

| Module | Holds |
|---|---|
| `core/ids.py` | typed IDs |
| `core/visibility.py` | `Visibility`, `Scope` |
| `core/settings.py` | Session Zero settings the engine reads |
| `core/ontology.py` | Entity, Asset, Relationship, Meter, Clock, Fact, ScheduledEvent, Commitment, Seat |
| `core/intent.py` | `Verb`, `Intent` |
| `core/delta.py` | the `Delta` union |
| `core/resolution.py` | bands, tiers, stages, rejection reasons, starting odds |
| `core/events.py` | every event kind, the `Event` envelope |
| `core/state.py` | `State` (truth) and `View` (one seat's knowledge) |
| `schema/anansi.schema.json` | exported JSON Schema; CI fails on drift |

---

## 1. The log is canon **[decided]**

README #19. There is no state table. There is an append-only sequence of `Event` records per campaign, and `State` is what you get by folding them. Anything the game knows, it knows because an event said so.

Consequences the code keeps:

- `Event.seq` is dense per campaign. The store (Layer 2) refuses gaps, repeats, updates and deletes.
- Nothing is ever deleted from the ontology. Entities, assets, clocks and commitments change `status`; they do not vanish. A dead minister is `Entity(status=dead)`, still in the log, still in the state, still citable by a journalist.
- Every event carries `versions` (engine, schema, prompt, model) so a replay can tell which rules produced it.
- Two classes of event: **mutating** kinds that the fold reads, and **record** kinds (narration, rolls, proposals, rejections, admin reads) that exist for `/why`, replay and audit but change no state. `events.MUTATING_KINDS` is the list; a test keeps it a subset of the real kinds.
- `payload_enc` is reserved on every envelope. Empty until Layer 9, when raw orders and Covert payloads are encrypted per player.

### Event kinds in v1

| Group | Kinds |
|---|---|
| Creation | `campaign_created`, `settings_set`, `entity_created`, `asset_created`, `relationship_set`, `meter_created`, `clock_created`, `fact_asserted`, `commitment_created`, `seat_created`, `agent_assigned`, `seat_status_set` |
| Orders | `order_received`, `intent_filed`, `intent_rejected`, `clarification_asked` |
| Resolution | `feasibility_rated`, `rolled`, `delta_proposed`, `delta_rejected`, `delta_applied`, `operation_opened`, `operation_resolved`, `scheduled_event_fired` |
| Output | `narration_written`, `briefing_delivered` |
| Tick | `tick_opened`, `tick_stage_completed`, `tick_closed` |
| Admin | `admin_read` |

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

`project` keeps an item when `item.scope.visible_to(seat)`. For intents, a view holds only the viewer's own pending intents (`View.own_pending`); other seats' intents are never in any view. Settings and the tick counter are always visible.

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

`core/delta.py`. A discriminated union of 17 typed changes, each against an existing ID (or creating a new object with an ID the engine minted). Every delta carries a `reason` capped at 200 characters for `/why`.

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

Starting odds (`core/resolution.py`): Impossible 0%, Long shot 20%, Contested 50%, Likely 75%, Assured 95% chance of a success tier; within success, Partial 40 / Success 45 / Opportunity 15. Exposure rolls independently against the clock's `exposure`; a hit grants knowledge to one investigator seat, not to the public. Escalation to public only through further investigate or leak intents.

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

`state_hash` is blake2b over the canonical JSON of `State`. The Layer 2 determinism test and the Layer 8 SQLite-to-Postgres migration test both compare it.

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
- The committed `schema/anansi.schema.json` matches a fresh export (drift fails CI).

`tests/test_purity.py` keeps `anansi.core` free of I/O, clock, network, model and unseeded-random imports.

---

## 13. Open after this layer

- **[open]** Exact exposure-roll mapping from `Clock.exposure` 0-5 to a percentage. Proposed 0 / 5 / 10 / 20 / 35 / 50. Layer 3.
- **[open]** Whether `Meter.max_delta_per_tick` applies per delta or summed across a tick. Proposed: summed, so two small deltas cannot beat the cap. Layer 3.
- **[open]** `Label` cap (60 chars) and `reason` cap (200) are guesses; retune when the first recorded fixtures show what the resolver actually writes. Layer 5.
- **[open]** Co-GM event lead time and season length default remain from README §21.
