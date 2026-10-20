# Anansi

An AI-run multiplayer statecraft game. Game parameters, v2 (2026-10-19).

v2 folds in the decisions from [docs/pressure-test-2026-10-19.md](docs/pressure-test-2026-10-19.md) (sections A-G and addendum H1-H20). Where this README and the pressure test differ, this README is current. The pressure test is kept as design history and reasoning.

Status legend: **[decided]** = Josh's call. **[proposed]** = Vera's design, not yet approved. **[open]** = needs a decision.

---

## 1. Pitch

Anansi is an AI-run multiplayer statecraft game. Think of an expanded, gamified Model UN run like a D&D campaign. Players describe a world. An AI game master builds it and runs everything nobody is playing. Each player takes a seat: head of state, general, foreign minister, spymaster, journalist, opposition leader, or anything the world supports. They can sit in the same government or in rival ones. Players act in their own words, any time, through a personal in-world agent. The world reacts, keeps secrets, and keeps moving whether they are online or not.

**Name.** Anansi is the West African (Akan) spider who won all the world's stories from the sky god Nyame by cleverness, not force. The name fits three things: storytelling, a web of connections, and trickery as a legitimate way to win. "Anansi" is a codename; see §19 before any public release.

## 2. Origin and reference

Inspired by **Fantasy President Career** (fantasypresidentcareer.com, @president_mode, Frontier Gaming Ltd). It went viral on X in Sep 2026 (two posts at 13M views each; 212k+ players). Researched 2026-10-19:

- Monthly briefing with an event, or a quiet month where you set the agenda.
- Free-text policy response. No multiple choice. Optional "public spin" line.
- 24 AI voter personas score each policy 0-100 with a quote. Left, centre and right papers write headlines. Approval moves across 50 states. About 30 power blocs track your record.
- Delayed consequences: a policy spawns a follow-up event months later.
- Fixed checkpoints: midterms at month 22, re-election at month 46.
- Shareables: a generated Wikipedia page of your presidency; Story Mode crisis scenarios.
- Free to month 12, then £5.99/mo.
- Why it spreads: screenshots of polarized reactions to the same policy.

**What Anansi keeps:** free-text input, persona reactions, delayed consequences, shareable artifacts.
**What Anansi adds:** multiplayer, player-built worlds, async real-time play, hidden information, a per-player agent, a human co-GM layer, and many seat types beyond president.

**Closest existing games** (novelty check, 2026-10-19): Pax Historia and Open Historia (nation-level, whole room skips time together, no hidden information); STATECRAFT (secret allegiances, but a single 30-minute sitting); AI game masters such as Friends & Fables and AI Dungeon (one shared story, no secrets between players); human-run nation roleplay on Discord and NationStates (the social format, without AI running it). The combination of several players inside one government, each seeing only their own slice, in a world that runs while they are offline, was not found anywhere. The build effort goes into secrets and seats.

Not the reference: Studio 14b's scripted "President Simulator" on Steam (Early Access Oct 28, 2026).

## 3. Decided parameters

