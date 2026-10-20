# Anansi parameters: pressure test and resolution plan

Date: 2026-10-19. Subject: README v1 (game parameters).

## Method

The README was reviewed by five independent, read-only reviewers. Each one took a different angle:

1. Player experience and social dynamics
2. Adversarial player (exploits and abuse)
3. Systems coherence (contradictions and undefined mechanics)
4. Technical feasibility, LLM reliability and cost
5. Safety, content, legal and distribution

The reviews returned about 70 findings. Many overlapped, so this document merges them into 20 problems, ranks them, and gives a resolution for each one. Each item has a tag:

- **[design]**: can be fixed in design without changing a decided parameter.
- **[decision]**: narrows or reinterprets a decided parameter, so Josh decides.
- **[gate]**: must be in place before a given milestone.

## Headline

The concept survives. The weak point is the referee. Free text, opacity and "the engine holds the numbers" can all hold, but only if the engine has a fixed schema and owns the odds and the dice, and only if code, not the LLM, filters what each seat sees. Most of the critical findings come down to one cause: the README says the rules engine checks and clamps, but it never says what the engine is checking.

The second weak point is social, not technical. With no capacity rule, the keenest player takes over. With no ending, campaigns fade out instead of finishing. And because nothing protects absent players, they come back to a ruined seat.

Cost is not a risk at friends-and-family scale. The estimate is about $7 a week for a 5-player campaign on a hybrid model split (see section E).

---

## A. Critical: engine foundations (gate: before any build beyond the minimum slice)

### A1. Fixed core schema [decision: confirms how decision 4 is implemented]
**Problem.** The world bible is prose, and decision 4 allows any world. That leaves the engine nothing typed to validate or clamp against. If the LLM invents a schema for each campaign, the referee split is empty. (Systems, Feasibility)
**Resolution.** Use one world-agnostic ontology. Campaigns create instances of it and never extend the types:
- Entity (polity, faction, person, org, location)
- Asset (Force / Wealth / Network / Office / Info / Facility, with tier 0-5, controller, location, loyalty)
- Relationship (signed disposition plus price/leverage tags)
- Track / Meter (bounded, with a maximum change per tick)
- Clock / Operation (segments, ETA, exposure, known_by)
- Fact (truth plus a visibility ACL)
- ScheduledEvent
- Commitment (treaties and promises as structured clauses)

Session Zero maps setting nouns onto these types ("oxygen quota" becomes a Meter; "the Moon Senate" becomes an Office). The LLM may only propose typed deltas against existing IDs, using constrained decoding. If a proposal fails validation, it is retried once, then dropped and logged. It is never partially applied.

### A2. Resolution pipeline: the engine owns the odds and the dice [design]
**Problem.** "The rules engine rates feasibility" is not possible on free text. In practice the LLM would set both the odds and the result. (Systems, Feasibility, Exploits)
**Resolution.** A four-stage pipeline:
1. **Structure.** A low-privilege call turns each order into a typed Intent: a verb from a closed list, the actor, target IDs, the committed means (asset IDs), the visibility and the duration. Meta-text such as "referee notes" or rule claims is dropped here.
2. **Rate.** The engine computes a feasibility band from state alone (Impossible / Long shot / Contested / Likely / Assured), using means, reach, counterparty disposition and price, capacity, and the realism setting.
3. **Roll.** A seeded RNG (seed = campaign + tick + action) gives an outcome tier: Fail with complication / Partial / Success / Success with opportunity. Exposure has its own independent roll. "Impossible" always resolves as an interesting failure.
4. **Propose and narrate.** The LLM proposes deltas allowed by that tier. The engine clamps and applies them. Narration is written from the applied result.

Every prompt, roll and response is logged. Replay means re-applying the logged deltas. An admin `/why <action>` command shows the inputs, the odds and the roll.

### A3. Opacity enforced by code, not by prompt [design] [gate: CI before the first playtest]
**Problem.** A model that holds the full truth and is told "write only what seat X knows" will leak through hints. Briefings are not the only leak path:
- a shared chronicle
- AI factions acting on secrets
- press reporting things nobody uncovered
- NPCs role-playing ignorance
- feasibility ratings that work as yes/no probes
- catch-all conditional orders
- co-GM narration
- Discord metadata such as roles, member lists and online status

(Feasibility, Exploits ×2, Systems)

**Resolution.**
- Every event carries `visibility` and `known_by`. Discovery changes `known_by` through an engine `reveal()` call.
- Every narrator, deputy, AI faction, NPC and outlet is built from `project(state, actor_id)`. AI actors get their own knowledge layers, the same as seats.
- The full-truth resolver emits only enums, IDs and numbers. None of its prose ever reaches a player.
- Each event has a separate `public_face`, written from the effect delta only.
- Each seat has its own chronicle. A shared chronicle of the true state is never fed to any narrator.
- Players see **perceived** odds, computed from their own knowledge, and only after the order is filed and locked.
- Conditional orders trigger only on events the seat can see. Hidden actions can only be caught by deployed assets, such as a counter-intelligence operation already running.
- Orders lock at a cutoff, and edits roll into the next tick.
- On Discord: actions go through ephemeral modals or DMs; alliance membership and role names reveal nothing; deputy status is never shown; a double agent gets filtered mole reports, not raw channel access.
- Tests:
  - **Canary:** plant unique strings in covert orders, run 50+ ticks, and search all outbound text for them.
  - **Counterfactual:** remove one covert Fact and confirm that AI moves stay the same when the actor has no knowledge of it.
  - **Injection:** run the attack suite.
  - **Metadata:** check Discord for metadata leaks.
