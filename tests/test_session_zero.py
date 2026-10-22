"""Layer 10 item 1c: ``/establish`` and the world compiler (spec §5 step 2).

The GM's draft is scripted here from two hand-written stand-ins, one ancient
and one from the 20th century; they are not model outputs and are re-recorded
live. Nothing is written to the log until a draft compiles.
"""

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from revisionist import cli
from revisionist.core import events as ev
from revisionist.core.fold import FoldError, replay
from revisionist.core.ontology import Phase
from revisionist.core.settings import Lethality, Plausibility, Settings
from revisionist.engine import scheduler as sch
from revisionist.engine import session_zero as sz
from revisionist.engine.discord.handlers import DRAFT_REFUSED, DRAFTING, HOST_ONLY
from revisionist.engine.llm import Reply, Request
from revisionist.engine.pg_store import PostgresEventStore
from revisionist.engine.providers import RecordingDeliverer, ScriptedResolver, TemplateNarrator
from revisionist.engine.store import MemoryStore, load_state
from revisionist.engine.tick import NotInPlayError, Providers, run_tick
from tests.pg import PgDb
from tests.studio import Drafts
from tests.test_discord_bot import HOST, STRANGER, Game
from tests.test_scheduler import Clock

DRAFTS = Path(__file__).parent / "drafts"
FLORENCE = "Florence, April 1478"
WASHINGTON = "Washington, October 1962"


