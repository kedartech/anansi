"""Layer 10 item 1d: the character, goal and brief steps of Session Zero (spec §5).

Every model call is scripted by ``Table``, which reads the IDs it needs out of
the view in the prompt, as a model would. Nothing here is a model output.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from revisionist import cli
from revisionist.core import events as ev
from revisionist.core import goals as g
from revisionist.core.canary import surfaces_from_log
from revisionist.core.fold import replay
from revisionist.core.ids import CampaignId, EventId, SeatId, derived_id
from revisionist.core.ontology import EntityStatus, Persona, Phase
from revisionist.core.project import project
from revisionist.core.sealing import seal_holder
from revisionist.core.settings import Settings
from revisionist.core.state import State
from revisionist.engine import session_zero as sz
from revisionist.engine.discord.handlers import DRAFTING, HOST_ONLY, JOINED_ZERO, PICK_FIRST
from revisionist.engine.llm import Reply, Request
from revisionist.engine.pg_store import PostgresEventStore
from revisionist.engine.providers import RecordingDeliverer, ScriptedResolver, TemplateNarrator
from revisionist.engine.store import EventStore, MemoryStore, SqliteStore, StoreError, load_state
from revisionist.engine.tick import Providers, run_tick
from tests import fixtures as fx
from tests.pg import PgDb
from tests.test_discord_bot import GUILD, HOST, Game
from tests.test_discord_duo import FRIEND
from tests.test_session_zero import FLORENCE, draft

runner = CliRunner()

A = SeatId("01ARZ3NDEKTSV4RRFFQ69PA001")
B = SeatId("01ARZ3NDEKTSV4RRFFQ69PB001")
C = SeatId("01ARZ3NDEKTSV4RRFFQ69PC001")


def _section(prompt: str, title: str) -> Any:
    body = prompt.split(f"## {title}\n", 1)[1].split("\n\n## ", 1)[0]
    return json.loads(body)


class Table:
    """The GM and every player's agent, scripted. Goal texts carry a marker per seat."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def call(self, request: Request) -> Reply:
        self.calls.append(request.call_class)
        out: dict[str, Any]
        match request.call_class:
            case "session_zero":
                out = draft("florence-1478.yaml")
            case "character_search":
                roster = _section(request.prompt, "Roster")
                banks = [c["id"] for c in roster if "bank" in c["pitch"].lower()]
                out = {"matches": [*banks, "NOT-A-ROSTER-ID"]}
            case "character_record":
                out = {"people": []}
            case "character_pick":
                if "Luca Pitti" in request.prompt:
                    out = {
                        "existed": True,
                        "placed": True,
                        "note": "A rival of the Medici, still in Florence.",
                        "name": "Luca Pitti",
                        "pitch": "An old rival who built a palace to outshine them.",
                        "reach": "Old Florentine money.",
                        "composite": False,
                        "means": [
                            {"name": "Pitti fortune", "kind": "wealth", "tier": 5},
                            {"name": "Pitti retainers", "kind": "force", "tier": 1},
                            {"name": "broken", "kind": "not-a-kind", "tier": 1},
                        ],
                    }
                else:
                    out = {"existed": False, "placed": False, "note": "No such person."}
            case "goal":
                out = self._goals(request.prompt)
            case "history_brief":
                out = {
                    "record": "You came to lead the city young.",
                    "pursuit": "History saw you keep it, at a price.",
                    "route": "Win the Signoria before the Pazzi move.",
                    "thin": None,
                }
            case _:
                raise AssertionError(request.call_class)
        return Reply(out, request.model, 10, 10, 0, 0.01)

    def _goals(self, prompt: str) -> dict[str, Any]:
        view = _section(prompt, "What your character knows")
        me = view["me"]["embodies"]
        mark = f"MARK-{view['viewer'][-4:]}"
        by_name = {a["name"]: k for k, a in view["assets"].items()}
        pazzi = next(k for k, e in view["entities"].items() if e["name"] == "The Pazzi")
        office = {
            "check": "holds_office",
            "holder": me,
            "office": by_name["Gonfalonier of Justice"],
        }
        if "be remembered" in prompt:
            return {"options": [], "rewording": "Hold the Gonfalonier of Justice at season end."}
        if "did not see" in prompt:  # the could_have route
            guard = {"check": "controls_asset", "holder": me, "asset": by_name["Palace guard"]}
            both = {"check": "all_of", "conditions": [office, guard]}
            return {"options": [{"text": f"Take the bank and the bell. {mark}", "condition": both}]}
        return {
            "options": [
                {"text": f"Hold the Gonfalonier of Justice. {mark}", "condition": office},
                {
                    "text": f"Outlast the Pazzi. {mark}",
                    "condition": {"check": "entity_is", "entity": pazzi, "status": "dissolved"},
                },
                {
                    "text": "Already done",
                    "condition": {"check": "entity_is", "entity": me, "status": "active"},
                },
            ]
        }