- The opacity boundary is the **human**, not the seat. Tests check the union of everything each human can see.

### A4. Means are an inventory, and history is a log [design]
**Problem.** "Any means a player can describe" lets players invent armies, assets and past treaties ("I have always had a mole in their cabinet"). The rolling chronicle drifts over 100+ re-summaries, and players can talk the GM into precedents that never happened. (Exploits, Systems, Feasibility)
**Resolution.**
- Means are the seat's typed Assets and Relationships, seeded at Session Zero and changed only by logged events. Seat backstory locks when Session Zero ends.
- A means that doesn't match the inventory becomes an **acquisition** action (recruit, buy, build, turn an agent). That takes time and slots and carries exposure. This is not a currency, so decision 9 holds.
- An engine Transfer/Contract primitive moves assets between seats atomically (offer, then accept). Promises stay as non-binding talk.
- The structured state plus an append-only log are the canon. The chronicle is hierarchical and never rewritten. Retrieval is by entity ID. A claim about the past that can't be linked to the log is treated as the character's assertion, not as fact.

### A5. Safety floor [decision] [gate: before the friends-and-family playtest]
**Problem.** The GM voices every scientist, chemist, smuggler and banker, so "my bio chief briefs me step by step" is the standard fiction-cover jailbreak. Lines and veils have no fixed floor, no rule for disagreements, and can be bypassed by co-GM votes, covert actions and deputies. A stream of war and terror content could also get the provider account banned. (Safety ×3)
**Resolution.**
- **Abstraction is an engine rule.** Weapons of mass destruction, explosives, weapon conversion, intrusion and laundering are abstract capability Clocks (for example, "bio programme stage 2 of 4"). Narration gives results, never procedures, quantities, precursors or code. Input and output classifiers back this up, with cumulative detection per player.
- **Three tiers.**
  1. *Platform hard lines,* not configurable: sexual content involving minors; on-screen sexual violence; graphic or celebratory atrocity; real-world how-to; propaganda written as real extremist groups; dehumanising content about real groups; incitement against real people.
  2. *Default veils,* which the table can open only by unanimous agreement: atrocity, torture and executions happen off screen and are seen through their consequences.
  3. *Table lines:* any player can add one anonymously at any time, and the most restrictive setting wins.
- Checks run on the engine's **input**: player orders, covert actions, co-GM proposals, deputy and AI-faction moves, and standing orders. Breaking a line is refused out of character. It is not attempted in the fiction.
- **A separate API organisation and key, used only by Anansi,** with its own billing. Never Josh's personal subscription or the account Vera runs on. Keep a fallback provider ready.

---

## B. Critical: gameplay structure (Josh decides)

### B1. The keenest player takes over [decision: interprets decisions 5, 9, 10]
**Problem.** No limit is enforced, and "active seats leave a bigger footprint." By week two the story revolves around whoever has the most free time. Today's "bandwidth" limit is a hidden currency judged by the LLM, which is worse than a visible one. Omnibus orders and order spam also work. (Player Experience, Exploits, Systems)
**Resolution (recommended).**
- **Visible operation slots derived from in-world assets**, for example slots = 1 + Network tier + Office tier, shown in every briefing. Each running operation holds one slot. Extra orders queue visibly instead of being quietly watered down.
- Slots are not spent, saved or traded, so there is no capital (decision 9 holds). More capacity is acquired in the fiction: hire staff, buy fixers, use the black market. That is the loophole play decision 9 is meant to protect.
- Talk, diplomacy, statements and flavour are always free.
- The structuring step splits omnibus orders into atomic operations. Bribing ten people is ten counterparties, each with their own price and their own risk.
- **The world reacts to the size of consequences, not the number of actions.** Exposure is rolled per operation, with no modifier for volume. That keeps decision 10: no scrutiny for being busy.
- Every human seat gets an equal narration slot in each briefing.

### B2. No ending and no scoreboard [decision: closes README §18 Q4]
**Problem.** Without visible numbers, async free text feels like typing into fog. Without an end date, campaigns fade instead of finishing. The end-of-game reveal and history, the best shareable, never arrives. Deputies and hidden agendas have nothing to aim at. (Player Experience ×2, Systems)
**Resolution (recommended).**
- **Seasons.** The default is about 3 real weeks or a set span of in-world time, and the finale date is announced at Session Zero. The season ends with a climactic formal session, an epilogue, a **full covert reveal**, the history page and a ledger for every seat. A sequel season can inherit the world.
- **Objectives per seat, with no single winner.** Each seat has 2-3 public mandate objectives from its archetype, plus an optional hidden agenda. Each objective is a Clock, so the engine scores it. Score against your own objectives, not against other players, which also removes kingmaking.
- **A dashboard on demand and at the top of every briefing:** a standing meter, means, 2-3 meters specific to the seat, and objective progress. Fixed in-world checkpoints (elections, budgets) give scored moments.
- **Decay detection.** If active seats fall below a threshold, the GM brings the finale forward in the fiction.