| # | Parameter | Decision |
|---|---|---|
| 1 | AI backend | Yes. Free-text actions with AI-generated reactions. |
| 2 | Headline mode | Multiplayer campaigns. Players enrol and choose a seat, in the same government or a different one. |
| 3 | Formats | Several formats on one engine. Military strategy is one seat or scenario type, not the whole game. |
| 4 | World creation | Players prompt it, D&D style: real countries in a real historical start, real countries in a fictional scenario, or an invented world. |
| 5 | Timing | Asynchronous, not strictly turn-based. The world responds to differences in activity between players. |
| 6 | Formal sessions | Hearings, votes, summits and similar procedure exist in the fiction and act as sync points. |
| 7 | Audience | Friends and family first, with a path to wider distribution. |
| 8 | Tone | Set by the players for each campaign. |
| 9 | Action economy | No required capital currency. Real-world loopholes are open: back channels, black markets, illegal dealings. Moves play out on plausibility and consequence. |
| 10 | Activity | A busy player is not punished with scrutiny. The world still responds to what they do. |
| 11 | Platform | Discord first. Long term, a gamified client with AI-made animation, characters, world maps and illustrations, with guardrails. |
| 12 | Player count | v1: 1 to 10 players plus a solo tutorial. Long term: 100+ as a later format (arenas). |
| 13 | Co-GM | Optional. The group can write events into the narrative by group vote. Proposals are not limited to world events. |
| 14 | Co-GM vote rules | Simple majority of votes cast; a tie fails. Quorum: half the human seats, rounded up, within one tick. Off at 2 or fewer human players. |
| 15 | Votes vs. covert plans | A vote can affect a covert plan but never cancel it outright. |
| 16 | Opacity | Players see only what is public in the world and what other players choose to disclose. Covert play is a deliberate option. |
| 17 | Builders | Josh and Vera, not the KT team. |
| 18 | Home | kedartech/anansi. |
| 19 | Event log | Every event in a campaign is logged, append-only. The log is the canonical record; world state is a projection of it. |
| 20 | Player agent | Players never talk to the GM directly. Each player has a personal in-world agent that structures orders, briefs, advises, and runs the seat as deputy when the player is away. Players design the agent's persona; its function is fixed. |
| 21 | Agent harm | The world can act on a player's agent: kidnap, turn or kill it. Losing it blinds the seat until a replacement is found in the fiction. |
| 22 | AI-generated events | The GM's director layer generates events (tragedies, disasters, exposures, assassinations, scandals). Some can cause permanent harm to a player. |
| 23 | Goals | Session Zero mode: Goals (set and revise trackable goals, scored per seat) or Open (sandbox, no set ending). |
| 24 | Betrayal and elimination | Set at Session Zero. Default: cutthroat, lethality Hard, elimination on. An eliminated player returns as a new character they design. |
| 25 | Operation slots | 3 per seat to start, cap 5, earned or lost in the fiction. Talk and diplomacy never use a slot. |
| 26 | Seats per human | One human, one seat (solo players may switch at chapter breaks). |
| 27 | Joining mid-campaign | By invite, up to 10. Take over an AI seat or build a new character. Vote from the second tick. |
| 28 | Real people | May appear by name and act in the fiction. The game never invents allegations about them. |
| 29 | Content rating | None by default. The table sets one at Session Zero if it wants one. Platform hard lines always apply. |
| 30 | First playtest | Josh holds a seat, solo first. Not adults-only. |
| 31 | Models | Josh's Claude API, separate key, hybrid split (frontier for the resolver, small for everything else). Players may bring their own Claude key for their seat's calls. |
| 32 | Budget | Provisional: $10/month solo, $30/week at 10 players (alert at $20), $150/month backstop on the key. Reset after the first build measures cost per tick. |
| 33 | Bug reports | In-game `/bug` snapshots state and log with a notes field. |
| 34 | Engine language | Python. Pydantic models are the schema; the deterministic core (rate, roll, clamp, apply, project) stays pure functions with no I/O so it can be ported to Rust later. Postgres for storage. |

## 4. Core loop [proposed]

1. **Session Zero.** Players describe the world, pick seats, and design their agents. The GM writes the world bible in the fixed schema (§12).
2. **Briefing.** Each seat's agent writes that seat's briefing, from that seat's knowledge only.
3. **Act.** Players talk to their agent in free text, any time. The agent turns orders into structured intents, tagged with a visibility level (§7). Players can also talk to each other directly.
4. **Resolve.** Each resolution tick, the engine batches every intent in that window, plus the AI factions' moves and any director events, and resolves them together (§13).
5. **React.** The world updates. Personas, the press, factions and markets respond. Consequences are scheduled for later ticks.
6. **Repeat.** Formal sessions, delayed consequences, director events and co-GM events punctuate the flow. Chapter breaks and season ends bring partial or full reveals.

## 5. Session Zero [proposed]

Every campaign starts here, as at a tabletop. Defaults to a quick start: a preset scenario, a menu of seats with one-line pitches, and preset settings. Everything can be customised.