def _florence(store: EventStore, table: Table) -> CampaignId:
    return sz.establish(store, FLORENCE, Settings(), table)


PERSONA = Persona(name="Ser Agnolo", role="chancellor", voice="dry")


# --- the whole of Session Zero ------------------------------------------------------


def test_two_players_go_from_establish_to_play() -> None:
    store, table = MemoryStore(), Table()
    c = _florence(store, table)

    assert sz.claim(store, c, A, "lorenzo de' medici") == "Lorenzo de' Medici"
    with pytest.raises(sz.StepRefusedError, match="already taken"):
        sz.claim(store, c, B, "Lorenzo de' Medici")
    with pytest.raises(sz.StepRefusedError, match="You already play"):
        sz.claim(store, c, A, "Tommaso Vanni")
    with pytest.raises(sz.StepRefusedError, match="No one called 'Cosimo'"):
        sz.claim(store, c, B, "Cosimo")
    assert sz.claim(store, c, B, "Tommaso Vanni") == "Tommaso Vanni"
    state = load_state(store, c)
    assert state.seats[A].controller.value == "human" and state.seats[A].kind.value == "player"
    assert state.roster[state.seats[A].embodies].claimed_by == A
    assert any("[taken]" in line for line in sz.roster_lines(state))

    lines = sz.propose_goals(store, c, A, "history", table)
    assert [x.split(" [")[1] for x in lines] == [
        "Easy, lasting]",
        "Hard, undoable]",
    ]  # "already done" is dropped; sorted easy to hard; each line reads "[rank, kind]"
    with pytest.raises(sz.StepRefusedError, match="Choose one"):
        sz.confirm_goal(store, c, A, 7)
    assert "(undoable). It must still hold at season end." in sz.confirm_goal(store, c, A, 2)
    with pytest.raises(sz.StepRefusedError, match="fixed for the season"):
        sz.propose_goals(store, c, A, "history", table)

    with pytest.raises(sz.StepRefusedError, match="Try: Hold the Gonfalonier"):
        sz.propose_goals(store, c, B, "own", table, "be remembered as great")
    flagged = sz.propose_goals(store, c, B, "could_have", table)
    assert "Balance: A weak character with a hard goal" in flagged[0]
    assert "host has to approve" in sz.confirm_goal(store, c, B, 1)

    with pytest.raises(sz.StepRefusedError, match="Still to do: Lorenzo de' Medici: agent"):
        sz.begin(store, c)
    brief = sz.history_brief(store, c, A, table)
    assert brief.startswith("As recorded: ")
    assert f"{sz.SUGGESTED} Win the Signoria" in brief
    for seat in (A, B):
        sz.design_agent(store, c, seat, PERSONA)
    with pytest.raises(sz.StepRefusedError, match="already designed"):
        sz.design_agent(store, c, A, PERSONA)

    with pytest.raises(sz.StepRefusedError, match="1 goal\\(s\\) wait"):
        sz.begin(store, c)
    [(seat, balance)] = sz.flagged(store, c)
    assert seat == B and balance.flag == "too_hard"
    with pytest.raises(sz.StepRefusedError, match="Nothing of that seat"):
        sz.approve(store, c, A)
    sz.approve(store, c, B)
    assert sz.begin(store, c).startswith("Session Zero is closed. 2 player(s)")
    state = load_state(store, c)
    assert state.phase is Phase.PLAY
    with pytest.raises(sz.StepRefusedError, match="not open"):
        sz.claim(store, c, C, "Giuliano de' Medici")

    providers = Providers(ScriptedResolver(), TemplateNarrator(), RecordingDeliverer())
    assert run_tick(store, c, providers).tick == 0  # play begins

    # The steps are private: neither player can read the other's goal or brief.
    events = list(store.read(c))
    holdings = {"a": frozenset({A}), "b": frozenset({B})}
    seen = surfaces_from_log(events, holdings)
    a_text, b_text = ("\n".join(t for _, t in seen[h]) for h in ("a", "b"))
    assert f"MARK-{A[-4:]}" not in b_text and f"MARK-{B[-4:]}" not in a_text
    assert "As recorded" in a_text and "As recorded" not in b_text
    for e in events:
        if isinstance(e.payload, ev.GoalProposed):
            assert e.scope.known_by == {e.payload.seat_id}
            assert seal_holder(e) == e.payload.seat_id  # the player's own words
        if isinstance(e.payload, ev.GoalSet):
            assert e.scope.known_by == {e.payload.goal.seat_id}
            assert seal_holder(e) is None  # the fold needs it
    assert table.calls.count("goal") == 3  # a fixed goal is refused before any call