### B3. Absent players come back to a ruined seat [design]
**Problem.** A "cautious" deputy against an active human loses. Someone away for a week comes back deposed and quits. Formal sessions can be timed for when a target is away. (Player Experience, Exploits)
**Resolution.**
- Deputies are competent on **defence**, follow the seat's objectives, and can't make irreversible commitments (treaties, war, resignation, transfers). When those come up, the deputy stalls in the fiction.
- Players can declare leave in advance, with standing priorities.
- Irreversible outcomes against a seat (removal, death, total loss) happen **only at a formal session**, with notice and a deadline. The deputy asks for an adjournment, and one is granted automatically.
- Formal sessions need at least 24-48 hours' notice, respect availability windows declared at Session Zero, accept pre-filed conditional votes, and have a weekly cap per caller.
- Every catch-up briefing ends with 2-3 live comeback options.
- Standing orders compile into a small number of structured triggers that never escalate on their own. Deputies accept instructions only from their authenticated owner, so forged orders from other players are resolved as forgery attempts.

### B4. Co-GM votes as a weapon [decision: narrows decisions 14 and 15]
**Problem.** A majority bloc can vote in plagues, mutinies and windfalls aimed at one seat. An informed player can aim a "storm" at a covert plan they know about. A proposal itself can leak information. At 1-2 players the vote is god mode. "Never nullify" turns covert contingencies into guaranteed triggers, and unlimited "affect" cancels plans in practice anyway. (Player Experience, Exploits ×2, Systems ×2, Feasibility)
**Resolution (recommended).**
- Co-GM events are **exogenous only**: nature, markets, external powers, and NPCs without a seat. They can't name a seat or a player-controlled asset. The engine rejects events whose impact falls mostly on one human seat.
- Proposals are anonymous. Voters see a forecast built only from public facts. Each seat gets one proposal per N in-world weeks, and the table gets at most one passed event per tick. The event lands 1-2 ticks later, with an in-fiction warning.
- Defaults: **quorum of half the seats, two-thirds of votes cast to pass.** Co-GM is off below 3 human players.
- **Effects on covert plans come from a fixed table,** applied in code: Delay, Reroute (risk +1), Repurpose (progress carries over to a new goal of equal size), or Cash out (the committed means come back as leverage). At most one step per vote, with a cap on stacking. The validator rejects `cancelled` from this source.
- The protection covers **votes only**. Player actions, AI moves, discovery and loss of means can still defeat a covert plan. Dormant contingencies keep their means committed and build up exposure risk over time.

### B5. Betrayal between real friends and family [decision: limits decision 16 per campaign]
**Problem.** Weeks-long async betrayal, with no table and no "good game" handshake, costs real relationships. Lies can leak out of the game. Out-of-game group chats pool genuine GM briefings, which beats in-game opacity. The builder can read every secret. (Player Experience ×2, Exploits, Feasibility)
**Resolution.**
- **An intensity setting at Session Zero:** cutthroat / intrigue with caps / cooperative against the AI. The cooperative setting turns off covert play against other human seats.
- **No elimination.** A toppled player always moves to a new seat (exile, opposition, memoirist).
- A mandatory reveal at the end of the season. A table norm, set at Session Zero, that lies stay in the game. A table norm on out-of-game alliances (allowed, or in-game channels only).
- Briefings carry intelligence with confidence levels, and AI factions can feed one seat disinformation, so a pooled briefing is not ground truth. Each seat gets slightly different wording of shared facts (canary traps), so leaks can be traced in the fiction.
- **Josh hosts or GMs the first playtest instead of holding a seat.** Later, if he plays, covert payloads are encrypted at rest, debug access needs a "break glass" flag that the table is told about, and every exposure roll is logged with its odds.

### B6. v1 scope [decision: narrows decision 12 for v1 only]
**Problem.** 1, 5 and 100 players are three different games. Building for all three at once makes the friends-and-family game worse. (Player Experience, Feasibility, Systems)
**Resolution.** Tune v1 for **3-8 seats**. Solo is the tutorial and the funnel. Large-scale play is a later team-play format, and arenas are built only when a real campaign goes past about 12 players. Decision 12 stays as the long-term range. When arenas are built, the merge is deterministic code, with ownership of each entity, commutative deltas and means reserved at submission time. It is never done by an LLM.

---

## C. High: playability [design unless marked]

**C1. Blank page and Session Zero homework.** Every briefing ends with one dilemma that has a clock on it, plus 3 suggested moves; free text stays the input. Session Zero defaults to a quick start: a preset scenario, a menu of seats with one-line pitches and difficulty, and preset settings. Tick 1 is an inciting crisis that touches every seat. A 10-minute solo tutorial comes first.

**C2. Session Zero rigging.** After the world bible is written, a separate call scores each seat's means and reach on a fixed rubric, and the table approves any outliers. Only a coarse power tier is published per seat. Setting details that favour one seat need the table to sign off. Lines and veils control how content is shown (on screen or off), not which strategic options exist. [decision on whether lines are unilateral: recommended yes, per A5]

