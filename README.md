# Anansi

An AI-run multiplayer statecraft game. Game parameters, v1 (2026-10-19).

Status legend: **[decided]** = Josh's call. **[proposed]** = Vera's design, not yet approved. **[open]** = needs a decision.

---

## 1. Pitch

Anansi is an AI-run multiplayer statecraft game. Think of an expanded, gamified Model UN run like a D&D campaign. Players describe a world. An AI game master builds it and runs everything nobody is playing. Each player takes a seat: head of state, general, foreign minister, spymaster, journalist, opposition leader, or anything the world supports. They can sit in the same government or in rival ones. Players act in their own words, any time. The world reacts, keeps secrets, and keeps moving whether they are online or not.

**Name.** Anansi is the West African (Akan) spider who won all the world's stories from the sky god Nyame by cleverness, not force. The name fits three things: storytelling, a web of connections, and trickery as a legitimate way to win.

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
**What Anansi adds:** multiplayer, player-built worlds, async real-time play, hidden information, a human co-GM layer, and many seat types beyond president.

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
| 12 | Player count | Fully flexible: 1 to 100+. |
| 13 | Co-GM | Optional. The group can write events into the narrative. |
| 14 | Co-GM mechanism | Group vote. |
| 15 | Votes vs. covert plans | A vote can affect a covert plan but never cancel it outright. |
| 16 | Opacity | Players see only what is public in the world and what other players choose to disclose. Covert play is a deliberate option. |
| 17 | Builders | Josh and Vera, not the KT team. |
| 18 | Home | kedartech/anansi. |

## 4. Core loop [proposed]

1. **Session Zero.** Players describe the world and pick seats. The GM writes the world bible.
2. **Briefing.** Each seat gets its own briefing, written only from what that seat knows.
3. **Act.** Players post actions in free text any time, each tagged with a visibility level (§7). They can also talk to each other directly.
4. **Resolve.** Each resolution tick, the GM batches every action submitted in that window, plus the AI factions' moves, and resolves them together.
5. **React.** The world updates. Personas, the press, factions and markets respond. Consequences are scheduled for later ticks.
6. **Repeat.** Formal sessions, delayed consequences and co-GM events punctuate the flow.

## 5. Session Zero [proposed]

Every campaign starts here, as at a tabletop.

- **Setting prompt:** free text, for example "1914, the archduke survives", "modern Europe but Texas seceded", or "three moon colonies fighting over water".
- **Realism dial:** strict historical, plausible, or anything goes.
- **Tone:** for example grounded, satirical, pulp or grim. Set by the players.
- **Clock speed:** how much in-world time passes per real day, and the tick interval.
- **Lines and veils** (the tabletop safety tool): lines are topics never on the table; veils are topics that happen off-screen only.
- **Hidden roles:** allowed or not.
- **Co-GM vote rules:** quorum and threshold (§10).
- **Seat selection:** each player picks or invents a seat. The GM confirms it exists in the world bible or adds it.
- **Output:** a **world bible**. It covers polities, leaders, factions, resources, geography, tensions, open threads, and each seat's starting knowledge, which may differ between seats.

## 6. Seats [proposed]

- A seat is any role with agency in the world. Examples: head of state, minister, general, admiral, intelligence chief, ambassador, opposition leader, governor, CEO, journalist, arms dealer, revolutionary.
- Several players can share a government. Their seats come with **differing interests**: the defence minister wants budget, the finance minister wants cuts. Players in the same government should feel political tension, not just cooperation.
- Each seat has **means** (money, people, assets, contacts, legal powers) and **reach** (what it can affect directly).
- **Hidden agenda (optional):** a private objective known only to that player, assigned or chosen at enrolment.
- **AI deputy:** every seat has one. It acts on the player's standing orders while they are away (§9).

## 7. Opacity and information model [decided principle; proposed mechanics]

- **One truth, many views.** The engine holds one true world state. Each seat has its own knowledge layer: what it has seen, been told, or uncovered.
- **Visibility levels on every action and event:**
  - **Public:** the world sees it.
  - **Restricted:** named seats only, for example a cabinet or an alliance.
  - **Private:** the actor only. The effects may still become visible.
  - **Covert:** actively concealed, with exposure risk.
- **Players control disclosure.** They can share, leak, lie or forge. The game does not fact-check what one player tells another.
- **Discovery is gameplay.** Intelligence work, journalists, informants, audits, defectors and plain bad luck can expose covert actions over time. The rules engine rates the odds.
- **Covert seats are first-class.** Spy, double agent, shadow financier and saboteur are valid seats.
- **Hard engine requirement:** briefings and narration are generated from a seat's filtered view, never from the full truth. One player's private or covert action must not leak into another player's briefing. This gets its own test suite (leak tests) before any playtest.

## 8. Actions and limits [proposed, built on decision 9]

There is no points budget. Any means a player can describe is open, legal or not. What bounds a move is the world itself:

- **Means:** does the seat actually have the money, people, contacts or access?
- **Time and bandwidth:** orders take in-world time to carry out, and a seat's staff and assets can only run so many operations at once. Forty orders give you forty half-done jobs.
- **Counterparties:** a deal needs someone on the other side, AI or human, with their own interests and price.
- **Exposure:** illicit routes can leak, be traced, or be sold out by the contact you trusted.

For each action, the rules engine rates **feasibility** and **risk**. It does not refuse creativity. An implausible action is attempted and fails in the fiction, often in an interesting way.

**Action anatomy:** the free-text order, an optional public spin line, a visibility tag, and an optional target or counterparty.