def draft(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load((DRAFTS / name).read_text())
    return loaded


class GM:
    """Replies to ``session_zero`` calls with the given drafts, in order, and to the
    v3 ``pitch`` and ``draft_*`` calls from the same drafts (``tests.studio``)."""

    def __init__(self, *replies: dict[str, Any]) -> None:
        self.replies = list(replies)
        self.requests: list[Request] = []
        self.drafts = Drafts(*replies)
        """The v3 path (pitch, then the staged draft) answers from the same drafts."""

    def call(self, request: Request) -> Reply:
        self.requests.append(request)
        staged = self.drafts.answer(request.call_class)
        if staged is not None:
            return Reply(staged, request.model, 1500, 2000, 0, 0.04)
        assert request.call_class == "session_zero"
        return Reply(self.replies.pop(0), request.model, 4000, 6000, 0, 0.12)


@pytest.mark.parametrize(
    ("place", "name", "people"),
    [(FLORENCE, "florence-1478.yaml", 11), (WASHINGTON, "washington-1962.yaml", 10)],
)
def test_a_draft_that_compiles_opens_session_zero(place: str, name: str, people: int) -> None:
    store = MemoryStore()
    gm = GM(draft(name))
    settings = Settings(plausibility=Plausibility.STRICT, tone="grim")
    campaign = sz.establish(store, f"  {place} ", settings, gm)
    state = load_state(store, campaign)
    assert state.phase is Phase.SESSION_ZERO and state.tick == 0
    assert len(state.roster) == people
    assert any(c.composite for c in state.roster.values())
    assert all(state.entities[c].kind.value == "person" for c in state.roster)
    assert not any(s.controller.value == "human" for s in state.seats.values())
    assert state.goals == {}
    events = list(store.read(campaign))
    opened = [e.payload for e in events if isinstance(e.payload, ev.SessionZeroOpened)]
    assert opened == [ev.SessionZeroOpened(start=place, notes=tuple(draft(name)["notes"]))]
    costs = [e.payload for e in events if isinstance(e.payload, ev.CostLogged)]
    assert [c.purpose for c in costs] == ["session_zero"]
    assert gm.requests[0].model == sz.SESSION_ZERO_MODEL
    assert place in gm.requests[0].prompt
    assert "Settings (fixed by the table" in gm.requests[0].prompt


def test_the_tables_settings_win_over_the_drafts() -> None:
    store = MemoryStore()
    reply = draft("florence-1478.yaml")
    assert reply["settings"]["lethality"] == "brutal"
    settings = Settings(lethality=Lethality.SOFT, tone="satirical")
    state = load_state(store, sz.establish(store, FLORENCE, settings, GM(reply)))
    assert state.settings == settings


def test_a_refused_draft_is_retried_once_with_its_problems() -> None:
    bad = draft("florence-1478.yaml")
    bad["assets"][0]["controller"] = "nobody"
    store = MemoryStore()
    gm = GM(bad, draft("florence-1478.yaml"))
    sz.establish(store, FLORENCE, Settings(), gm)
    assert len(gm.requests) == 2
    assert "asset medici_bank: unknown entity nobody" in gm.requests[1].prompt
    assert "unknown entity" not in gm.requests[0].prompt


def test_two_bad_drafts_write_nothing() -> None:
    bad = draft("washington-1962.yaml")
    bad["roster"] = bad["roster"][:3]
    store = MemoryStore()
    with pytest.raises(sz.SessionZeroError) as info:
        sz.establish(store, WASHINGTON, Settings(), GM(bad, copy.deepcopy(bad)))
    assert info.value.problems == ("roster: 3 characters, not 8-12",)
    assert len(info.value.costs) == 2
    assert store.campaign_ids() == []


def _without_places(w: dict[str, Any]) -> None:
    w["entities"] = [e for e in w["entities"] if e["kind"] != "location"]
    for a in w["assets"]:
        a.pop("location", None)


@pytest.mark.parametrize(
    ("edit", "problem"),
    [
        (lambda w: w["seats"][0].update(controller="human"), "a draft has no human seats"),
        (lambda w: w["roster"].append(dict(w["roster"][0])), "listed twice"),
        (lambda w: w["roster"][0].update(entity="florence"), "not a person"),
        (lambda w: w["roster"][0].update(means=["pazzi_bank"]), "does not control pazzi_bank"),
        (lambda w: w["roster"][0].pop("composite"), "composite: Field required"),
        (lambda w: w.update(entities="nonsense"), "entities: Input should be a valid"),
        (lambda w: _without_places(w), "places: 0 locations, at least 6"),
        (
            lambda w: next(a for a in w["assets"] if a["key"] == "giuliano_household").pop(
                "location"
            ),
            "asset giuliano_household: a force needs a location",
        ),
        (
            lambda w: next(e for e in w["entities"] if e["key"] == "giuliano").pop("parent"),
            "entity giuliano: holds assets but has no parent faction",
        ),
        (
            lambda w: w.update(assets=[a for a in w["assets"] if a["controller"] != "federico"]),
            "seat urbino_seat: urbino holds no assets; set its people's parent to urbino",
        ),
    ],
)
def test_the_history_checks(edit: Any, problem: str) -> None:
    bad = draft("florence-1478.yaml")
    edit(bad)
    world, problems = sz._check(bad, FLORENCE, Settings())
    assert world is None and any(problem in p for p in problems), problems


def test_the_drafts_pass_the_history_checks() -> None:
    for name, place in (("florence-1478.yaml", FLORENCE), ("washington-1962.yaml", WASHINGTON)):
        _, problems = sz._check(draft(name), place, Settings())
        assert problems == [], (name, problems)


def test_a_place_must_be_named() -> None:
    with pytest.raises(sz.SessionZeroError, match="place and date"):
        sz.draft_world("   ", Settings(), GM())
    with pytest.raises(sz.SessionZeroError, match="place and date"):
        sz.draft_world("x" * 81, Settings(), GM())
    reply = Reply(["not", "a", "world"], "m", 1, 1, 0, 0.0)

    class Junk:
        def call(self, request: Request) -> Reply:
            return reply

    with pytest.raises(sz.SessionZeroError, match="not a world file"):
        sz.draft_world(FLORENCE, Settings(), Junk())


def test_no_tick_runs_during_session_zero() -> None:
    store = MemoryStore()
    campaign = sz.establish(store, FLORENCE, Settings(), GM(draft("florence-1478.yaml")))
    providers = Providers(ScriptedResolver(), TemplateNarrator(), RecordingDeliverer())
    with pytest.raises(NotInPlayError, match="Session Zero is still open"):
        run_tick(store, campaign, providers)


# --- surfaces --------------------------------------------------------------------

runner = CliRunner()


def test_the_cli_establishes_and_refuses_a_tick(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("REVISIONIST_DB", str(tmp_path / "game.db"))
    monkeypatch.setenv("REVISIONIST_BRIEFINGS_DIR", str(tmp_path / "briefings"))
    monkeypatch.setattr(cli, "transport_factory", lambda: GM(draft("washington-1962.yaml")))
    card = runner.invoke(cli.app, ["establish", "pitch", WASHINGTON])
    assert card.exit_code == 0, card.output
    assert "1. Washington, October 1962" in card.output
    result = runner.invoke(cli.app, ["establish", "confirm", "1", "--lethality", "soft"])
    assert result.exit_code == 0, result.output
    assert "Session Zero at Washington, October 1962" in result.output
    assert "10 characters to choose from:" in result.output
    assert "Ruth Calder (composite): A photo interpreter" in result.output
    refused = runner.invoke(cli.app, ["tick", "--offline"])
    assert refused.exit_code == cli.EXIT_REFUSED
    assert "Session Zero is still open" in refused.output


def test_the_cli_reports_a_draft_that_never_compiled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("REVISIONIST_DB", str(tmp_path / "game.db"))
    bad = draft("florence-1478.yaml")
    bad["roster"] = []
    monkeypatch.setattr(cli, "transport_factory", lambda: GM(bad, bad))
    assert runner.invoke(cli.app, ["establish", "pitch", FLORENCE]).exit_code == 0
    result = runner.invoke(cli.app, ["establish", "confirm", "1"])
    assert result.exit_code == cli.EXIT_ERROR
    assert "roster: 0 characters" in result.output


def test_the_scheduler_waits_out_session_zero(pg_db: PgDb) -> None:
    store = PostgresEventStore(pg_db.app_dsn)
    campaign = sz.establish(store, FLORENCE, Settings(), GM(draft("florence-1478.yaml")))
    clock = Clock()
    sch.register(store, clock()).advance(campaign)
    outcomes = sch.run(
        store,
        clock,
        offline=True,
        transport_factory=lambda: GM(),
        briefings=Path("unused"),
        warn=lambda m: None,
        campaign=campaign,
    )
    assert [(o.action, o.detail) for o in outcomes] == [(sch.Action.WAITING, "session zero")]
    assert load_state(store, campaign).tick == 0
    store.close()


def test_discord_establish_is_the_hosts(pg_db: PgDb, tmp_path: Path) -> None:
    bad = draft("florence-1478.yaml")
    bad["roster"] = []
    game = Game(pg_db, GM(bad, bad, draft("florence-1478.yaml")), tmp_path)
    assert game.establish(STRANGER, FLORENCE) == HOST_ONLY
    heads_up = DRAFTING.format(place=FLORENCE)
    assert game.establish(HOST, FLORENCE) == heads_up + DRAFT_REFUSED
    reply = game.establish(HOST, FLORENCE).removeprefix(heads_up)
    assert reply.startswith("Session Zero is open at Florence, April 1478. 11 characters")
    assert "Lorenzo de' Medici" in reply and "Tommaso Vanni" in reply
    assert game.command("advance", HOST) == "waiting: session zero"


def test_session_zero_opens_once_and_lists_only_people() -> None:
    store = MemoryStore()
    campaign = sz.establish(store, FLORENCE, Settings(), GM(draft("florence-1478.yaml")))
    events = list(store.read(campaign))
    opened = next(e for e in events if isinstance(e.payload, ev.SessionZeroOpened))
    again = opened.model_copy(update={"seq": len(events), "event_id": "0" * 25 + "1"})
    with pytest.raises(FoldError, match="opens once"):
        replay([*events, again])
    listed = next(e for e in events if isinstance(e.payload, ev.CharacterListed))
    place = next(e.payload.entity for e in events if isinstance(e.payload, ev.EntityCreated))
    wrong = ev.CharacterListed(
        character=listed.payload.character.model_copy(update={"entity_id": place.entity_id})  # type: ignore[union-attr]
    )
    bad = listed.model_copy(update={"payload": wrong})
    upto = events[: listed.seq]
    with pytest.raises(FoldError, match="not a person"):
        replay([*upto, bad])