**C3. Notification fatigue.** Family campaigns default to one digest per player per day and **12-24 hour ticks**. Each player gets one "dispatch" thread holding the briefing, dashboard, pending decisions and deadlines. Only two things ping: a deadline for your seat, and an action aimed at you. Alliances use threads, not channels. Short ticks are only for crisis sessions.

**C4. The Fantasy President hook doesn't survive batching.** An instant persona reaction card appears when a public action or spin line is submitted, and consequences still resolve on the tick. Share cards contain public information only and carry a one-line context. The end-of-season reveal and history become the headline shareable.

**C5. Hierarchy inside one government.** The world bible gives each seat a domain the head can't veto. The head's overrides are public and cost standing. Firing a human seat needs a formal session and moves that player to a new seat. A cabinet deadlock becomes an in-fiction crisis on a timer.

**C6. Weak seats.** A viability check at Session Zero. A spotlight scheduler sends at least one meaningful decision to every human seat each tick. Low-reach seats trade in information and exposure, which gives them leverage.

**C7. Multiple seats, switching and joining mid-campaign** [decision: closes §18 Q5/Q6]. One human, one seat, one vote; the exception is solo mode, or several seats within one polity whose knowledge is shared anyway. Seat switches happen at season boundaries, or as the landing after a fall, and the knowledge a switching player carries comes with them as a "defector" Fact. A player joining mid-campaign takes over an AI-held seat with its own knowledge layer, and catches up from the deputy's log. Campaigns are invite-only.

**C8. Realism dial** [decision]. Split it into **Plausibility** (Strict / Plausible / Pulp: shifts the feasibility bands; never removes clamps) and **Historical inertia** (None / Tendencies / Strong: AI factions carry doctrine priors, and "historical pressure" events weaken as the world diverges). Invented worlds get a short laws-of-the-world list that the feasibility check reads.

**C9. Time model.** One in-world clock that only ticks advance. Ticks fire on a timer, when everyone has submitted, or when the host advances them. Operations have in-world ETAs. Delayed consequences are ScheduledEvent rows. Formal sessions are pinned to an in-world date, and the scheduler records both the in-world and real-time deadlines.

**C10. Tick reliability.** Each tick is a staged state machine: FREEZE, PROPOSE, VALIDATE, APPLY (one transaction), NARRATE, DELIVER (an outbox with dedupe). Every stage is persisted, so it can be resumed or retried. If the resolver fails, the tick is quiet and actions carry forward.

**C11. Live NPC conversations.** These run on a small model with only the faction's projected knowledge and a daily turn cap. Chat can produce offers only. A commitment becomes a `pending_deal` that the next tick accepts or rejects. Each action gets an immediate acknowledgement plus a one-line read on its odds.

**C12. Formats** [decision: which ships first]. A fixed core plus a declarative Format Profile per format: tick trigger, time per tick, reaction budget, deputies on or off, checkpoints, end condition, model tier. Scenarios are a preset world bible plus a profile. The prototype must run under two profiles (campaign and crisis).

**C13. Discord specifics.** Orders through a modal (paragraph input up to 4,000 characters, a visibility select, an optional spin line). Defer every interaction straight away. Briefings as embeds, with a link to the full text on the web. Rate-limit-aware delivery. `/briefing` pull as a fallback when a DM fails. A test DM at onboarding. One Discord server per campaign. Least-privilege permissions; the message-content intent isn't needed.

---

## D. Safety and legal for the friends-and-family playtest [gate: before the playtest]

**D1. Minors** [decision]. Record each player's age band. Under 13 is not allowed. If anyone aged 13-17 is in a campaign, a forced teen-table preset applies, DMs between players go through the bot, and a guardian is told. **Recommended: an adults-only first playtest.**

**D2. Harassment.** An anonymous `/pause` (X-card) and `/report`. Josh can audit reported exchanges, and players are told at Session Zero that opacity is a game rule, not privacy from the admin. Forgery is between seats only, never of the real person behind a seat. No targeting anyone's real identity or politics. Moderation applies to restricted, private and covert channels as well as public ones.

**D3. Real living people and the midterms** (US general election **Nov 3, 2026**) [decision]. In present-day settings, current officeholders and candidates appear by role ("the senior senator from Ohio") or as invented successors. The engine never generates criminal, sexual, corruption or health allegations against real living people. Every shareable carries a fiction watermark that can't be cropped out. No mastheads that mimic real newspapers, and no fake polls attributed to real polling firms.

**D4. Data.** A one-page notice at Session Zero: what is stored, who can read it (Josh and the model provider), retention (for example 90 days after the season ends), and how to get a copy or request deletion. Encryption at rest. API terms with no training on data and minimal retention. Per-player encryption keys in the log, so erasure works even though the log is append-only.

---

## E. Cost and hosting [decision: closes §18 Q2/Q3]

Rough estimate. Assumptions: 4 ticks a day, 3 actions per player per day, no prompt caching. "Hybrid" means a frontier model for the resolver and chronicle digests, and a small model for everything else.