- **Setting prompt:** free text, for example "1914, the archduke survives", "modern Europe but Texas seceded", or "three moon colonies fighting over water".
- **Plausibility:** Strict / Plausible / Pulp. Shifts feasibility bands; never removes engine clamps.
- **Historical inertia:** None / Tendencies / Strong. How hard AI factions carry real-world doctrine, weakening as the world diverges.
- **Tone:** for example grounded, satirical, pulp or grim.
- **Content rating:** none unless set. Lines (never on the table) and veils (off-screen only). Any player can add a line anonymously at any time; the most restrictive setting wins. If anyone under 18 is at the table, Session Zero reminds the table to set a rating.
- **Goals mode or Open mode** (decision 23).
- **Betrayal intensity:** cooperative / intrigue with caps / cutthroat (default) / cutthroat with elimination (default on).
- **Lethality:** Soft (setbacks only) / Hard (permanent losses, default) / Brutal (elimination from events).
- **Agent harm:** on or off (default on).
- **Compromised-aide twist:** opt-in (automatic at cutthroat).
- **Clock speed:** in-world time per real day, and the tick interval. Family campaigns default to 12-24 hour ticks and one digest per player per day.
- **Availability windows** per player, which formal sessions respect.
- **Seat selection:** each player picks or invents a seat. The GM confirms it exists in the world bible or adds it. A balance check scores each seat's means and reach on a fixed rubric; outliers need the table's approval.
- **Agent design:** each player designs their agent's persona (§10).
- **Table norms:** lies stay in the game; out-of-game alliances allowed or in-game channels only.
- **Output:** a **world bible** in the fixed schema. It covers polities, leaders, factions, resources, geography, tensions, open threads, and each seat's starting knowledge, which may differ between seats. Seat backstory locks when Session Zero ends.

## 6. Seats [proposed]

- A seat is any role with agency in the world. Examples: head of state, minister, general, admiral, intelligence chief, ambassador, opposition leader, governor, CEO, journalist, arms dealer, revolutionary.
- Several players can share a government. Their seats come with **differing interests**: the defence minister wants budget, the finance minister wants cuts. The world bible gives each seat a domain the head can't veto; the head's overrides are public and cost standing.
- Each seat has **means** (typed assets and relationships: money, people, assets, contacts, legal powers) and **reach** (what it can affect directly). Means are an inventory, seeded at Session Zero and changed only by logged events. A means the seat doesn't have becomes an acquisition action: recruit, buy, build, turn an agent. That takes time and slots and carries exposure.
- **Objectives.** In Goals mode, each seat has 2-3 public mandate objectives plus an optional hidden agenda, each tracked as an engine clock. Score is against your own objectives; there is no single winner.
- **Operation slots.** 3 to start, cap 5. Each running operation holds one slot until it resolves; extra orders wait in a visible queue. Slots are gained by hiring staff, fixers or a network, and lost through scandal or lost assets. They are never spent, saved or traded. Statements, talks and diplomacy are always free.
- **One human, one seat.** In solo, the player holds one seat and may switch at chapter breaks.
- **Falling.** A seat can be lost to events or to other players. The player returns as a new character designed to fit the world as it now stands: passes the same balance check, starts with public knowledge only, fresh inventory seeded by the engine. The player keeps their own memory of what the old seat knew; that is drama, not policed.
- **Joining mid-campaign.** By invite, up to 10 players, starting at the next tick. A newcomer takes over an AI-run seat (inheriting its knowledge layer) or builds a new character via the redesign path. Their agent gives a catch-up briefing. They vote from their second tick.

## 7. Opacity and information model [decided principle; proposed mechanics]

- **One truth, many views.** The engine holds one true world state. Each seat has its own knowledge layer: what it has seen, been told, or uncovered. AI factions, NPCs and press outlets have their own knowledge layers too.
- **Visibility levels on every action and event:**
  - **Public:** the world sees it.
  - **Restricted:** named seats only, for example a cabinet or an alliance.
  - **Private:** the actor only. The effects may still become visible.
  - **Covert:** actively concealed, with exposure risk.
