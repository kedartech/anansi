"""Playtest issue 9: the table-wide events are posted publicly, everything else stays ephemeral.

Session Zero runs on the scripted ``Table``; the live game runs on ``FakeClaude``.
Public lines are read from ``Game.public``, the followups sent without ``ephemeral``.
"""

import asyncio
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from revisionist import cli
from revisionist.core.canary import canaries_hidden_from_human
from revisionist.core.ids import SeatId
from revisionist.engine import scheduler as sch
from revisionist.engine.discord import bot
from revisionist.engine.discord.handlers import BAD_CODE, DRAFTING, HOST_ONLY, Said, public_safe
from revisionist.engine.discord.tables import SeatLinks
from revisionist.engine.store import load_state
from tests import test_discord_bot
from tests.pg import PgDb
from tests.test_discord_bot import GUILD, HOST, FakeInteraction, Game
from tests.test_discord_duo import FRIEND
from tests.test_session_zero import FLORENCE
from tests.test_session_zero_steps import PERSONA, Table

ORDER = "Tell the world the strait stays open."
salt = test_discord_bot.game
"""The live game on the salt-strait world, from ``test_discord_bot``."""


@pytest.fixture
def zero(monkeypatch: pytest.MonkeyPatch, pg_db: PgDb, tmp_path: Path) -> Iterator[Game]:
    """Nothing live yet: Session Zero is opened by ``/establish`` in the test."""
    monkeypatch.setenv("REVISIONIST_DB", pg_db.app_dsn)
    monkeypatch.setenv("REVISIONIST_DISCORD_GUILD_ID", GUILD)
    game = Game(pg_db, Table(), tmp_path / "briefings")
    monkeypatch.setattr(cli, "clock", game.clock)
    yield game


def _invite(game: Game) -> str:
    return game.command("invite", HOST).splitlines()[1].strip()


def _seated(game: Game) -> None:
    """Session Zero open, the host and a friend holding seats, nothing public yet."""
    game.command("establish", HOST, place=FLORENCE)
    for user in (HOST, FRIEND):
        game.command("join", user, code=_invite(game))
    game.public.clear()


def _campaign_name(game: Game) -> str:
    store = game.store()
    try:
        return sch.campaign_name(store, game.campaign())
    finally:
        store.close()


def _canary(game: Game) -> str:
    store = game.store()
    try:
        state = load_state(store, game.campaign())
    finally:
        store.close()
    return next(iter(canaries_hidden_from_human(state, ())))


# --- the public lines -----------------------------------------------------------------


def test_establish_is_announced_and_a_refusal_is_not(zero: Game) -> None:
    opened = zero.command("establish", HOST, place=FLORENCE)
    line = opened.removeprefix(DRAFTING.format(place=FLORENCE)).splitlines()[0]
    assert zero.public == [line]
    assert line.startswith(f"Session Zero is open at {FLORENCE}. ")
    assert zero.last_sent[-1] == (line, False)
    assert "one game at a time" in zero.command("establish", HOST, place=FLORENCE)
    assert zero.public == [line]


def test_join_is_announced_and_a_bad_code_is_not(zero: Game) -> None:
    zero.command("establish", HOST, place=FLORENCE)
    zero.public.clear()
    assert zero.command("join", HOST, code="NOT-A-CODE") == BAD_CODE
    assert zero.public == []
    assert zero.command("join", HOST, code=_invite(zero)).startswith("You are in.")
    assert zero.public == [f"<@{HOST}> joined {_campaign_name(zero)}."]
    assert zero.last_sent[-1] == (zero.public[0], False)


def test_claim_is_announced_and_the_roster_and_search_are_not(zero: Game) -> None:
    _seated(zero)
    assert "Lorenzo de' Medici" in zero.command("character", HOST, action="roster")
    assert "- **Lorenzo de' Medici**" in zero.command("character", HOST, action="roster")
    assert "Lorenzo de' Medici" in zero.command("character", HOST, action="search", text="money")
    assert zero.public == []
    reply = zero.command("character", HOST, action="claim", text="Lorenzo de' Medici")
    assert reply == "You play Lorenzo de' Medici."
    assert zero.public == [f"<@{HOST}> plays Lorenzo de' Medici."]
    assert zero.last_sent[-1] == (zero.public[0], False)


def test_pick_is_announced(zero: Game) -> None:
    _seated(zero)
    assert zero.command("character", FRIEND, action="pick", text="Luca Pitti") == (
        "You play Luca Pitti."
    )
    assert zero.public == [f"<@{FRIEND}> plays Luca Pitti."]


def test_begin_is_announced_and_its_refusals_are_not(zero: Game) -> None:
    _seated(zero)
    zero.command("begin", HOST)
    assert zero.public == []
    zero.command("character", HOST, action="claim", text="Lorenzo de' Medici")
    zero.command("character", FRIEND, action="claim", text="Giuliano de' Medici")
    for user in (HOST, FRIEND):
        zero.command("goal", user, action="history")
        zero.command("goal", user, action="confirm", text="1")
        zero.handlers.agent(user, PERSONA)
    zero.public.clear()
    assert zero.command("begin", FRIEND) == HOST_ONLY
    assert zero.public == []
    begun = zero.command("begin", HOST)
    assert begun.startswith("Session Zero is closed. 2 player(s)")
    assert zero.public == [begun]
    assert zero.last_sent[-1] == (begun, False)