| Players | Frontier only | Hybrid | Small only |
|---|---|---|---|
| 5 | ~$28/week | ~$7/week | ~$2.40/week |
| 20 | ~$96/week | ~$30/week | ~$8/week |
| 100 | ~$423/week | ~$138/week | ~$35/week |

- About 20% of tokens go to AI factions, 20% to NPC chat and 13% to briefings. The resolver is about 12%. Fan-out per seat and per faction is what drives cost, not the resolver.
- Prompt caching cuts input cost by about 40%. Running personas, press, deputies and chat on the lab GPU removes about 75% of API tokens. Reasoning models on the resolver raise frontier-only cost to about $45-60 a week at 5 players.
- At 100 players, hybrid works out to about $6 per player per month. That is close to Fantasy President's ~$8, so frontier-only can't be the pricing basis at scale.
- **Resolution:** hybrid split; a separate API organisation (A5); a budget governor per campaign, with a token meter per call class and a daily cap, that degrades in order (fewer persona calls, then merged press, then deputies held, then a longer tick); the engine and Postgres on the home server; event log backed up off-box every night; the lab GPU for small-model calls, falling back to the API. Calibrate all of this against costs measured in the minimum slice.

---

## F. Before public distribution [gate: before any release beyond friends and family]

- **Operating a service:** an LLC holding the repo, accounts and Discord app; a ToS, acceptable-use, privacy and content policy; an age gate (18+, or 16+ with the teen preset forced on); a moderation queue and ban process; Discord bot verification; payments through a merchant of record; DMCA registration if users can upload.
- **Name:** "Anansi" is crowded near games. There is Anansi AS (a Steam publisher), Heidelbar's *Anansi* card game, the Razer Anansi keyboard and Anansi Labs (decision software), plus *Anansi Boys*. It is fine as a codename. Before launch, run a trademark knockout search in classes 9/28/41/42 and get a clearance opinion, or use a compound mark.
- **Cultural credit:** an "About the name" page crediting the Akan/Ashanti source, Nyame and the story-box tale. No stereotyped imagery or voice. Get informal feedback from Ghanaian or Caribbean readers before launch.
- **Copying:** mechanics are free to use, but text, look and marks are not. Write an original disclaimer (remove "follows Fantasy President's model" from §16). Rename the "Fantasy President style" format. Restyle the history page as an in-world almanac, not Wikipedia.
- **Images (later client):** no photoreal or recognisable likeness of any real living person in any context. A visible AI/fiction label plus C2PA provenance. A face-match moderation pass before display.

---

## G. Revised build path (replaces README §19)

1. **Core schema and resolution pipeline on paper** (A1, A2), with the opacity model (A3) and the safety floor (A5).
2. **Minimum slice (about 2-3 weekends):**
   - one hand-written world bible in the fixed schema
   - 2 human seats plus 3 AI factions
   - orders via CLI or a single Discord channel, tagged public or covert
   - a tick triggered manually: structure, rate, roll, propose, clamp, apply, then a projected briefing for each seat
   - one press outlet
   - the canary leak test from day one
   - not included: deputies, arenas, co-GM, formal sessions or personas
3. **Exit criteria:** 10 ticks with 2 friends, zero canary leaks, players say collisions felt fair, and cost per tick measured.
4. **Then add, in order:** Discord DMs and modals, then the dashboard and objectives, then deputies with absence protection, then personas and the instant reaction card, then press, then formal sessions, then seasons and the reveal, then co-GM.
5. **Friends-and-family playtest** (3-8 seats, adults only, Josh as host), once the A and D gates are met.
6. Arenas, the later client and distribution only after the playtest and the F checklist.

---

## Decisions needed from Josh

Each one has a recommendation. Accepting a recommendation updates README §3 and §18.

1. **Capacity (B1):** visible operation slots derived from in-world assets, with the world reacting to the size of consequences rather than the number of actions. *Recommended: yes. Decision 9 still holds because nothing is spent.*
2. **Endings (B2):** seasons of about 3 weeks, objectives per seat, no single winner, a full reveal at the end. *Recommended: yes.*
3. **Co-GM (B4):** exogenous events only; quorum of half the seats and two-thirds to pass; off below 3 humans; a fixed adaptation table for covert plans; vote protection only. *Recommended: yes.*
4. **v1 scope (B6):** tune for 3-8 seats; arenas deferred until a campaign passes 12. *Recommended: yes.*
5. **Betrayal intensity (B5):** a per-campaign setting that can turn off covert play against other humans. *Recommended: yes.*
6. **First playtest:** Josh hosts rather than holding a seat; adults only (D1). *Recommended: yes.*
7. **Real people (D3):** role-not-name for current officeholders in present-day settings; no allegations against real people. *Recommended: yes.*
8. **Model and account (A5, E):** hybrid split; a separate API organisation used only by Anansi; a per-campaign budget cap. *Recommended: yes. The cap amount is Josh's call.*
9. **Seats (C7):** one human, one seat; switching at season boundaries; mid-campaign joiners take over AI seats. *Recommended: yes.*
10. **First format to ship (C12):** *Recommended: campaign, with solo as the tutorial.*


---