- **Players control disclosure.** They can share, leak, lie or forge. The game does not fact-check what one player tells another. Forgery is between seats only, never of the real person behind a seat.
- **Discovery is gameplay.** Intelligence work, journalists, informants, audits, defectors and plain bad luck can expose covert actions over time. The engine rates the odds and rolls them.
- **Covert seats are first-class.** Spy, double agent, shadow financier and saboteur are valid seats.
- **Enforced by code, not by prompt.** Every event carries `visibility` and `known_by`. Every narrator, agent, AI faction, NPC and outlet is built from `project(state, actor_id)`. The full-truth resolver emits only enums, IDs and numbers; none of its prose reaches a player. Each seat has its own chronicle; no shared true chronicle is ever fed to a narrator. Players see **perceived** odds, from their own knowledge, only after an order is filed and locked. Conditional orders trigger only on events the seat can see.
- **The opacity boundary is the human,** not the seat. Tests check the union of everything each human can see.
- **Leak tests** run in CI before any playtest: canary strings planted in covert orders and searched for in all outbound text over 50+ ticks; counterfactual tests (remove one covert fact, AI moves must not change); the prompt-injection suite; Discord metadata checks.
- **Builder as player.** When Josh plays multiplayer: covert payloads encrypted at rest, break-glass debug access announced to the table, every admin read logged.

## 8. Actions and limits [proposed, built on decisions 9, 10, 25]

There is no points budget. Any means a player can describe is open, legal or not. What bounds a move is the world itself:

- **Means:** does the seat actually have the money, people, contacts or access? If not, it's an acquisition first.
- **Slots:** a seat runs at most its slot count of operations at once. The rest queue visibly.
- **Time:** operations have in-world ETAs. Orders take time to carry out.
- **Counterparties:** a deal needs someone on the other side, AI or human, with their own interests and price. Promises are non-binding talk; transfers and contracts are atomic engine primitives (offer, then accept).
- **Exposure:** illicit routes can leak, be traced, or be sold out by the contact you trusted. Exposure is rolled per operation, with no modifier for how many a seat runs.

For each action, the engine rates **feasibility** and **risk** from state alone. It does not refuse creativity. An implausible action is attempted and fails in the fiction, often in an interesting way. Omnibus orders are split into atomic operations; bribing ten people is ten counterparties.

**The world reacts to the size of consequences, not the number of actions.**

**Action anatomy:** the free-text order (to the agent), an optional public spin line, a visibility tag, and an optional target or counterparty. Orders lock at the tick cutoff; edits roll into the next tick.

## 9. Async model and activity [decided principle; proposed mechanics]

- **Resolution ticks:** intents are batched per window and resolved together. Collisions are real, and being awake at 3 a.m. gives no first-mover advantage. Ticks fire on a timer, when everyone has submitted, or when the host advances them. One in-world clock that only ticks advance.
- **Active seats** leave a bigger footprint. More of the world reacts: rivals counter, allies lean in or pull away, and new opportunities and threats open up. This is a response, not a penalty.
- **Idle seats** are run by the player's agent (§10) on the seat's objectives and standing orders. It plays competent defence and stalls irreversible commitments (treaties, war, resignation, transfers) in the fiction. Players can declare leave in advance. A returning player gets a catch-up briefing ending with 2-3 live comeback options.
- **Absence protection, with limits.** Irreversible outcomes against a seat from *other players* (removal, total loss) happen only at a formal session, with notice and a deadline; the agent asks for an adjournment and one is granted automatically. *Director events* (decision 22) do not wait: at Hard or Brutal lethality the agent defends, but the world moves on.
- **Formal sessions** (hearings, budget votes, summits, UN sessions, councils of war) are scheduled in the fiction with a real-time deadline, at least 24-48 hours' notice, respecting declared availability windows, with pre-filed conditional votes and a weekly cap per caller. Every relevant seat answers, or its agent answers for it.
- **Notifications.** One dispatch thread per player holding the briefing, dashboard, pending decisions and deadlines. Only two things ping: a deadline for your seat, and an action aimed at you.