def test_advance_is_announced_when_the_tick_runs(salt: Game) -> None:
    assert salt.command("advance") == "ticked: tick 0 resolved (advance)"
    assert salt.public == ["The world after tick 0:\nNo public change this tick."]
    assert salt.last_sent[-1] == (salt.public[0], False)


def test_an_advance_that_does_not_run_is_not_announced(salt: Game) -> None:
    assert salt.command("pause") == "Paused. No ticks until /resume."
    assert salt.command("advance").startswith("paused")
    assert salt.public == ["Paused. No ticks until /resume."]


def test_pause_and_resume_are_announced(salt: Game) -> None:
    assert salt.command("pause") == "Paused. No ticks until /resume."
    assert salt.command("resume") == "Resumed."
    assert salt.public == ["Paused. No ticks until /resume.", "Resumed."]
    assert salt.last_sent[-1] == ("Resumed.", False)


def test_retire_is_announced_and_the_listing_is_not(salt: Game) -> None:
    assert salt.command("retire").startswith("Live: ")
    assert salt.public == []
    campaign = salt.campaign()
    retired = salt.command("retire", campaign=campaign)
    assert retired.startswith("Retired ") and f"({campaign})" in retired
    assert salt.public == [retired]


# --- the private ones -----------------------------------------------------------------


def test_an_order_is_never_announced(salt: Game) -> None:
    assert salt.command("order", text=ORDER).startswith("filed ")
    assert salt.public == []


def test_goals_and_invites_are_never_announced(zero: Game) -> None:
    zero.command("establish", HOST, place=FLORENCE)
    zero.command("join", HOST, code=_invite(zero))
    zero.command("character", HOST, action="claim", text="Lorenzo de' Medici")
    zero.public.clear()
    assert zero.command("goal", HOST, action="history").endswith("/goal confirm <label>, e.g. H1.")
    assert zero.command("goal", HOST, action="confirm", text="1").startswith("Your goal is fixed")
    assert zero.command("invite", HOST).startswith("Invite for a new player")
    assert zero.public == []


# --- the canary check on public lines -------------------------------------------------


def test_a_public_line_with_a_hidden_canary_is_dropped_and_the_reply_arrives(
    salt: Game, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    canary = _canary(salt)
    monkeypatch.setattr(sch, "campaign_name", lambda store, campaign_id: f"Salt {canary}")
    with caplog.at_level(logging.WARNING, logger="revisionist.discord"):
        retired = salt.command("retire", campaign=salt.campaign())
    assert retired.startswith("Retired ")
    assert salt.public == []
    assert salt.last_sent[-1][1] is True
    assert any("public line withheld" in r.message for r in caplog.records)


def test_public_safe_refuses_any_canary_in_a_public_line(salt: Game) -> None:
    store = salt.store()
    state = load_state(store, salt.campaign())
    store.close()
    canary = _canary(salt)
    assert public_safe(state, "Tick 0 has run.")
    assert not public_safe(state, f"Tick 0 {canary}.")


# --- the transport ----------------------------------------------------------------------


def test_the_public_part_is_sent_after_the_private_part() -> None:
    interaction: Any = FakeInteraction(HOST)
    asyncio.run(bot._reply(interaction, Said("private", "public")))
    assert interaction.followup.sent == [("private", True), ("public", False)]


def test_a_reply_without_a_public_part_sends_nothing_public() -> None:
    interaction: Any = FakeInteraction(HOST)
    asyncio.run(bot._reply(interaction, Said("private")))
    asyncio.run(bot._reply(interaction, "plain"))
    assert interaction.followup.sent == [("private", True), ("plain", True)]


def test_begin_dms_each_player_their_own_opening_dossier(zero: Game) -> None:
    _seated(zero)
    zero.command("character", HOST, action="claim", text="Lorenzo de' Medici")
    zero.command("character", FRIEND, action="claim", text="Giuliano de' Medici")
    for user in (HOST, FRIEND):
        zero.command("goal", user, action="history")
        zero.command("goal", user, action="confirm", text="1")
        zero.handlers.agent(user, PERSONA)
    zero.command("begin", HOST)
    by_user = {
        user: "\n".join(m.content for m in zero.discord.messages if m.channel_id == f"dm-{user}")
        for user in (HOST, FRIEND)
    }
    for user, text in by_user.items():
        assert "Your holdings:" in text, (user, text)
        assert "Places:" in text
    store = zero.store()
    try:
        cid = zero.campaign()
        state = load_state(store, cid)
        seats_of: dict[str, list[SeatId]] = {}
        for seat, user in SeatLinks(store.conn).users(cid).items():
            seats_of.setdefault(user, []).append(seat)
    finally:
        store.close()
    assert by_user[HOST] != by_user[FRIEND]  # each reads their own seat, not a shared page
    for user, text in by_user.items():
        assert seats_of.get(user), user
        hidden = canaries_hidden_from_human(state, seats_of[user])
        assert not [c for c in hidden if c in text], (user, text)