# Addendum: Josh's review notes (2026-10-19)

Josh's responses to the pressure test. Where an item conflicts with the resolution plan above, **this addendum supersedes it**. Each item states the decision, the design that follows from it, and any residual risk.

## H1. Full event log [decided]

**Decision.** Log every event across the whole campaign, for debugging and for backtracking the game.

**Design.**
- One append-only event log is the canonical record. It holds:
  - raw player text
  - structured intents
  - feasibility ratings
  - RNG seeds and rolls
  - proposed and applied deltas
  - narration
  - deliveries
  - votes
  - agent and deputy actions
  - AI-generated events
  - admin access
- World state is a projection of the log. It can be rebuilt at any tick.
- What the log enables:
  - replay
  - rewind to tick N
  - fork a campaign from any tick
  - the `/why <action>` trace
  - the end-of-season reveal and history page, which are generated from the log
- Log entries are versioned with the engine version, prompt versions and model IDs, so a replay reproduces what actually ran.

**Residual risk.** The log holds every secret. It is encrypted at rest and keyed per player (see D4), and every admin read is itself a logged event. This matters because Josh plays as well as hosts (H11).

## H2. Bug reporting [decided]

**Decision.** An easy in-game bug report that snapshots the current game state together with the log, plus a notes field.

**Design.**
- A `/bug` command (and later a button in the client) opens a modal with a free-text notes field. Optional fields: "what I expected" and "what happened".
- When the report is submitted, the engine attaches:
  - the campaign ID and the tick
  - a state snapshot
  - a log slice (the last N ticks, plus the reporter's last orders and briefings)
  - engine, prompt and model versions
  - RNG seeds
  - the Discord message IDs involved
- The bundle is enough to replay the tick locally and reproduce the bug.
- **Opacity.** What the reporter sees is limited to their own seat's view. The full true-state snapshot is attached to the report but encrypted, and only the admin can read it. Players are told this when they file.
- Reports are stored in the engine database. Josh triages them. Exporting a report to a GitHub issue is a manual step that strips anything covert.
- One-tap acknowledgement in the dispatch thread. The player who filed the report is told when it is fixed.

## H3. Player game agent: an interface layer between player and GM [decided]

**Decision.** Players never talk to the GM directly. Each player has a personal in-game agent that sits between them and the game.

**Design.**
- The agent:
  - turns free text into structured intents
  - asks clarifying questions when an order is ambiguous
  - shows perceived odds after an order is filed
  - writes the player's briefings and dashboard
  - suggests moves (this answers C1, the blank-page problem)
  - runs the seat as deputy while the player is away (this replaces the separate deputy in B3)
- **Information boundary.** The agent only ever holds `project(state, seat_id)`. If a player jailbreaks it, they only recover what that seat already knows. The GM resolver receives only structured intents and never raw player text, which closes off most prompt-injection paths. Together with the code gate in A3, this gives two layers of defence.
- **Constraints.**
  - The engine still validates every intent against the seat's inventory. A player can talk their agent into claiming an army; the claim fails at validation.
  - The agent advocates for its player, so it never sets odds or outcomes. The engine does that.
  - The agent is loyal by default. Disinformation reaches the player through the intelligence feeding the agent, never by the agent lying. A compromised aide is an opt-in twist set at Session Zero.
- **Cost.** One small-model instance per seat. This replaces most of the NPC-chat budget and the deputy budget in section E rather than adding to them.

## H4. Designable game agent [decided; restraints proposed]

**Decision.** Each player designs their agent's character, within restraints. Examples: a President's secretary, a spy's partner, a Congressman's wife.

**Design.** The player designs the **persona**. The **function** stays fixed.

The player sets:
- name
- role in the world (aide, spouse, handler, partner, chief of staff)
- personality and voice
- backstory
- relationship to the seat
- optionally, a portrait (in the later client)

**Proposed restraints.**
1. **No mechanical advantage.** The persona is narrative only. A "spy's partner who used to be a hacker" does not create a cyber asset. The persona is mapped to an in-world Entity with no new Assets, and the Session Zero balance check (C2) reviews it.
2. **Fixed knowledge boundary.** Whatever the backstory, the agent knows only what the seat knows.
3. **Loyalty is fixed** unless the table opts into the compromised-aide twist (H3).
4. **Safety lines apply.** The persona must fit the campaign's rating (H12) and the platform hard lines (A5).
5. **Real people.** An agent cannot be a real living private individual, such as a family member or friend by name. A persona based on a real public figure follows H10.
6. **Locked at Session Zero.** Cosmetic changes (voice, name) are allowed later. Role changes happen only through events in the fiction.

**Open question for Josh.** The agent is an in-world character. Can the world act on it (kidnap, turn, kill), given that harm can be permanent (H5)? Proposed: yes, as a Session Zero setting. Losing the agent degrades the seat's interface until a replacement is recruited in the fiction. The function never goes away entirely.

## H5. AI-generated events, including permanent harm [decided]

**Decision.** The GM's director layer generates events and weaves them into the narrative: tragedies, disasters, communication exposures, assassinations, scandals. Josh's pushback: **some events can cause permanent harm to a player.** This supersedes the "no permanent harm to an absent player" line in the previous reply. It also relaxes B3 for AI events.

**Design.**
- A director layer schedules pressure and pacing (tension curve, spotlight balance) and proposes events that pass through the engine like any other change.
- **Exposures must be earned.** A communication exposure can only reveal something that exists in the log and carried exposure risk. The director never invents scandals and never leaks covert facts with no causal path.
- **Permanent harm is allowed**, with three conditions:
  - It is set by a **Lethality** setting at Session Zero: Soft (setbacks only) / Hard (permanent losses) / Brutal (elimination possible from events).
  - It is drawn from risk that is already on the board, such as exposed operations, hostile actors or standing threats, so it can be traced in `/why`.
  - It is foreshadowed at least one tick ahead in the seat's briefing, where the seat could plausibly know.
- At Hard or Brutal, an absent player's agent defends the seat (H3), but the world does not wait for them.
- Events stay within the campaign's rating and the platform hard lines (A5).

## H6. Goals: modes [decided]

**Decision.** Goals are a Session Zero mode, as proposed. This closes B2.
- **Goals mode.** Players set goals at the start and can add or revise them as the game progresses. The agent helps turn each goal into an engine Clock so progress is scored. Scoring is per seat, with no single winner.
- **Open mode.** A sandbox with no set ending.
- **Recommendation for both modes.** Chapter breaks with a partial covert reveal and a history snapshot, so an open campaign still gets payoffs.

## H7. Deputy is the player's agent [decided]

B3 stands, with the player's agent (H3) acting as deputy. It already holds the player's priorities and voice, plays competent defence, and stalls irreversible commitments while the player is away.

## H8. Betrayal and elimination [decided]

**Decision.** Betrayal intensity is set at Session Zero, and **cutthroat elimination rules remain available**. This replaces "no elimination" in B5.

**Design.**
- Session Zero options: cooperative / intrigue with caps / cutthroat / cutthroat with elimination.
- **When a player is eliminated,** they continue as a completely new character that they design to fit the world as it now stands. Their agent helps build the character from current public facts. The new character:
  - passes the same balance check as everyone else (C2)
  - starts with public knowledge only
  - has a fresh inventory seeded by the engine
  - can appear in the world however the fiction allows: an exile, a rival, a journalist, an insurgent
- The player keeps their memory of what their old seat knew. That is allowed and treated as drama, not policed.
- The mandatory end-of-season reveal and the table norms from B5 still apply.

## H9. Group-vote proposals are not limited [decided]

**Decision.** Group-vote (co-GM) proposals are not limited. This removes the per-seat proposal cap and the "exogenous only" restriction in B4.

**What still holds.**
- Every passed event goes through the rules engine and the safety floor.
- A vote affects covert plans but never nullifies them.
- Proposals are anonymous.
- A passed event lands with a lead time.

**Open questions for Josh.**
- Quorum and pass threshold (proposed: half the seats, two-thirds of votes cast).
- Whether co-GM is available at 1-2 players.
- Whether proposals may name a specific player's seat.

Residual risk: with targeting unlimited, a majority bloc can vote events onto one player. If that is intended, the vote itself is the safeguard.

## H10. Real people and real names [decided]

**Decision.** Real people and their names may appear in the game, but the game never invents allegations about them. This replaces "role, not name" in D3.

**Design.**
- Real people can appear by name and can act within the fiction. In the game they can sign treaties, give speeches, lose elections or be outmanoeuvred.
- **No invented allegations.** The engine and director never generate claims of crime, corruption, sexual misconduct, health conditions or private scandal about real people. The documented public record is fine. If a player's covert play "exposes" a real figure, the exposure is a fictional in-game event that stays inside the game, and narration frames it as the player's operation, not as a fact about the person.
- Every shareable carries the uncroppable fiction watermark (D3). This matters right now: the US general election is on **Nov 3, 2026**.
- Real living private individuals (anyone who is not a public figure) are never included by name.

## H11. Josh holds a seat in the first playtest, solo first [decided]

**Decision.** Josh holds a seat in the first playtest, starting in single-player (solo) mode. This replaces "Josh hosts" in B5.

**Implications.**
- Solo goes first, so the first playable target is solo career. This answers decision 10 for the first playtest: solo first, then campaign.
- In solo, the conflict of interest between builder and player doesn't arise.
- When Josh plays multiplayer, the B5 safeguards apply:
  - covert payloads encrypted at rest
  - break-glass debug access that is announced to the table
  - every admin read logged (H1)

## H12. Not adults-only; ratings decided in Session Zero [decided]

**Decision.** The playtest is not adults-only. A content rating is set in Session Zero. This replaces "adults only" in D1.

**Design.**
- Session Zero sets a campaign **rating** (for example Teen / Mature / Unrestricted within the platform floor), along with lines and veils.
- The platform hard lines (A5) apply at every rating.
- **Recommendation:** if anyone under 18 is at the table, the rating options are capped at Teen.
- Discord's terms require all users to be 13 or older.
- The other D1 protections stay when minors play:
  - age band recorded at onboarding
  - DMs between players routed through the bot
  - a guardian told

## H13. Scope: up to 10 players plus a solo tutorial [decided]

**Decision.** v1 supports 1 to 10 players, with the solo tutorial. This replaces "tune for 3-8" in B6. Arenas (regional resolution) are removed from v1, because one resolver handles 10 seats. Long-range scale (decision 12) stays in the parameters as a future format.

**Cost.** Roughly $15 a week at 10 players on the hybrid split, before measuring.

---

## H14. Group votes: simple majority, off below 3 players [decided]

- **Threshold.** A simple majority of votes cast passes a proposal. This replaces the two-thirds proposal in B4 and H9.
- **Minimum table.** Group votes are not available with 2 or fewer human players. Josh wrote "2>". It is read here as "2 or fewer", which matches the question asked.
- **Quorum.** Not specified. Proposed default: at least half the human seats must vote, otherwise the proposal lapses. Still open.
- **Residual risk.** A bare majority with unlimited proposals (H9) makes a 3-2 bloc decisive. The engine and the safety floor still apply to every event, and a vote still adapts covert plans rather than cancelling them.

## H15. Agents can be harmed or killed [decided]

**Decision.** The world can act on a player's agent: kidnap, turn or kill it. This answers the H4 question.

**Design.**
- **Losing the agent blinds the seat.** Briefings drop to the public feed only, plus raw intercepts with no analysis. There are no perceived odds and no suggested moves. While the seat has no agent, orders are still accepted, but they go to the engine as written, with no clarifying questions.
- **Recovery happens in the fiction.** The player recovers by finding or procuring a new agent: recruit, buy, borrow from an ally, or rescue the old one. This is an acquisition action (A4) that takes time and carries exposure. It is never automatic.
- **The replacement starts with partial knowledge.** It inherits the seat's knowledge layer only as far as the fiction allows. A rescued agent keeps its memory. A new hire is briefed from the seat's surviving records.
- **A turned agent** follows the compromised-aide rules. It is available at cutthroat (H16) without a separate opt-in.
- **Absence.** An absent seat whose agent is lost has no deputy. The engine holds that seat's existing standing orders, and no new commitments are made.
- **The player redesigns the replacement's persona** (H4), within the same restraints.

## H16. Default intensity: cutthroat [decided]

**Decision.** The Session Zero default is **cutthroat**. Tables can choose softer or harsher settings.

**Interpretation, to confirm.** The default betrayal intensity (H8) is cutthroat, which means full covert play between human seats. Elimination stays a separate opt-in. The default lethality (H5) is **Hard**, meaning permanent losses are possible, because that is the lethality setting that fits cutthroat play.

## H17. No content rating unless specified [decided]

**Decision.** By default a campaign has no content rating. A rating applies only if the table sets one at Session Zero. This supersedes H12's rating step and its recommended teen cap.

**What still holds at every table.** The platform hard lines (A5), and the table's own lines and veils.

**Residual risk.** No rating is applied automatically when minors are present. The D1 protections (recorded age band, DMs routed through the bot, guardian told) are kept as defaults. A Session Zero prompt reminds the table to set a rating when anyone under 18 is present. It is not enforced.

## H18. Models: Josh's Claude API [decided]

**Decision.** For now, Anansi uses Josh's Claude API key and Claude models. This replaces the separate API organisation in A5 and E.

**Design.**
- One Anthropic model family on a hybrid split: a frontier Claude model for the resolver and chronicle digests, and a small Claude model for agents, NPCs, press and personas.
- The key lives in the engine's environment and never appears in logs or bug reports.
- Prompt caching is on from day one.

**Residual risk and mitigations.**
- Generated war and terror content runs on Josh's own account, so a policy flag lands there. The input and output classifiers in A5 and the abstraction rule are the protection.
- Use a **separate API key** under the same account. This keeps spend visible and makes the key revocable on its own. It is not a separate organisation.
- Set a workspace or key spend limit.
- Keep Anansi traffic off the subscription Vera runs on.
- The budget cap amount is still Josh's call.

---

## Updated decision status (after H14 to H18)

1. Capacity: visible operation slots (B1). **Open**
2. Endings (B2). **Decided:** Goals mode and Open mode (H6)
3. Co-GM (B4). **Decided:** proposals unlimited (H9), simple majority, off at 2 or fewer players (H14). Quorum is **open**; proposed default is half the seats.
4. v1 scope (B6). **Decided:** 1 to 10 players plus solo (H13)
5. Betrayal (B5). **Decided:** cutthroat by default (H16), elimination opt-in (H8)
6. First playtest (B5, D1). **Decided:** Josh holds a seat, solo first (H11). No rating unless set (H17).
7. Real people (D3). **Decided:** names allowed, no invented allegations (H10)
8. Model and account (A5, E). **Decided:** Josh's Claude API on a hybrid split (H18). Budget cap amount is **open**.
9. Seats, switching and joining (C7). **Partly decided:** eliminated players redesign (H8). Multi-seat and mid-campaign joining are **open**.
10. First format (C12). **Decided for the playtest:** solo first (H11)

Still open:
- operation slots (B1)
- co-GM quorum
- budget cap
- multi-seat and mid-campaign joining
- whether "cutthroat" also implies elimination and Hard lethality by default (H16)