def test_steps_wait_for_a_character() -> None:
    store, table = MemoryStore(), Table()
    c = _florence(store, table)
    for step in (
        lambda: sz.propose_goals(store, c, A, "history", table),
        lambda: sz.confirm_goal(store, c, A, 1),
        lambda: sz.history_brief(store, c, A, table),
        lambda: sz.design_agent(store, c, A, PERSONA),
    ):
        with pytest.raises(sz.StepRefusedError, match="Pick a character first"):
            step()
    with pytest.raises(sz.StepRefusedError, match="No one has claimed"):
        sz.begin(store, c)
    sz.claim(store, c, A, "Giuliano de' Medici")
    with pytest.raises(sz.StepRefusedError, match="Fix your goal first"):
        sz.history_brief(store, c, A, table)
    with pytest.raises(sz.StepRefusedError, match="Write the goal"):
        sz.propose_goals(store, c, A, "own", table, "  ")
    with pytest.raises(sz.StepRefusedError, match="name and a role"):
        sz.design_agent(store, c, A, Persona(name=" ", role="aide"))


def test_search_names_only_free_roster_characters() -> None:
    store, table = MemoryStore(), Table()
    c = _florence(store, table)
    found = sz.search(store, c, None, "someone who moves money", table)
    bankers = {"Lorenzo de' Medici", "Francesco de' Pazzi", "Tommaso Vanni (composite)"}
    assert {x.split(":")[0] for x in found} == bankers  # the stray ID is dropped
    sz.claim(store, c, A, "Lorenzo de' Medici")
    left = {x.split(":")[0] for x in sz.search(store, c, A, "money", table)}
    assert left == bankers - {"Lorenzo de' Medici"}


def test_an_own_pick_is_checked_against_the_record_and_capped() -> None:
    store, table = MemoryStore(), Table()
    c = _florence(store, table)
    with pytest.raises(sz.StepRefusedError, match="does not place Nobody Real"):
        sz.pick(store, c, A, "Nobody Real", table)
    assert table.calls[-1] == "character_pick"
    with pytest.raises(sz.StepRefusedError, match="already in the world but not on the roster"):
        sz.pick(store, c, A, "Federico da Montefeltro", table)
    assert sz.pick(store, c, B, "Luca Pitti", table) == "Luca Pitti"
    state = load_state(store, c)
    luca = state.seats[B].embodies
    means = sorted((a.name, a.tier) for a in state.assets.values() if a.controller == luca)
    assert means == [("Pitti fortune", sz.PICK_TIER_MAX), ("Pitti retainers", 1)]
    assert state.roster[luca].claimed_by == B and not state.roster[luca].composite
    assert sz.pick(store, c, C, "giuliano de' medici", table) == "Giuliano de' Medici"  # roster