## 10. Player agent [decided; proposed mechanics]

Players never talk to the GM directly. Each player has a personal in-world agent: a chief of staff, handler, aide, spouse, partner, whatever fits.

**Function (fixed):**
- turns free text into structured intents, and asks clarifying questions when an order is ambiguous
- shows perceived odds after an order is filed
- writes the seat's briefings and dashboard
- suggests moves (every briefing ends with one dilemma on a clock and 3 suggested moves)
- runs the seat as deputy while the player is away (§9)
- helps turn goals into engine clocks (Goals mode) and helps design a new character after elimination

**Persona (designed by the player):** name, role in the world, personality and voice, backstory, relationship to the seat, portrait (later client). Locked at Session Zero; cosmetic changes allowed later; role changes only through events in the fiction.

**Restraints:**
- No mechanical advantage. The persona is narrative only; it creates no assets.
- The agent holds only `project(state, seat_id)`. A jailbroken agent reveals nothing the seat didn't already know.
- The engine validates every intent against the seat's inventory. A player can talk their agent into claiming an army; the claim fails at validation.
- The agent advocates; it never sets odds or outcomes.
- Loyal by default. Disinformation reaches the player through the intelligence feeding the agent, never by the agent lying. A compromised aide is a Session Zero twist (automatic at cutthroat).
- Not a real living private individual. Real public figures follow decision 28.

**Harm (decision 21).** The world can kidnap, turn or kill the agent, drawn from risk already on the board and foreshadowed a tick ahead. Losing it **blinds the seat**: briefings drop to the public feed plus raw intercepts, no perceived odds, no suggested moves, orders go to the engine as written, slots drop to 1, and an absent seat has no deputy. Recovery is an acquisition action in the fiction (recruit, buy, borrow, rescue): never automatic, takes time, carries exposure. A rescued agent keeps its memory; a new hire is briefed from the seat's surviving records. The player redesigns the replacement's persona.

## 11. AI game master [proposed]

The GM plays:

- every unplayed nation, faction and seat, each from its own knowledge layer
- the press: several outlets with different slants
- the public, as personas reacting to public actions
- markets and institutions
- the **director**: paces tension and spotlight, and proposes events (decision 22). Exposures must be earned: a communication exposure can only reveal something in the log that carried exposure risk. The director never invents scandals and never leaks covert facts with no causal path. Permanent harm follows the lethality setting, is drawn from risk already on the board, and is foreshadowed a tick ahead where the seat could plausibly know.
- consequences scheduled for later ticks
- live NPC conversations on a small model, with only the faction's projected knowledge and a daily turn cap. Chat produces offers only; a commitment becomes a pending deal the next tick accepts or rejects.

**Referee split, the one firm technical rule.** The LLM reads intents and **proposes** structured consequences. A deterministic **rules engine** owns the state: it validates the proposals, clamps their size, applies them, and logs them. Narration is generated from the *applied* result, not the other way round. See §12 and §13.

**Every human seat gets an equal narration slot in each briefing.** A spotlight scheduler sends at least one meaningful decision to every human seat each tick.

## 12. Event log and core schema [decided principle; proposed schema]

**The log is canon (decision 19).** One append-only event log holds: raw player text, structured intents, feasibility ratings, RNG seeds and rolls, proposed and applied deltas, narration, deliveries, votes, agent and deputy actions, director events, admin access. Entries are versioned with engine, prompt and model versions. World state is a projection of the log and can be rebuilt at any tick.

What the log enables: replay, rewind to tick N, fork a campaign, the admin `/why <action>` trace, bug-report bundles, and the end-of-season reveal and history page.

**Protection.** Encrypted at rest, keyed per player (so erasure works despite append-only). Every admin read is itself a logged event.

**Fixed, world-agnostic ontology.** Campaigns create instances of these types and never extend them. Session Zero maps setting nouns onto them ("oxygen quota" becomes a Meter; "the Moon Senate" becomes an Office):