## 9. Async model and activity [decided principle; proposed mechanics]

- **Resolution ticks:** actions are batched per window (for example every 4-6 hours; set in Session Zero) and resolved together. Collisions are real, and being awake at 3 a.m. gives no first-mover advantage.
- **Active seats** leave a bigger footprint. More of the world reacts: rivals counter, allies lean in or pull away, and new opportunities and threats open up. This is a response, not a penalty.
- **Idle seats** run on standing orders through the AI deputy, cautiously and in character. The world notices the drift: rivals probe, and aides make small calls the player might not have chosen. A returning player gets a catch-up briefing.
- **Formal sessions** (hearings, budget votes, summits, UN sessions, councils of war) are scheduled in the fiction with a real-time deadline. Every relevant seat answers, or its deputy answers for it. These are the sync points that make async play feel shared.

## 10. Co-GM layer [decided: optional, group vote; proposed mechanics]

- Any player can propose a world event, for example "a volcanic eruption closes the strait" or "the old king dies".
- The table votes. Quorum and threshold are set in Session Zero **[open: defaults]**.
- A passed event enters the narrative as a GM-injected event and still passes through the rules engine.
- **Effect on covert plans:** a vote can **affect** a covert plan but never **nullify** it. Voters usually don't know the plan exists, because they are voting on a world event. The plan has to adapt: it may be delayed, cost more, carry more exposure risk, or need a new route. It is never cancelled outright, and the vote never reveals it to the table.

## 11. AI game master [proposed]

The GM plays:

- every unplayed nation, faction and seat
- the press: several outlets with different slants
- the public, as personas in the Fantasy President style
- markets and institutions
- consequences scheduled for later ticks
- the narrator for each seat's briefing

**Referee split, the one firm technical rule.** The LLM reads actions and **proposes** structured consequences. A deterministic **rules engine** owns the state: it validates the proposals, clamps their size, applies them, and logs them. Narration is generated from the *applied* result, not the other way round. This blocks "my army wins and everyone surrenders" and keeps play fair between players.

**Memory:** structured world state, an append-only event log, and a rolling campaign chronicle that the GM reads each tick.

## 12. Scale: 1 to 100+ [decided range; proposed mechanics]

- **1 player:** the AI fills every other seat. This doubles as solo mode and onboarding.
- **Small table (2-10):** a single resolution pass per tick.
- **Large (10-100+):** the world splits into **arenas** (regions, alliances, theatres). Each arena resolves in parallel, then a merge pass handles effects across arenas. No single AI call reads every player's orders.

## 13. Formats on one engine [decided principle; proposed list]

- **Campaign:** the headline mode. A long-running async world over weeks.
- **Crisis session:** one live 60-90 minute sitting, like a Model UN crisis committee.
- **Solo career:** Fantasy President style; also onboarding.
- **Scenarios:** preset starts, for example "destroyer hit in the strait", "coup at dawn" or "1914".
- **Military strategy:** a seat family (generals, admirals, theatre commanders) plus war scenarios, not a separate game.

## 14. Platform and architecture [decided: Discord first; proposed architecture]

- **Engine first, clients second.** A headless game engine with an API holds all the state and rules.
- **Discord client (v1):**
  - public channels for public events and press
  - alliance or cabinet channels for restricted information
  - DMs for private briefings and covert orders
  - slash commands for actions and votes
  - scheduled posts for ticks and formal sessions
- **Game client (later):** web or desktop, on the same API, with AI-made world maps, character portraits, illustrations and animation. **Guardrails** on generated media: content rules follow Session Zero lines and veils, there are no real-person likenesses in compromising contexts, and generated output is moderated before display.

## 15. Shareables [proposed]

- per-seat briefings
- newspaper front pages from several outlets
- persona reaction cards (the viral screenshot)
- a campaign timeline
- an end-of-campaign history in the style of the Wikipedia-page trick
- later: generated maps and portraits

## 16. Safety and moderation [proposed]

- Session Zero lines and veils, enforced in GM prompts and in output checks.
- Real-world settings are clearly fictional; disclaimers follow Fantasy President's model.
- Players see each other's text, so public posts go through a moderation pass. This matters more once the game is distributed.
- **Prompt injection:** player text is data, never instructions. The referee split (§11) is the main defence.
- Black-market, illegal and violent actions are in-fiction only. The GM narrates outcomes and does not produce real-world how-to detail.

## 17. Cost [open]

- Use a small model for personas, press and deputies, and a stronger model for tick resolution and Session Zero.
- At friends-and-family scale, Josh pays for it. A 100-player campaign changes the maths, so a pricing plan is needed before distribution.
- Measure the cost per tick per active seat in the first prototype.

## 18. Open questions

1. Co-GM vote defaults: quorum and threshold.
2. Model choice and per-campaign cost ceiling.
3. Engine stack: language, database, hosting (home server vs. cloud).
4. Victory and end conditions: do campaigns end, and how is "winning" defined per seat (objectives, legacy score, survival)?
5. Can a player hold more than one seat, or switch seats mid-campaign?
6. Can players join an existing campaign mid-way?

## 19. Build path

1. Design doc in the repo: the state schema, visibility model, action pipeline, tick resolver, arenas and co-GM votes.
2. Session Zero prototype: a prompt in, a world bible out.
3. One-tick prototype: two players plus AI nations. Actions go in; structured deltas and per-seat briefings come out.
4. Rules engine with tests, including the leak tests from §7.
5. Discord client v1.
6. A friends-and-family playtest campaign before it counts as working.