def test_a_goal_must_name_what_you_know_and_be_possible() -> None:
    state = replay_world()
    south = project(state, fx.SOUTH)
    hidden = g.ControlsAsset(holder=fx.E_SOUTH, asset=fx.A_NORTH_SECRET_ARMY)
    assert fx.A_NORTH_SECRET_ARMY not in south.assets
    assert any("not something you know of" in p for p in g.vet(south, state, hidden))
    view = project(state, fx.NORTH)
    assert g.vet(view, state, g.ControlsAsset(holder=fx.E_NORTH, asset=fx.A_NORTH_ARMY)) == [
        "already met: a goal has to be worked for"
    ]
    dead = state.model_copy(
        update={
            "entities": {
                **state.entities,
                fx.E_SOUTH_AIDE: state.entities[fx.E_SOUTH_AIDE].model_copy(
                    update={"status": EntityStatus.DEAD}
                ),
            }
        }
    )
    revive = g.EntityIs(entity=fx.E_SOUTH_AIDE, status=EntityStatus.ACTIVE)
    assert g.vet(project(dead, fx.NORTH), dead, revive) == [
        "entity_is: " + fx.E_SOUTH_AIDE + " is already dead"
    ]
    office = g.HoldsOffice(holder=fx.E_NORTH, office=fx.A_SOUTH_GUARD)
    assert g.vet(view, state, office) == [f"holds_office: {fx.A_SOUTH_GUARD} is not an office"]


def replay_world() -> State:
    return replay(fx.creation_events())


def test_the_balance_check_flags_both_outliers() -> None:
    state = replay_world()
    take = g.ControlsAsset(holder=fx.E_NORTH, asset=fx.A_SOUTH_GUARD)
    assert g.balance(state, fx.NORTH, take).flag is None
    rich = state.model_copy(
        update={
            "assets": {
                k: a.model_copy(update={"tier": 5}) if a.controller == fx.E_NORTH else a
                for k, a in state.assets.items()
            }
        }
    )
    easy = g.MeterAt(meter=fx.M_NORTH_STABILITY, op="ge", value=65)
    assert g.balance(rich, fx.NORTH, easy).flag == "too_easy"
    poor = state.model_copy(
        update={"assets": {k: a for k, a in state.assets.items() if a.controller != fx.E_NORTH}}
    )
    hard = g.AllOf(conditions=(take, g.ControlsAsset(holder=fx.E_NORTH, asset=fx.A_SOUTH_TREASURY)))
    scored = g.balance(poor, fx.NORTH, hard)
    assert scored.flag == "too_hard" and scored.power <= g.WEAK and scored.difficulty >= g.HARD
    either = g.AnyOf(conditions=(take, easy))
    assert g.difficulty(state, fx.NORTH, either) == min(
        g.difficulty(state, fx.NORTH, take), g.difficulty(state, fx.NORTH, easy)
    )


# --- one character, one player: at the store -------------------------------------


@pytest.fixture(params=["memory", "sqlite", "postgres"])
def any_store(request: pytest.FixtureRequest, tmp_path: Path) -> Any:
    if request.param == "memory":
        yield MemoryStore()
    elif request.param == "sqlite":
        s = SqliteStore(tmp_path / "c.db")
        yield s
        s.close()
    else:
        db: PgDb = request.getfixturevalue("pg_db")
        p = PostgresEventStore(db.app_dsn)
        yield p
        p.close()


def test_a_second_claim_of_one_character_fails_at_the_store(any_store: EventStore) -> None:
    table = Table()
    c = _florence(any_store, table)
    sz.claim(any_store, c, A, "Lorenzo de' Medici")
    state = load_state(any_store, c)
    first = next(e for e in any_store.read(c) if isinstance(e.payload, ev.CharacterClaimed))
    assert first.event_id == EventId(derived_id(c, "claim", state.seats[A].embodies))
    racer = first.model_copy(
        update={
            "seq": state.last_seq + 1,
            "payload": ev.CharacterClaimed(seat_id=B, entity_id=state.seats[A].embodies),
        }
    )
    with pytest.raises(StoreError):
        any_store.append(racer)  # what a second writer with a stale view would try


# --- surfaces ----------------------------------------------------------------------