- Entity (polity, faction, person, org, location); the player agent is an Entity
- Asset (Force / Wealth / Network / Office / Info / Facility, with tier 0-5, controller, location, loyalty)
- Relationship (signed disposition plus price and leverage tags)
- Track / Meter (bounded, with a maximum change per tick)
- Clock / Operation (segments, ETA, exposure, known_by, slot held)
- Fact (truth plus a visibility ACL)
- ScheduledEvent
- Commitment (treaties and promises as structured clauses)
- Seat (human or AI, slots, agent, objectives, knowledge layer)

The LLM may only propose typed deltas against existing IDs, using constrained decoding. A proposal that fails validation is retried once, then dropped and logged. It is never partially applied.

## 13. Resolution pipeline [proposed]

Four stages per intent, every tick:

1. **Structure** (in the player's agent, low privilege): free text becomes a typed Intent: a verb from a closed list, the actor, target IDs, the committed means (asset IDs), visibility and duration. Meta-text such as "referee notes" or rule claims is dropped. The resolver never sees raw player text.
2. **Rate** (engine, state only): a feasibility band (Impossible / Long shot / Contested / Likely / Assured) from means, reach, counterparty disposition and price, slots, and the plausibility setting.
3. **Roll** (engine): a seeded RNG (seed = campaign + tick + action) gives an outcome tier (Fail with complication / Partial / Success / Success with opportunity). Exposure has its own independent roll. Impossible always resolves as an interesting failure.
4. **Propose and narrate** (LLM then engine): the LLM proposes deltas allowed by that tier; the engine clamps and applies them; narration is written per seat from the applied result and that seat's projection.

**Tick state machine:** FREEZE, PROPOSE, VALIDATE, APPLY (one transaction), NARRATE, DELIVER (outbox with dedupe). Every stage is persisted, so it can be resumed or retried. If the resolver fails, the tick is quiet and intents carry forward.

## 14. Co-GM layer [decided; proposed mechanics]

- Any player can propose an event or a rule change. Proposals are not limited to world events and are not capped per seat.
- Proposals are anonymous. The table votes within one tick. **Quorum:** half the human seats, rounded up (abstaining counts). **Pass:** simple majority of votes cast; a tie fails. Lapses without quorum. **Off at 2 or fewer human players**; switches on when a joiner makes 3.
- A passed event goes through the rules engine and the safety floor like any other change, and lands 1-2 ticks later with an in-fiction warning.
- **Effect on covert plans** comes from a fixed table applied in code: Delay, Reroute (risk +1), Repurpose (progress carries to a goal of equal size), or Cash out (committed means return as leverage). At most one step per vote, with a cap on stacking. The validator rejects `cancelled` from this source. The vote never reveals the plan to the table.
- This protection covers votes only. Player actions, AI moves, discovery and loss of means can still defeat a covert plan.
- Residual risk, accepted: with unlimited proposals and a bare majority, a 3-2 bloc can vote events onto one player. The vote itself is the safeguard.

## 15. Chapters, seasons and endings [decided mode; proposed mechanics]

- **Goals mode:** players set goals at Session Zero and revise them during play; each becomes a clock the engine scores. Scoring is per seat; no single winner. Fixed in-world checkpoints (elections, budgets) give scored moments.
- **Open mode:** sandbox, no set ending.
- **Both modes:** chapter breaks with a partial covert reveal and a history snapshot, so an open campaign still pays off. A season (about 3 real weeks by default) ends with a climactic formal session, an epilogue, a full covert reveal, the history page and a ledger per seat. A sequel season can inherit the world. If active seats fall below a threshold, the director brings the finale forward.
- **Dashboard** on demand and at the top of every briefing: standing, means, slots, 2-3 seat-specific meters, objective progress.

## 16. Scale and formats [decided range; proposed mechanics]

- **v1: 1 to 10 players** on a single resolver pass. Solo is the tutorial and the funnel.
- **Later: 100+** via arenas (regions, alliances, theatres) that resolve in parallel with a deterministic merge pass. Built only when a real campaign passes about 12 players. Never merged by an LLM.
- **Formats on one engine,** each a declarative Format Profile (tick trigger, time per tick, reaction budget, deputies on/off, checkpoints, end condition, model tier):
  - **Solo career:** ships first (decision 30). Fantasy-President-style; also onboarding.
  - **Campaign:** the headline mode. Long-running async world over weeks.
  - **Crisis session:** one live 60-90 minute sitting, like a Model UN crisis committee.
  - **Scenarios:** preset starts, for example "destroyer hit in the strait", "coup at dawn" or "1914".
  - **Military strategy:** a seat family (generals, admirals, theatre commanders) plus war scenarios, not a separate game.

## 17. Platform and architecture [decided: Discord first; proposed architecture]

- **Engine first, clients second.** A headless game engine with an API holds all the state and rules.
- **Discord client (v1):**
  - one Discord server per campaign, least-privilege permissions
  - orders through an ephemeral modal (paragraph input, visibility select, optional spin line) or DM with the agent
  - public channels for public events and press; alliance threads for restricted information; DMs for briefings and covert orders
  - briefings as embeds with a link to the full text on the web; `/briefing` pull as a fallback; a test DM at onboarding
  - slash commands for actions, votes, `/bug`, `/pause` (X-card), `/report`, `/key`
  - scheduled posts for ticks and formal sessions
  - no metadata leaks: alliance membership and role names reveal nothing; agent and deputy status is never shown
- **Hosting:** engine and Postgres on the home server; event log backed up off-box nightly.
- **Game client (later):** web or desktop, on the same API, with AI-made world maps, character portraits, illustrations and animation. **Guardrails** on generated media: content rules follow Session Zero lines and veils, no real-person likenesses, generated output moderated before display.

## 18. Shareables [proposed]

- per-seat briefings
- an instant persona reaction card when a public action or spin line is submitted (consequences still resolve on the tick)
- newspaper front pages from several outlets
- a campaign timeline
- chapter and end-of-season reveals, and an end-of-campaign history page styled as an in-world almanac
- later: generated maps and portraits

Share cards contain public information only, carry a one-line context, and carry an uncroppable fiction watermark.

## 19. Safety, content and legal [decided floor; proposed mechanics]

**Three tiers.**
1. **Platform hard lines,** not configurable: sexual content involving minors; on-screen sexual violence; graphic or celebratory atrocity; real-world how-to; propaganda written as real extremist groups; dehumanising content about real groups; incitement against real people.
2. **Default veils,** which the table can open only by unanimous agreement: atrocity, torture and executions happen off screen.
3. **Table lines:** any player adds one anonymously at any time; the most restrictive setting wins.

- **Abstraction is an engine rule.** Weapons of mass destruction, explosives, weapon conversion, intrusion and laundering are abstract capability clocks. Narration gives results, never procedures, quantities, precursors or code. Input and output classifiers back this up.
- Checks run on the engine's **input**: player orders, covert actions, co-GM proposals, agent and AI-faction moves, standing orders. Breaking a line is refused out of character, not attempted in the fiction.
- **Prompt injection:** player text is data, never instructions. The agent boundary (§10) and the referee split (§11) are two layers of defence.
- **Real people (decision 28):** may appear by name and act in the fiction. The engine and director never generate claims of crime, corruption, sexual misconduct, health conditions or private scandal about real people. The documented public record is fine. A player's covert "exposure" of a real figure is narrated as the player's operation inside the game. Real living private individuals are never included by name. No mastheads that mimic real newspapers; no fake polls attributed to real polling firms. The US general election is **Nov 3, 2026**; the watermark matters now.
- **Minors.** Under 13 is not allowed (Discord's terms). When anyone 13-17 plays: age band recorded, DMs between players routed through the bot, a guardian told, and a Session Zero reminder to set a rating. Not enforced beyond that (decision 29).
- **Harassment.** Anonymous `/pause` and `/report`. Josh can audit reported exchanges; players are told at Session Zero that opacity is a game rule, not privacy from the admin. No targeting anyone's real identity or politics. Moderation applies to every channel level.
- **Data.** A one-page notice at Session Zero: what is stored, who can read it (Josh and the model provider), retention (for example 90 days after the season), and how to get a copy or request deletion. API terms with no training on data and minimal retention.
- **Before public distribution** (not needed for friends and family): an LLC holding the repo, accounts and Discord app; ToS, acceptable-use, privacy and content policy; an age gate; a moderation queue and ban process; Discord bot verification; payments through a merchant of record. Trademark knockout search on "Anansi" (crowded near games: Anansi AS, Heidelbar's *Anansi*, Razer Anansi, Anansi Labs) or use a compound mark. An "About the name" page crediting the Akan/Ashanti source. Original disclaimer text and format names, not copied from Fantasy President. For the later client: no likeness of any real living person, a visible AI/fiction label plus C2PA provenance, a face-match moderation pass.

## 20. Models and cost [decided]

- **Josh's Claude API,** on a separate key under the same account with its own spend limit. Never the subscription Vera runs on. The key lives in the engine's environment and never appears in logs or bug reports. Prompt caching on from day one.
- **Hybrid split:** a frontier Claude model for the tick resolver, Session Zero and chronicle digests; a small Claude model for agents, NPCs, press, personas and the director's drafts. Roughly $15/week at 10 players before measuring.
- **Bring your own key (decision 31).** A player can connect their own Claude key via an ephemeral modal or web form, encrypted at rest, revocable with `/key remove`. It pays only for that seat's calls (agent, briefings, NPC chats they start). The resolver never runs on a player's key; it sees the full truth. The key changes who pays, never which model runs. Each player sets their own cap; on failure or cap, the seat falls back to the host key inside the host budget.
- **Budget governor** per campaign: a token meter per call class and spend per player and per key. On reaching a cap it degrades in order: fewer persona calls, then merged press, then agents on standing orders only, then a longer tick. The game slows down rather than stopping.
- **Provisional caps (decision 32):** $10/month solo playtest; $30/week at 10 players with an alert at $20; $150/month backstop set on the key in the Anthropic console. Reset once the minimum slice measures real cost per tick.
- Residual risk: war and terror content runs on Josh's account. The abstraction rule and classifiers are the protection. Keep a fallback provider in mind.

## 21. Open questions

1. Whether co-GM proposals may name a specific player's seat (currently allowed by decision 13; revisit after the first multiplayer slice).
2. Exact lead time for co-GM events (1-2 ticks proposed).
3. Season length default (about 3 weeks proposed) and chapter-break cadence.

## 22. Build path

1. **Design doc** (`docs/design.md`): event types and the log, the state schema as a projection, the agent boundary and `project()`, the four-stage pipeline and clamp rules, visibility and `reveal()`, the tick state machine, the safety floor hooks. Written as pydantic models (decision 34).
2. **Minimum slice, solo** (about 2-3 weekends): one hand-written world bible in the fixed schema; 1 human seat with its agent plus 3 AI factions; orders via CLI; a tick triggered manually running structure, rate, roll, propose, clamp, apply, narrate; one press outlet; a canary leak test from day one (AI factions must not act on the seat's covert facts). Not included: director events, co-GM, formal sessions, personas, Discord.
3. **Exit criteria:** 10 ticks solo, zero canary leaks, cost per tick measured, Josh says collisions with AI factions felt fair.
4. **Two-human slice:** a second human seat, Discord DMs and modals, the human-boundary canary test, perceived odds.
5. **Then add, in order:** dashboard and Goals mode; absence protection with the agent as deputy; personas and the instant reaction card; press; formal sessions; chapters, seasons and the reveal; co-GM votes; director events and agent harm; elimination and redesign; joining mid-campaign; bring-your-own key.
6. **Friends-and-family playtest** (up to 10 seats, Josh holding a seat) once the leak tests, safety floor and data notice are in place.
7. Arenas, the later client and distribution only after the playtest and the pre-distribution checklist in §19.
