# Revisionist entry-point inventory

Read-only snapshot, 2026-10-23. **main** = `/home/thejoshbq/code/kedartech/revisionist`. **web** = `/home/thejoshbq/code/kedartech/revisionist-web` (feat/web-client). Paths are relative to each tree's root. Items tagged `(web)` exist only on the web branch. Discord and engine files are cited from main; the web branch's copies were not compared line by line. Its `cli.py` matches main's line numbers through line 1131 (checked by grep on every decorator).

## Map of the repo
- `src/revisionist/cli.py`: Typer CLI, the only console script (`revisionist`). There is no `__main__.py`.
- `src/revisionist/core/`: pure rules with no I/O: events, fold/state, referee, contest, goals, canary (leak scan), world compile.
- `src/revisionist/engine/`: tick runner, scheduler, SQLite and Postgres stores, Claude transport (`llm.py`), Session Zero, `engine/discord/` bot, `engine/prompts/` model prompts. Web branch adds `engine/web/`.
- `src/revisionist/engine/migrations/` (Postgres SQL), `worlds/` (world YAML), `schema/` (committed event JSON Schema), `deploy/` (systemd user units, backup scripts), `docs/`.
- `tests/` (pytest; `recordings/` replays model calls), `.github/workflows/ci.yml`. Web branch adds `web/` (static client), `0009_web.sql`, and `tests/test_web.py`.

## 1. Process / service entry points
- `pyproject.toml:20-21`: `[project.scripts]` `revisionist = "revisionist.cli:app"`. Installed with `uv tool install --editable '.[postgres,discord]'` (`docs/deploy.md:68`).
- `src/revisionist/schema_export.py:36-37`: `python -m revisionist.schema_export` regenerates the committed schema.
- `deploy/systemd/revisionist-discord.service:15`: `ExecStart=%h/.local/bin/revisionist discord run`. Type=simple (`:10`), `Restart=on-failure` (`:16`), env file `%h/.config/revisionist/revisionist.env` (`:11`), `WantedBy=default.target` (`:27`).
- `deploy/systemd/revisionist-scheduler.service:14`: `ExecStart=... revisionist scheduler run`. Type=oneshot (`:9`), same env file (`:10`).
- `deploy/systemd/revisionist-scheduler.timer:5`: `OnCalendar=*:0/10` (every 10 min). Triggers the scheduler unit (`:9`).
- `deploy/systemd/revisionist-backup.service:10`: `ExecStart=%h/.local/bin/revisionist-backup`. Oneshot, admin env file `:9`. Installed from `deploy/backup/revisionist-backup.sh` (`docs/deploy.md:95`).
- `deploy/systemd/revisionist-backup.timer:5`: `OnCalendar=*-*-* 03:30:00`, `RandomizedDelaySec=15min` (`:7`).
- `deploy/backup/revisionist-restore-drill.sh:1-37`: manual drill, not on a timer. Installed as `revisionist-restore-drill` (`docs/deploy.md:96`). Runs `revisionist db verify` at `:37`.
- Bot entry: `cli.py:1098-1131` (`discord run`) → `engine/discord/bot.py:464` `run()` → `RevisionistBot.run(token)`.
- Scheduler entry: `engine/scheduler.py:278` `run()`, called from `cli.py:862`.
- Solo entry: `revisionist tick` (`cli.py:386`), run by hand on SQLite.
- `(web)` `revisionist web serve` (`cli.py:1140`) runs in the foreground. The web branch has no systemd unit or deploy file for it (`deploy/` and `docs/deploy.md` have none).

## 2. CLI commands
Exit codes: 1 error, 2 refused, 3 leak (`cli.py:56-58`). `--seat` takes an ID or a name (`cli.py:99-102`).