def test_the_cli_runs_session_zero_to_the_first_tick(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("REVISIONIST_DB", str(tmp_path / "game.db"))
    monkeypatch.setenv("REVISIONIST_BRIEFINGS_DIR", str(tmp_path / "briefings"))
    table = Table()
    monkeypatch.setattr(cli, "transport_factory", lambda: table)

    def ok(*args: str) -> str:
        result = runner.invoke(cli.app, list(args))
        assert result.exit_code == 0, result.output
        return result.output

    ok("establish", FLORENCE)
    assert "Tommaso Vanni (composite)" in ok("character", "roster")
    assert "Lorenzo de' Medici" in ok("character", "search", "money")
    assert ok("character", "claim", "Lorenzo de' Medici").strip() == "you play Lorenzo de' Medici"
    again = runner.invoke(cli.app, ["character", "claim", "Giuliano de' Medici"])
    assert again.exit_code == cli.EXIT_REFUSED and "pass --new" in again.output
    assert "H2. Hold the Gonfalonier" in ok("goal", "suggest", "history")
    assert "Your goal is fixed" in ok("goal", "confirm", "2")
    assert sz.SUGGESTED in ok("history")
    ok("agent", "--name", "Ser Agnolo", "--role", "chancellor")
    assert "no goals wait" in ok("goal", "flags")
    assert "Session Zero is closed" in ok("begin")
    assert "tick 0 resolved" in ok("tick", "--offline")
    bad = runner.invoke(cli.app, ["goal", "suggest", "sideways"])
    assert bad.exit_code == cli.EXIT_ERROR


def test_discord_session_zero_for_two(
    pg_db: PgDb, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    table = Table()
    game = Game(pg_db, table, tmp_path / "briefings")
    monkeypatch.setenv("REVISIONIST_DB", pg_db.app_dsn)
    monkeypatch.setenv("REVISIONIST_DISCORD_GUILD_ID", GUILD)
    monkeypatch.setattr(cli, "clock", game.clock)
    opened = game.command("establish", HOST, place=FLORENCE)
    assert opened.startswith(DRAFTING.format(place=FLORENCE) + "Session Zero is open")

    def invite() -> str:
        reply = game.command("invite", HOST)
        assert "a new player" in reply, reply
        return reply.splitlines()[1].strip()

    for user in (HOST, FRIEND):
        assert game.command("join", user, code=invite()).startswith(JOINED_ZERO)
    assert game.command("order", FRIEND, text="Raise the city.") == PICK_FIRST
    assert "Pick a character first" in game.command("history", FRIEND)
    assert "Lorenzo de' Medici" in game.command("character", FRIEND, action="roster")
    assert game.command("character", HOST, action="claim", text="Lorenzo de' Medici") == (
        "You play Lorenzo de' Medici."
    )
    taken = game.command("character", FRIEND, action="claim", text="Lorenzo de' Medici")
    assert taken == "Lorenzo de' Medici is already taken."
    game.command("character", FRIEND, action="claim", text="Giuliano de' Medici")
    for user in (HOST, FRIEND):
        offered = game.command("goal", user, action="history")
        assert "- **H2** Hold the Gonfalonier" in offered
        assert "Your goal is fixed" in game.command("goal", user, action="confirm", text="2")
        assert sz.SUGGESTED in game.command("history", user)
        # Game.command's own first parameter is called name, so the agent step goes
        # straight to its handler; the bot registers it like every other command.
        assert "now serves" in game.handlers.agent(user, PERSONA)
    assert game.command("begin", FRIEND) == HOST_ONLY
    assert game.command("begin", HOST).startswith("Session Zero is closed. 2 player(s)")
    assert "Giuliano" in game.command("status", FRIEND)


class Strict(Table):
    """A search model that finds nobody: the playtest's 'assassin' and 'poet' failures."""

    def call(self, request: Request) -> Reply:
        if request.call_class == "character_search":
            self.calls.append(request.call_class)
            return Reply({"matches": []}, request.model, 10, 10, 0, 0.01)
        return super().call(request)


def test_an_empty_search_is_refused_before_any_model_call() -> None:
    store, table = MemoryStore(), Table()
    c = _florence(store, table)
    before = len(table.calls)
    with pytest.raises(sz.StepRefusedError, match="Say what you want to play"):
        sz.search(store, c, None, "   ", table)
    assert len(table.calls) == before


def test_a_search_the_model_finds_nothing_for_retries_then_falls_back_to_words() -> None:
    store, table = MemoryStore(), Strict()
    c = _florence(store, table)
    before = len(table.calls)
    found = sz.search(store, c, None, "a banker", table)
    assert table.calls[before:] == ["character_search", "character_search", "character_record"]
    names = {x.split(":")[0] for x in found}
    assert "Lorenzo de' Medici" in names and len(found) <= 5
    assert sz.search(store, c, None, "zzzz qqqq", table) == []
