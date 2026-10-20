"""Layer 10 item 1e: the dashboard and the season's end (spec §15).

Winning is decided at season end: Lasting goals ever met, Undoable goals that
still hold. The full reveal makes every goal and covert secret public, and the
report says who won, or that no one did.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from revisionist import cli
from revisionist.core import delta as d
from revisionist.core import events as ev
from revisionist.core import goals as g
from revisionist.core.canary import canaries_hidden_from
from revisionist.core.dashboard import dashboard, render
from revisionist.core.fold import FoldError, replay
from revisionist.core.ids import CampaignId, DeltaId, SeatId
from revisionist.core.ontology import EntityStatus, Phase
from revisionist.core.project import project
from revisionist.core.resolution import OutcomeTier
from revisionist.core.visibility import PUBLIC, Visibility, private_to
from revisionist.core.world import WorldFile
from revisionist.core.world_compile import compile_world, resolve_keys
from revisionist.engine import play, season
from revisionist.engine.providers import RecordingDeliverer, ScriptedResolver, TemplateNarrator
from revisionist.engine.store import MemoryStore, load_state
from revisionist.engine.tick import NotInPlayError, Providers, run_tick
from tests import fixtures as fx
from tests.test_cli import FakeClaude
from tests.test_discord_bot import HOST
from tests.test_discord_duo import FRIEND, Duo, duo  # noqa: F401  (the fixture)
from tests.test_discord_duo import PUBLIC as PUBLIC_CHANNEL
from tests.test_goals import AIDE_DEAD, GOAL, GOAL_CANARY, TAKE_GUARD, _with_goal
from tests.test_intel import _learn, _south_goal
from tests.test_world_compile import duo_raw

runner = CliRunner()


def _providers() -> Providers:
    return Providers(ScriptedResolver(), TemplateNarrator(), RecordingDeliverer())


def _store(condition: g.WinCondition) -> MemoryStore:
    store = MemoryStore()
    store.append_all(_with_goal(condition))
    return store


def _apply(store: MemoryStore, delta: d.Delta) -> None:
    state = load_state(store, fx.CAMPAIGN)
    seq = store.last_seq(fx.CAMPAIGN) + 1
    store.append(fx.envelope(seq, ev.DeltaApplied(intent_id=None, delta=delta), tick=state.tick))


def _guard_to(holder: str, n: int) -> d.AssetControllerSet:
    return d.AssetControllerSet(
        delta_id=DeltaId(f"01ARZ3NDEKTSV4RRFFQ69S0D{n:02d}"),
        reason="test",
        asset_id=fx.A_SOUTH_GUARD,
        controller=fx.EntityId(holder),
    )


# --- the dashboard -----------------------------------------------------------------


def test_the_dashboard_shows_the_owner_their_goal_and_no_one_else() -> None:
    state = replay(_with_goal(g.AllOf(conditions=(TAKE_GUARD, AIDE_DEAD))))
    north = render(dashboard(project(state, fx.NORTH)))
    assert north[0].startswith("Regent of the North,") and "Slots 3." in north[0]
    assert any(line.startswith("Means: ") and "Northern Levies (tier 2)" in line for line in north)
    assert any(line.startswith("Standing: Stability") for line in north)
    goal = next(line for line in north if line.startswith("Your goal"))
    assert "(undoable)" in goal and "Progress 0 of 2; not yet." in goal and GOAL_CANARY in goal
    for seat in (fx.SOUTH, fx.GUILD):
        text = "\n".join(render(dashboard(project(state, seat))))
        assert GOAL_CANARY not in text and "Your goal" not in text


def test_the_dashboard_lists_the_pieces_gathered_on_each_rival() -> None:
    state = _south_goal(replay(fx.creation_events()))
    for _ in range(2):
        state, _piece = _learn(state, OutcomeTier.SUCCESS)
    rivals = dashboard(project(state, fx.NORTH)).rivals
    assert rivals == (("Chancellor of the South", ("exists: has a secret aim", "domain: force")),)
    assert dashboard(project(state, fx.GUILD)).rivals == ()


def test_each_briefing_opens_with_the_dashboard() -> None:
    store = _store(TAKE_GUARD)
    deliverer = RecordingDeliverer()
    run_tick(store, fx.CAMPAIGN, Providers(ScriptedResolver(), TemplateNarrator(), deliverer))
    sent = {seat: text for seat, text, _ in deliverer.sent}
    assert (
        sent[fx.NORTH].startswith("Regent of the North, tick 0.") and GOAL_CANARY in sent[fx.NORTH]
    )
    assert (
        sent[fx.SOUTH].startswith("Chancellor of the South, tick 0.")
        and GOAL_CANARY not in sent[fx.SOUTH]
    )
    assert not sent[fx.HERALD].startswith("The Herald, tick")  # the press has no dashboard
    lines = play.dashboard_lines(load_state(store, fx.CAMPAIGN), fx.NORTH)
    assert lines[0] == "Regent of the North, tick 1. Slots 3."


# --- winners -----------------------------------------------------------------------


def test_an_undoable_goal_held_at_the_end_wins() -> None:
    store = _store(TAKE_GUARD)
    _apply(store, _guard_to(fx.E_NORTH, 1))
    run_tick(store, fx.CAMPAIGN, _providers())
    report = season.end_season(store, fx.CAMPAIGN)
    assert report[1] == "Winners: Regent of the North."
    assert any("Regent of the North sought: Take what the South holds." in line for line in report)
    assert any("first met at tick 0; held at the end" in line for line in report)
    state = load_state(store, fx.CAMPAIGN)
    assert state.phase is Phase.SEASON_OVER
    assert state.goals[GOAL].scope == PUBLIC  # the full reveal
    assert GOAL_CANARY not in canaries_hidden_from(state, fx.SOUTH)
    assert not any(f.scope.visibility is Visibility.COVERT for f in state.facts.values())
    with pytest.raises(NotInPlayError, match="the season is over"):
        run_tick(store, fx.CAMPAIGN, _providers())
    with pytest.raises(season.SeasonError, match="only end in play"):
        season.end_season(store, fx.CAMPAIGN)
    assert season.report(store, fx.CAMPAIGN) == report  # rebuilt from the log, the same


def test_an_undoable_goal_broken_before_the_end_loses() -> None:
    store = _store(TAKE_GUARD)
    _apply(store, _guard_to(fx.E_NORTH, 1))
    run_tick(store, fx.CAMPAIGN, _providers())
    _apply(store, _guard_to(fx.E_SOUTH, 2))
    run_tick(store, fx.CAMPAIGN, _providers())
    report = season.end_season(store, fx.CAMPAIGN)
    assert report[1] == "No one met their goal."
    assert any("first met at tick 0; not held at the end" in line for line in report)


def test_a_lasting_goal_wins_even_reversed() -> None:
    store = _store(AIDE_DEAD)
    _apply(
        store,
        d.EntityStatusSet(
            delta_id=DeltaId("01ARZ3NDEKTSV4RRFFQ69S0E01"),
            reason="test",
            entity_id=fx.E_SOUTH_AIDE,
            status=EntityStatus.DEAD,
        ),
    )
    run_tick(store, fx.CAMPAIGN, _providers())
    _apply(
        store,
        d.EntityStatusSet(
            delta_id=DeltaId("01ARZ3NDEKTSV4RRFFQ69S0E02"),
            reason="test",
            entity_id=fx.E_SOUTH_AIDE,
            status=EntityStatus.ACTIVE,
        ),
    )
    run_tick(store, fx.CAMPAIGN, _providers())
    assert season.end_season(store, fx.CAMPAIGN)[1] == "Winners: Regent of the North."


def test_the_log_cannot_crown_anyone_the_goals_did_not() -> None:
    events = _with_goal(TAKE_GUARD)
    forged = fx.envelope(len(events), ev.SeasonEnded(winners=(fx.NORTH,), revealed=0))
    with pytest.raises(FoldError, match="other winners"):
        replay([*events, forged])
    honest = fx.envelope(len(events), ev.SeasonEnded(winners=(), revealed=0))
    assert replay([*events, honest]).phase is Phase.SEASON_OVER


def test_the_report_tells_who_had_uncovered_what() -> None:
    world = WorldFile.model_validate(duo_raw())
    campaign = CampaignId("01ARZ3NDEKTSV4RRFFQ69G5DU2")
    ids = resolve_keys(world, campaign)
    store = MemoryStore()
    store.append_all(compile_world(world, campaign, fx.VERSIONS))
    varda, ostrel = SeatId(ids["varda_seat"]), SeatId(ids["ostrel_seat"])
    state = load_state(store, campaign)
    piece = d.FactAssert(
        delta_id=DeltaId("01ARZ3NDEKTSV4RRFFQ69S0F01"),
        reason="goal intelligence",
        fact_id=fx.FactId("01ARZ3NDEKTSV4RRFFQ69S0F02"),
        subject=state.seats[ostrel].embodies,
        predicate="goal_exists",
        obj="has a secret aim",
        scope=private_to(varda),
    )
    seq = store.last_seq(campaign) + 1
    store.append(
        fx.envelope(seq, ev.DeltaApplied(intent_id=None, delta=piece)).model_copy(
            update={"campaign_id": campaign, "event_id": "01ARZ3NDEKTSV4RRFFQ69S0F03"}
        )
    )
    report = season.end_season(store, campaign)
    assert report[1] == "No one met their goal."
    ostrel_at = next(
        i for i, line in enumerate(report) if line.startswith("Ostrel War Council sought")
    )
    assert report[ostrel_at + 1] == "  First Minister of Varda had uncovered 1 of 4 pieces."
    assert report[-1].endswith("covert secrets are now public.")


# --- surfaces ------------------------------------------------------------------------


def test_the_cli_shows_the_dashboard_and_ends_the_season(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("REVISIONIST_DB", str(tmp_path / "game.db"))
    monkeypatch.setenv("REVISIONIST_BRIEFINGS_DIR", str(tmp_path / "briefings"))
    monkeypatch.setattr(cli, "transport_factory", FakeClaude)

    def ok(*args: str) -> str:
        result = runner.invoke(cli.app, list(args))
        assert result.exit_code == 0, result.output
        return result.output

    ok("new", "salt-strait")
    board = ok("dashboard")
    assert "Your goal (undoable): Keep the Saltworks" in board and "Progress 1 of 2" in board
    ok("tick", "--offline")
    early = runner.invoke(cli.app, ["season", "report"])
    assert early.exit_code == cli.EXIT_REFUSED and "has not ended" in early.output
    ended = ok("season", "end")
    assert "No one met their goal." in ended and "Keep the Saltworks" in ended
    assert ok("season", "report") == ended
    after = runner.invoke(cli.app, ["tick", "--offline"])
    assert after.exit_code == cli.EXIT_REFUSED and "the season is over" in after.output


def test_discord_season_end_posts_the_reveal_once(duo: Duo) -> None:  # noqa: F811
    assert runner.invoke(cli.app, ["discord", "channel", "public", PUBLIC_CHANNEL]).exit_code == 0
    assert duo.game.command("join", FRIEND, code=duo.code).startswith("You hold")
    board = duo.game.command("dashboard", FRIEND)
    assert "Your goal (undoable): Take the Saltworks for Ostrel." in board
    assert "Keep the Saltworks" not in board  # Varda's goal stays Varda's
    assert duo.game.command("season-end", FRIEND).startswith("Only the host")
    reply = duo.game.command("season-end", HOST)
    assert "No one met their goal." in reply
    posted = [m.content for m in duo.game.discord.messages if m.channel_id == PUBLIC_CHANNEL]
    assert len(posted) == 1 and posted[0].startswith("Season's end at tick 0.")
    assert "Take the Saltworks for Ostrel." in posted[0]
    assert "The season can only end in play" in duo.game.command("season-end", HOST)
    assert len([m for m in duo.game.discord.messages if m.channel_id == PUBLIC_CHANNEL]) == 1