Top-level:
- `version` (`cli.py:127`): print engine version.
- `new WORLD [--seat PERSON]` (`:133`): compile a world YAML (name in `worlds/` or a path) and start a campaign. A realism world needs `--seat` (a person's key or name), which claims them and closes Session Zero; a legacy world refuses it.
- `dashboard [--seat]` (`:265`): standing, means, goal progress.
- `status [--seat]` (`:308`): campaign status as your seat sees it.
- `briefing [--seat]` (`:319`): agent briefing. Exit 3 if withheld (`:331`).
- `order TEXT [--seat] [--visibility private|public|covert] [--spin ≤280]` (`:342`): file an order.
- `tick [--offline]` (`:386`): resolve one tick. `--offline` uses the scripted resolver and narrator (`cli.py:52-54`). On a realism campaign it runs `engine/realism_tick.run_realism_tick` and prints the wake and the new date. Code decides every outcome; the only model call is the PROSE stage, one call per document author per wake through `engine/prose.ClaudeProseWriter`. `--offline` swaps in `TemplateProseWriter` (plain period sentences, no model).
- `lever NAME [key=value ...] [--seat]`: realism. File an order by pulling a lever on your card; every parameter is checked against its spec. Prints the order id and its first step.
- `step ORDER ACTION [key=value ...] [--seat]`: realism. Act at the step of your order that waits on you (`pass`, `refuse`, `amend` with edits, ...). A final step that enacts the order runs its resolver in the same batch, so its quantities (vote counts) are set before the next tick.
- `consult PERSON "QUESTION" [--seat]`: realism. Ask a person on your card (by surname, full name or entity id) over your card's channel to them. The counsel model speaks as that person from their projection only (`core/realism/person.project_person`). Lands a `counsel_held` record private to your seat, plus `cost_logged`; changes nothing in the world and files nothing. Exit 2 for someone not within reach.
- `aide [--seat] [--offline]`: realism. Your aide's brief from your projected view: a summary of unread pages, the decisions waiting on you, and up to three draft orders from your levers, printed as `revisionist lever ...` commands and never filed. `--offline` uses the template aide (no model).
- `why TARGET [--break-glass] [--reason]` (`:423`): trace an intent or event through the log.
- `replay --to TICK` (`:451`): rebuild state at a tick and print its hash.
- `cost` (`:460`): model spend per tick and in total; on a realism campaign also per in-world day.
- `leaks [--humans]` (`:467`): canary scan. `--humans` needs Postgres and Discord links.
- `history [--seat]` (`:639`): tick-0 brief from the record.
- `agent --name --role [--voice] [--backstory] [--relationship] [--seat]` (`:650`): design your agent's persona.
- `begin` (`:670`): close Session Zero and start play.

Groups:
- `establish` (`:160-161`):
  - `pitch TEXT [--effort 1-4]` (`:170`): read a pitch into a setting card.
  - `confirm N [--strike] [--end YYYY-MM-DD] [--days 1-365] [--plausibility] [--inertia] [--lethality] [--betrayal] [--tone] [--tick-hours 1-168] [--effort]` (`:195`): open the game from start point N.
- `season` (`:276-277`):
  - `end` (`:280`): full reveal, winners; ticks stop.
  - `report` (`:296`): season report.
- `character` (`:500-501`):
  - `roster` (`:531`), `search DESCRIPTION` (`:537`), `claim NAME [--new]` (`:550`), `pick NAME [--new]` (`:563`).
- `goal` (`:502-503`):
  - `suggest {history|could_have}` (`:577`), `own TEXT` (`:596`), `confirm LABEL` (`:609`).
  - `flags` (`:620`), `approve SEAT` (`:626`): host balance flags.
- `db` (`:676-677`):
  - `migrate [--app-role]` (`:684`): apply migrations as `REVISIONIST_ADMIN_DSN`.
  - `copy --from SQLITE --to PG_URL [--campaign]` (`:700`), `verify [--campaign]` (`:731`).
- `admin` (`:747-748`):
  - `show EVENT_ID [--reason]` (`:751`): print an event; a sealed private one is logged as an admin read.
  - `erase --seat [--reason] [--campaign]` (`:769`): destroy a seat's key.
  - `new-master-key PATH` (`:809`).
- `scheduler` (`:830-831`):
  - `run [--campaign] [--offline]` (`:857`), `status` (`:883`), `advance` (`:911`), `pause [--reason]` (`:917`), `resume` (`:926`).
- `discord` (`:934-935`):
  - `link --user ID [--campaign] [--seat]` (`:977`), `unlink` (`:1000`), `invite [--campaign] [--seat] [--hours 1-720]` (`:1016`), `invites` (`:1050`), `channel public CHANNEL_ID` (`:1063`), `seats` (`:1086`), `run` (`:1098`).
- `(web)` `web` (`cli.py:1133-1134`): `serve [--host 127.0.0.1] [--port 8740] [--static DIR] [--offline-prose]` (`:1140-1148`). `--offline-prose` makes the realism `advance` write pages with `TemplateProseWriter` instead of `ClaudeProseWriter`.

## 3. Discord surface
- Registration: `engine/discord/bot.py:112` `register()`. Every command is guild-scoped (`guild=guild`). Command names are listed at `bot.py:37-62`.
- `bot.py:442-443` `setup_hook`: syncs the guild command tree. `bot.py:435`: default intents only.
- `bot.py:73` `answer()`: defers ephemerally and replies by followup. Replies slower than `LONG_REPLY_S` (`bot.py:34`) are also DMed.
- Seat commands (bot.py handler → `engine/discord/handlers.py` method):
  - `/order text visibility spin`: `:124` → `:210` `order`. File an order.
  - `/briefing`: `:134` → `:232` `briefing`. Latest agent briefing.
  - `/dispatch`: `:139` → `:281` `dispatch`. Latest tick briefing.
  - `/join code`: `:147` → `:296` `join`. Redeem an invite code for a seat.
  - `/status`: `:154` → `:241` `status`.
  - `/dossier`: `:296` → `:200` `dossier`. What your seat holds, knows and faces.
  - `/dashboard`: `:301` → `:493` `dashboard`.
  - `/report`: `:422` → `:883` `report`. Season report after it ends.
  - `/bug notes`: `:415` → `:263` `bug`. Report a bug pinned to this tick.
  - `/key`: `:427` → `NOT_YET` stub (`bot.py:429`, `handlers.py:100`).
  - `/character action [text]`: `:237` → `:614` `character`. Actions: roster, search, claim, pick.
  - `/goal action [text]`: `:250` → `:646` `goal`. Actions: history, could_have, own, confirm.
  - `/history`: `:261` → `:664` `history`.
  - `/agent name role voice backstory relationship`: `:266` → `:674` `agent`.
- Host commands:
  - `/establish pitch text`: `:162` → `:374` `establish_pitch`.
  - `/establish confirm choice strike end days`: `:175` → `:392` `establish_check` (refuses early), then `:429` `establish_confirm`. Result is DMed (`bot.py:195-200`).
  - `/retire [campaign]`: `:206` → `handlers.py:515` `retire`.
  - `/invite [seat]`: `:216` → `:550` `invite`. One-time `/join` code.
  - `/channel`: `:223` → `:594` `channel`. Make this channel the public channel.
  - `/begin`: `:285` → `:680` `begin`. Then DMs each opening dossier (`bot.py:288-293`, `handlers.py:694` `opening_dossiers`).
  - `/season-end`: `:306` → `:502` `season_end`.
  - `/advance`: `:311` → `:718` `advance`. Run the next tick now.
  - `/pause`: `:316` → `:757` `pause`. `/resume`: `:321` → `:765` `resume`.
  - `/host` group (`bot.py:325`, added at `:411`):
    - `seats` (`:328` → `:774` `host_seats`), `invites` (`:333` → `:779`).
    - `link user seat` (`:339` → `:786`), `unlink seat` (`:351` → `:803`).
    - `flags` (`:358` → `:810`), `approve seat` (`:364` → `:815`).
    - `schedule` (`:371` → `:824`), `cost` (`:376` → `:833`).
    - `leaks` (`:381` → `:841`), `why target reason` (`:390` → `:851`).
    - `replay tick` (`:400` → `:871`), `verify` (`:407` → `:876`).
- Listeners:
  - `bot.py:445-461` `on_message`: a DM starting `order:` → `handlers.order`. Any other DM → `handlers.advise` (`handlers.py:181`). Guild messages and bots are ignored.
- Components: none. Nothing in `src/` uses `discord.ui` views, modals or selects.
- Scheduled tasks in process: none. Ticks come from the systemd timer and `scheduler run` (see section 1).

## 4. Engine entry points
Ticks and orders:
- `engine/runner.py:58` `play_tick()`: run or resume one tick. Calls `faction_turns` (`:90`) for AI factions, then `run_tick`, then the leak report.
- `engine/tick.py:243` `run_tick()`: close one tick (resolve, narrate, deliver). Safe to re-run after a crash. Raises `NotInPlayError` (`:123`).
- `engine/tick.py:215` `file_intent()`: validate an intent with the referee. Appends `IntentFiled` or `IntentRejected`.
- `engine/play.py:77` `give_order()`: player order → `agent.receive_order` (`play.py:100`) → `file_intent` (`agent.py:493`).
- `engine/agent.py:451` `receive_order()`. `engine/agent.py:212` `class Agent`; `:285` `deputy_turn()` files faction deputies' intents. `:515` `counterfactual_diff()`.
- `engine/runner.py:114`: deputy intents are filed with `KeywordSafety`.
- `engine/scheduler.py:278` `run()`: tick every due campaign once. `:133` `ScheduleTable` with `advance`, `pause`, `resume`, `retire` (`:208`). `:226` `register()`, `:235` `live_campaigns()`, `:78` `due()`.

Realism (Phase 4):
- `engine/realism_tick.py` `run_realism_tick(..., prose=writer)` (the web `advance` passes one too; see section 5): stages ADVANCE, PIPELINES, RESOLVE, FEEDS, then PROSE when a writer is passed (`play_tick` always passes one). PROSE gathers pages issued since the last `tick_closed` without prose, groups them by author, makes one writer call per author, drops prose that states a figure the page lacks (`core/realism/prose.invented_numbers`), and lands `document_written` events with their own `tick_stage_completed`. A `TransportError` leaves the pages bare and the tick still closes; call costs go on `tick_closed.costs`.
- `engine/prose.py`: `ProseWriter` protocol (takes `DocumentView`s and an `Author`, never `State`), `ClaudeProseWriter`, `TemplateProseWriter`. Prompt `engine/prompts/document_prose.md`.
- `engine/counsel.py` `consult()`: counsel; prompt `engine/prompts/counsel.md`.
- `engine/aide.py` `aide_for()` / `brief()`: the aide; prompt `engine/prompts/aide.md`.
- `scripts/prose_ab.py --live`: the P0 prose A/B (two models, five pages, blind sheet and separate key). Refuses without `--live`.

Briefings and reads:
- `engine/play.py:116` `get_briefing()`: agent briefing for a seat (model call through the transport).
- `engine/advise.py:21` `advise()`: DM question to the agent (`AGENT_MODEL`, `advise.py:31`).
- `engine/play.py:136` `dashboard_lines()`, `:186` `status_lines()`. `core/dashboard.py:38` `dashboard()`.
- `core/dossier.py:52` `dossier()`. `core/orders.py:57` `order_results()`.
- `engine/campaign.py:226` `briefings_dir()`. `engine/campaign.py:208` `FileDeliverer`.

Contests and the referee:
- `core/referee.py:224` `validate()`, `:306` `rate()` (feasibility band), `:481` `roll()`, `:788` `admissible()`, `:864` `reveal()`.
- `core/contest.py:42` `momentum_moves()`, `:63` `decisions()`. A contest is decided when its countdown runs out.
- `core/history.py:35` `settle()`: scheduled history events.
- `core/fold.py:82` `apply()`, `:94` `replay()`, `:105` `replay_to()`, `:74` `state_hash()`. The reducer everything agrees on.

Establish, start, retire:
- `engine/campaign.py:85` `world_path()`, `:96` `load_world()`, `:100` `new_campaign()`. Events come from `core/world_compile.py:95` `compile_world()`. `:229` `check_history()`.
- `engine/pitch.py:183` `read_pitch()`: pitch → 2–3 start points (model; `PITCH_MODEL` at `:19`).
- `engine/session_zero.py:140` `draft_world()` (model via `engine/draft.py:208` `draft()`), `:161` `establish()`, `:187` `propose()`, `:196` `confirm()`.
- `engine/session_zero.py:1068` `begin()`: closes Session Zero and starts play.
- `engine/session_zero.py:1043` `flagged()`, `:1057` `approve()`: balance flags.
- `engine/season.py:26` `end_season()` (full reveal, ticks stop), `:55` `report()`.
- Retire: `engine/scheduler.py:208` `ScheduleTable.retire()`; Discord handler `handlers.py:515`.

Seats:
- `engine/campaign.py:117` `human_seats()`, `:122` `human_seat()`, `:135` `resolve_seat()` (ID or name), `:145` `faction_seats()`.
- `engine/session_zero.py:405` `claim()`, `:439` `search()`, `:644` `pick()` (a real person from the record).
- `engine/host_ops.py:155` `link_seat()`, `:180` `unlink_seat()`.
- `engine/discord/tables.py:43` `SeatLinks` (`link` `:47`, `unlink` `:61`, `users` `:90`). `:123` `Invites` (`issue` `:129`, `redeem` `:140`).

Goals and mandates:
- `engine/session_zero.py:794` `propose_goals()` (routes: history, could_have, own), `:895` `confirm_goal()`, `:974` `history_brief()`, `:1011` `design_agent()`.
- `core/goals.py:247` `check()` (progress, met, undone), `:271` `season_winners()`, `:387` `balance()` (flags goals too strong for a seat's power).
- Mandates: no identifier in `src/` matches "mandate" (case-insensitive grep is empty). Unclear; goals are the closest thing.

Leak scan:
- `core/canary.py:43` `scan()`, `:38` `scan_text()`, `:67` `scan_deliveries()`, `:127` `scan_humans()` (per person across seats).
- `core/report.py:141` `leak_report()`. `engine/host_ops.py:85` `leak_lines()`. `cli.py:467` `leaks`.

Model (LLM) calls:
- `engine/llm.py:113` `ClaudeCli.call()`: the only live transport. Runs `claude -p` with no settings sources and no tools (`:114-135`). Strips `REVISIONIST_*` from the child env (`:138`).
- `engine/llm.py:93` `Transport` protocol, `:55` `Request`, `:73` `Reply`.
- `engine/llm.py:235` `RecordedTransport`: replays `tests/recordings/`.
- `engine/llm.py:376` `ClaudeResolver` (call class `resolver`, `RESOLVER_MODEL` `claude-sonnet-5-5` at `:35`).
- `engine/llm.py:483` `ClaudeNarrator` (call class `narrator`, `NARRATOR_MODEL` `claude-haiku-5-5` at `:36`).
- Other call classes: `session_zero` (`session_zero.py:112`), `character_search` (`:464`), `character_record` (`:533`), `character_pick` (`:665`), `goal` (`:822`), `history_brief` (`:986`), `draft_{stage}` (`draft.py:146`), `pitch` (`pitch.py:193`).
- Offline providers: `engine/providers.py:28` `ScriptedResolver`, `:66` `TemplateNarrator`, `:90` `KeywordSafety`. Used when `offline` is set (`runner.py:75`).
- Cost: `engine/campaign.py:154` `log_cost()`. `core/report.py:47` `cost_report()`.

Store and event log:
- `engine/store.py:33` `EventStore` protocol, `:103` `load_state()`, `:123` `MemoryStore`, `:196` `SqliteStore`.
- `engine/pg_store.py:29` `PostgresEventStore`. `engine/campaign.py:56` `open_store()` chooses between them.
- `engine/maintenance.py:32` `verify_campaign()`, `:72` `copy_campaign()`. `engine/host_ops.py:194` `replay_line()`, `:207` `verify_lines()`.

Sealing:
- `engine/seal.py:84` `KeyringSealer`, `:255` `PgKeyring`, `:248` `new_master_key()`, `:232` `load_master_key()`, `:330` `destroy_key()`.

## 5. Web/JSON API `(web)`
Server: `src/revisionist/engine/web/server.py`. Stdlib `ThreadingHTTPServer` (`:459-468` `serve()`), plain HTTP. Bind to a private address.
- `:64` `App` (DSN, static dir, transport, clock; `secure_cookie` default `False` at `:75`). `:227` `make_handler()`.
- Routes:
  - `GET /api/me` (`:305`): `{"account": null}` if signed out, else `state_for()` (`:108`).
  - Other `GET /api/*` → 404 (`:313`).
  - Any other `GET` → static file, or `index.html` for client routes (`:314`, `:436-454`).
  - `POST /api/signup` (`:329` → `_signup` `:357`): 201 with session cookie and recovery codes.
  - `POST /api/signin` (`:331` → `_signin` `:376`).
  - `POST /api/recover` (`:333` → `_recover` `:399`).
  - `POST /api/signout` (`:335` → `_signout` `:424`).
  - `POST /api/seat/<action>` (`:337-344`): needs a session (`:342`), dispatches via `SEAT_ACTIONS` (`:161-194`).
- Seat actions (`SEAT_ACTIONS` keys, mapped to `WebHandlers`): `join`, `roster`, `search`, `claim`, `pick`, `goal`, `confirm`, `history`, `agent`, `order`, `ask`, `dossier`, `dispatch`, `status`, `report`, `bug`. Replies whose text is in `REFUSALS` (`:211-224`) return `refused: true`.
- Realism seat block, `view.realism` in `GET /api/me` (`realism_snapshot.seat_realism`): `documents`, `desk`, `situation`, `orders`, `clock`. `situation` (`realism_snapshot.situation`) is the Situation tab: each body line with a unit and a number-or-silent value, from arrived pages only, grouped by line and by author, never averaged; a silent latest report is a gap dated by `last_given`.
- Realism actions, `POST /api/realism/<action>` (`App.realism`, `server.py`): `read` (`{doc_id}`; returns the page and logs `document_read` via `realism_tick.mark_read`), `flag` (`{doc_id, flagged}`; `realism_tick.set_flag` logs `document_flagged`, a no-op when the flag already stands, 404 for a document outside the seat's view), `lever`, `step`, `advance`. `advance` passes a prose writer to `run_realism_tick`: `ClaudeProseWriter(transport_factory())`, or `TemplateProseWriter()` when the `App` was built with `offline_prose=True`. Pages the writer drops are logged at warning level and stay bare. Replies are in words ("Sent to Sam Rayburn: ...", "The calendar moves to ..."); the CLI keeps `wake_line`.
- Auth and hardening:
  - Session cookie `rv_session`, HttpOnly, SameSite=Strict (`:45`, `:256-258`). Token hashed at rest (`accounts.py:69`).
  - Writes need a JSON content type and an `Origin` matching `Host` (`:285-292`, called at `:327`).
  - Body cap 64 KiB (`:46`, `:294-298`).
  - Sign-in and recovery throttle: 10 failures per handle per 15 min (`:47`, `:94-104`).
  - CSP header on every response (`:50-54`, `:242`).
- `src/revisionist/engine/web/handlers.py:30` `WebHandlers(Handlers)`: reuses the Discord command code. Overrides `:49` `_game_of()`, `:69` `seat_of()`, `:73` `join()`, `:109` `_welcome()`.
- `src/revisionist/engine/web/accounts.py`: `:44` `hash_password()` (scrypt), `:51` `check_password()`, `:73` `new_recovery_code()`, `:92` `Accounts` (`create` `:96`, `sign_in` `:141`, `recover` `:150`, `codes_left` `:175`, `open_session` `:184`, `session` `:193`, `close_session` `:202`). `:217` `WebSeats` (`link` `:224`, `for_account` `:253`).
- `src/revisionist/engine/web/snapshot.py`: `:165` `seat_snapshot()`, `:291` `checked()` (leak scan before sending; raises `WithheldError` `:39`), `:71` `campaign_info()`, `:92` `schedule_info()`, `:100` `roster()`.
- Errors: `ApiError` (`server.py:57`). `WithheldError` returns `{"error": WITHHELD}` (`:317-319`).
- Known gap: `cli.py` never passes `secure_cookie`, so the cookie never gets `Secure` (`cli.py:1161-1167`).

Frontend (static, no build):
- `web/index.html:12`: loads `/js/app.js` as a module. Mounts `#app` (`:15`). CSS: `tokens.css`, `fonts.css`, `app.css` (`:9-11`).
- `web/js/app.js:11`: route from `location.hash`, default `signin`. `:14-18` `go()`. `:53-84` `render()` picks the view. `:88-93`: re-render every 60 s unless typing or a dialog is open.
- `web/js/api.js:10` `call()`. `:32-40` `api` object: `me` (GET `/api/me`), `signup`, `signin`, `recover`, `signout`, and `seat(action)` → `/api/seat/<action>`.
- Views:
  - `web/js/views/auth.js`: `signIn` (`:45`), `signUp` (`:67`), `recoveryCodes` (`:91`), `recover` (`:119`), `join` (`:143`, calls `seat('join')`).
  - `web/js/views/session-zero.js`: `sessionZero` (`:212`), `currentStep` (`:20`). Steps at `:11-17`. Calls `seat` for search, claim, pick, goal, confirm, history, agent.
  - `web/js/views/war-room.js`: `warRoom` (`:263`). Tabs at `:271`: briefing, dossier, map, ledger. Calls `seat('order')` (`:195`) and `seat('ask')` (`:235`).
  - `web/js/views/frame.js`: `header` (`:6`). Bug report dialog calls `seat('bug')` (`:42`).
- Pure rules, unit-tested: `web/js/rules.js` (`band` `:14`, `visibility` `:28`, `disposition` `:39`, `parseReply` `:54`, `untilText` `:80`, `worldDate` `:91`). DOM helpers in `web/js/dom.js` (`h` `:9`, `reply` `:92`, `prose` `:104`).
- `web/package.json:8` `npm test` (`node --test test/*.test.js`). `:9` `npm run lint` (`eslint js test`).

## 6. Data
- SQLite (default):
  - `src/revisionist/engine/campaign.py:41-44` `db_target()`: `REVISIONIST_DB`, default `revisionist.db` in the working directory. A `revisionist.db` sits at the main repo root.
  - `engine/store.py:196` `SqliteStore`: `PRAGMA journal_mode=WAL` and `foreign_keys=ON` (`:202-203`). Schema from inline `_SCHEMA` via `executescript` (`:204`).
  - Tables: `events` (`:174`), `snapshots` (`:187`). Triggers make `events` append-only (`:184-186`).
  - SQLite schema is not in migration files.
- Postgres:
  - `campaign.py:47-48` `is_postgres()`. `engine/pg_store.py:29` `PostgresEventStore` (psycopg).
  - `pg_store.py:24` `MIGRATIONS` = `engine/migrations/`. `:109` `migration_files()` (sorted `NNNN_*.sql`).
  - `pg_store.py:113` `migrate()`: advisory lock (`:122`), `schema_migrations` table (`:124-128`), each file in its own transaction with a version row (`:133-139`), `{app_role}` substituted (`:136`).
  - Run by `revisionist db migrate` (`cli.py:684-697`) with `REVISIONIST_ADMIN_DSN` (`:691`). App role defaults to `revisionist_app` (`:687`).
- Migrations (main):
  - `0001_init.sql`: `events` (`:6`), `snapshots` (`:16`).
  - `0002_schedule.sql`: `schedule` (`:4`).
  - `0003_discord.sql`: `discord_seats` (`:4`), `discord_deliveries` (`:15`), `discord_bugs` (`:24`).
  - `0004_rename.sql`: renames the append-only trigger function `wyrd_append_only` to `revisionist_append_only`.
  - `0005_discord_invites.sql`: `discord_invites` (`:5`).
  - `0006_discord_channels.sql`: `discord_channels` (`:4`).
  - `0007_seal_keys.sql`: `seal_keys` (`:6`).
  - `0008_retire.sql`: adds `schedule.retired_at` (retired campaigns never tick again).
- `(web)` `0009_web.sql`: `web_accounts` (`:6`), `web_recovery_codes` (`:16`), `web_sessions` (`:23`), `web_seats` (`:32`). Grants `{app_role}` (`:41-42`).
- Event log: `core/events.py` (event models). `core/fold.py:82` `apply()`, `:94` `replay()`. `engine/store.py:103` `load_state()`. The Postgres store reads from a snapshot plus the tail (`pg_store.py:100`).
- Schema export: `schema_export.py:31` writes `schema/revisionist.schema.json`, which is committed. `tests/test_schema.py:85` checks it is current. `core/__init__.py:23` `SCHEMA_VERSION = "10"`.
- Worlds: `worlds/salt-strait.yaml`, `worlds/salt-strait-duo.yaml`. Resolved from `campaign.py:33` `WORLDS_DIR` by `world_path()` (`:85-93`). Drafts `tests/drafts/florence-1478.yaml` and `washington-1962.yaml` are fixtures.
- Briefings: `campaign.py:226-235` `briefings_dir()`: `REVISIONIST_BRIEFINGS_DIR`, else `<db>-briefings` beside a SQLite file, else `./revisionist-briefings`. Files `t<tick>-<seat>.md` from `FileDeliverer` (`:208-223`).
- Setting card waiting for `establish confirm`: `cli.py:164-167` `_card_file()`, `revisionist-card.json` beside briefings.
- Backups: `deploy/backup/revisionist-backup.sh` runs `pg_dump -Fc` into `REVISIONIST_BACKUP_DIR` (`:14`, `:21`). Prunes by age (`:26`). Copies off-box (`:28-31`). Restore drill: section 1.

## 7. Tests
- Run (CI order, `.github/workflows/ci.yml`):
  - `uv sync --frozen` (`:36`), `uv run --frozen ruff check .` (`:37`), `ruff format --check .` (`:38`), `uv run --frozen mypy` (`:39`), `uv run --frozen pytest --cov=revisionist.core --cov-report=term-missing` (`:40`).
  - Config: `pyproject.toml:43-49` ruff, `:51-55` mypy (strict, `src` and `tests`), `:57-59` pytest (`testpaths=tests`, `-q`).
- Postgres tests:
  - `tests/pg.py:23` reads `REVISIONIST_TEST_PG_ADMIN_DSN`. Without it the tests skip, unless `REVISIONIST_REQUIRE_PG=1` makes them fail (`pg.py:34-39`).
  - CI runs a Postgres 16 service (`ci.yml:12-23`) and sets `REQUIRE_PG=1` (`:28`).
  - Fixtures: `tests/conftest.py:13-22` (`pg_cluster`, session scope; `pg_db`, per test). `pg.py:65` `Cluster` builds a template database.
- Autouse fixtures:
  - `conftest.py:23-29` strips `REVISIONIST_*` from the environment for each test.
  - `conftest.py:31-` blocks Discord network calls.
- Model replay: `tests/recordings/two-rivers-agent.json` and `two-rivers-tick-1.json`, read by `RecordedTransport`. Refresh with `REVISIONIST_RECORD=1 uv run pytest tests/test_llm.py -k live` (`test_llm.py:254`) or `test_agent.py:326`.
- Realism model replay: `tests/recordings/realism-*.json`, hand-written (each entry is marked `hand_written`; no model wrote them). Regenerate after a prompt change with `uv run --frozen python -m tests.handwritten`; scenarios in `tests/prose_scenario.py` and `tests/counsel_scenario.py`.
- Helpers: `tests/fixtures.py`, `tests/discord_fakes.py` (`FakeDiscord` `:20`), `tests/pg.py`.
- Browser and Docker tests:
  - Main: none found (no Playwright or Docker files).
  - `(web)` `web/test/e2e/smoke.py`: Playwright against a live `revisionist web serve` (`:7` usage, `:16` import). Manual; not in CI.
  - `(web)` `web/` JS unit tests (`npm test`, `npm run lint`): not in CI.
- Coverage by area (main; `(web)` adds `test_web.py`):
  - Core rules: `test_fold`, `test_referee`, `test_referee_d1`, `test_referee_contest`, `test_contest`, `test_goals`, `test_goal_rule`, `test_goal_offer_cap`, `test_goal_labels`, `test_intel`, `test_perceived_odds`, `test_project`, `test_places`, `test_roster`, `test_calendar`, `test_history`, `test_tick_history`, `test_dossier_and_world_state`, `test_order_results`, `test_report`, `test_schema`, `test_purity` (name only, not opened).
  - Tick and engine: `test_tick`, `test_season`, `test_scheduler`, `test_one_game`, `test_world_compile`, `test_draft`, `test_pitch`, `test_briefing_orders`, `test_advise`, `test_agent`, `test_deputy_history`, `test_prompt_diet`, `test_model_override`, `test_llm`, `test_character_search`, `test_character_names`.
  - Session Zero: `test_session_zero`, `test_session_zero_steps`, `test_session_zero_confirm`, `test_session_zero_aliases`, `test_session_zero_v3_contract`.
  - Storage, sealing, verify: `test_store`, `test_db`, `test_seal`, `test_seal_pg`, `test_host_ops`, `test_human_boundary`, `test_verify_record_cli`, `test_verify_record_search`, `test_verify_why_cli`, `test_verify_why_discord`, `test_verify_host_link`, `test_verify_full_season`.
  - CLI: `test_cli`, `test_cli_establish`, `test_env_isolation` (runs the CLI as a subprocess), `test_deploy` (runs `deploy/backup` scripts).
  - Discord: `test_discord_bot`, `test_discord_format`, `test_discord_delivery`, `test_discord_duo`, `test_discord_host`, `test_discord_public`, `test_discord_establish`, `test_discord_game_matrix`, `test_discord_report_after_season`, `test_channel_carryover`, `test_durable_replies`.
  - Other: `test_drift_report` (not opened).
  - `(web)` `tests/test_web.py`: fixture `web` at `:128-129`, helper class `Web` at `:88`. Covers password hashing and recovery codes (`:145`, `:191`), cookie flags (`:157`), signup and signin rules (`:162`, `:171`), throttle (`:182`), same-origin writes (`:211`), seat auth (`:222`), static containment (`:227`), CSP (`:238`), Session Zero to play (`:247`), seat held by one client (`:290`), leak scan counting web players (`:320`), retire releasing web seats (`:339`), CLI refusing web-held seats (`:350`), snapshot (`:362`), canary withholding (`:378`).
- Stale bytecode: `tests/__pycache__/test_probe_*.pyc` exist in main with no source file.

## 8. Config and env vars
Environment variables (all read in code unless noted):
- `REVISIONIST_DB`: `src/revisionist/engine/campaign.py:44`. SQLite path or `postgresql://` URL. Default `revisionist.db`.
- `REVISIONIST_BRIEFINGS_DIR`: `campaign.py:229`.
- `REVISIONIST_MASTER_KEY_FILE`: `engine/seal.py:213` (`MASTER_KEY_ENV`), `:224`; also `campaign.py:72`. Enables sealing.
- `CREDENTIALS_DIRECTORY` (systemd credentials): `engine/seal.py:226`, `campaign.py:73`.
- `REVISIONIST_ADMIN_DSN`: `cli.py:691` (`db migrate`), `cli.py:787` (`admin erase`), `deploy/backup/revisionist-backup.sh:12`, `deploy/backup/revisionist-restore-drill.sh:11`.
- `REVISIONIST_APP_ROLE`: `cli.py:687` (Typer `envvar`; default `revisionist_app`).
- `REVISIONIST_DISCORD_TOKEN`: `cli.py:1101` (`discord run`), `engine/discord/delivery.py:35` (`TOKEN_ENV`), `:129`.
- `REVISIONIST_DISCORD_GUILD_ID`: `cli.py:988` (`link`), `:1076` (`channel`), `:1102` (`run`).
- `REVISIONIST_DISCORD_HOST_ID`: `cli.py:118` (`_guard`), `:493` (`_holdings`), `:1116` (`discord run`).
- `REVISIONIST_MODEL_<CALL>`: `engine/llm.py:203-204`. Overrides the model per call class; must start with `claude-` (`:207`). Examples in `tests/test_model_override.py:39-53`. Realism call classes: `PROSE` (default `claude-sonnet-5-5` [proposed], pending the A/B), `COUNSEL` (`claude-sonnet-5-5` [proposed]), `AIDE` (`claude-haiku-5-5` [proposed]).
- `REVISIONIST_RECORD`: `engine/llm.py:245`. `1` records live calls into a recording file.
- Backups:
  - `REVISIONIST_BACKUP_DEST` (`revisionist-backup.sh:13`, `:28`): rsync target or `rclone:<remote>:<path>`.
  - `REVISIONIST_BACKUP_DIR` (`:14`; also `restore-drill.sh:12`): default `~/.local/share/revisionist/backups`.
  - `REVISIONIST_BACKUP_KEEP_DAYS` (`:15`): default 14.
  - `REVISIONIST_BIN` (`restore-drill.sh:13`): the `revisionist` command.
- Tests:
  - `REVISIONIST_TEST_PG_ADMIN_DSN` (`tests/pg.py:23`; `conftest.py:10`; `ci.yml:26`).
  - `REVISIONIST_REQUIRE_PG` (`pg.py:38`; `ci.yml:28`; `test_deploy.py:104`).
- Child-process filter: `engine/llm.py:138` removes `REVISIONIST_*` from the environment passed to `claude`.
- Set by systemd, not read in code: `PATH` (`deploy/systemd/revisionist-discord.service:13`, `revisionist-scheduler.service:12`), `WorkingDirectory` (`:14`, `:13`), `EnvironmentFile` (`revisionist.env` in `discord.service:11` and `scheduler.service:10`; `revisionist-admin.env` in `backup.service:9`).

Config keys (not env):
- `core/settings.py` `Settings`, set by `establish confirm` options (`cli.py:205-210`): `plausibility` (default PLAUSIBLE), `inertia` (TENDENCIES), `lethality` (HARD), `betrayal` (CUTTHROAT), `tone` ("grounded"), `tick_hours` (24, range 1–168).
- World YAML: `worlds/*.yaml`, parsed by `WorldFile` (`campaign.py:96-97`).
- Discord `BotConfig` (`engine/discord/handlers.py:135`).

## Unclear / not verified
- "Mandate" generation: no identifier in `src/`. Goals are the closest match (section 4).
- Web branch's Discord and engine files were not compared line by line with main. Only `cli.py` (by decorator line numbers) and the web-only files were checked.
- `revisionist.env` and `revisionist-admin.env` contents are not in the repo; `docs/deploy.md` is the only reference.
- `(web)` `docs/deploy.md:283-285` still has `wyrd-*` unit names in a teardown section.
- `(web)` `secure_cookie` is never set by the CLI (see section 5).