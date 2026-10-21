"""The host's tools in Discord: the ``/host`` group, ``/report``, and the ``/advance`` failures.

Every ``/host`` subcommand is host-only and ephemeral. They run on the salt-strait
``game`` from ``test_discord_bot``, where the host holds the one human seat, and on
a Session Zero game opened on the scripted ``Table``.
"""

import asyncio
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from revisionist import cli
from revisionist.core.ids import SeatId
from revisionist.engine import campaign as cmp
from revisionist.engine import scheduler as sch
from revisionist.engine.discord import handlers
from revisionist.engine.discord.tables import SeatLinks
from revisionist.engine.store import load_state
from tests import test_discord_bot
from tests.pg import PgDb
from tests.test_discord_bot import GUILD, HOST, STRANGER, FakeInteraction, Game
from tests.test_session_zero import FLORENCE
from tests.test_session_zero_steps import Table

salt = test_discord_bot.game
"""The live game on the salt-strait world, from ``test_discord_bot``."""

SUBCOMMANDS = {
    "seats",
    "invites",
    "link",
    "unlink",
    "flags",
    "approve",
    "schedule",
    "cost",
    "leaks",
    "why",
    "replay",
    "verify",
}


@pytest.fixture
def zero(monkeypatch: pytest.MonkeyPatch, pg_db: PgDb, tmp_path: Path) -> Iterator[Game]:
    """Nothing live yet: Session Zero is opened by ``/establish`` in the test."""
    monkeypatch.setenv("REVISIONIST_DB", pg_db.app_dsn)
    monkeypatch.setenv("REVISIONIST_DISCORD_GUILD_ID", GUILD)
    game = Game(pg_db, Table(), tmp_path / "briefings")
    monkeypatch.setattr(cli, "clock", game.clock)
    yield game


def _host(game: Game, name: str, user_id: str = HOST, **options: Any) -> str:
    """Runs ``/host <name>`` through the real command tree, as Discord would."""
    group = game.bot.tree.get_command("host", guild=game.bot.guild)
    assert isinstance(group, discord.app_commands.Group)
    found = group.get_command(name)
    assert isinstance(found, discord.app_commands.Command)
    interaction = FakeInteraction(user_id)
    callback: Any = found.callback
    asyncio.run(callback(interaction, **options))
    assert interaction.response.deferred == [{"ephemeral": True, "thinking": True}]
    assert all(ephemeral for _, ephemeral in interaction.followup.sent)
    return "".join(content for content, _ in interaction.followup.sent)


def _user(user_id: str) -> SimpleNamespace:
    return SimpleNamespace(id=int(user_id))


def _human(game: Game) -> tuple[SeatId, str]:
    store = game.store()
    try:
        state = load_state(store, game.campaign())
        seat_id = cmp.human_seat(state)
        return seat_id, state.seats[seat_id].name
    finally:
        store.close()


def _links(game: Game) -> dict[SeatId, str]:
    store = game.store()
    try:
        return SeatLinks(store.conn).users(game.campaign())
    finally:
        store.close()


def test_the_host_group_has_every_subcommand(salt: Game) -> None:
    group = salt.bot.tree.get_command("host", guild=salt.bot.guild)
    assert isinstance(group, discord.app_commands.Group)
    assert {c.name for c in group.commands} == SUBCOMMANDS


def test_every_host_command_refuses_a_stranger_and_writes_nothing(salt: Game) -> None:
    _, seat = _human(salt)
    before, links = salt.last_seq(), _links(salt)
    attempts: list[tuple[str, dict[str, Any]]] = [
        ("seats", {}),
        ("invites", {}),
        ("link", {"user": _user(STRANGER), "seat": seat}),
        ("unlink", {"seat": seat}),
        ("flags", {}),
        ("approve", {"seat": seat}),
        ("schedule", {}),
        ("cost", {}),
        ("leaks", {}),
        ("why", {"target": "nothing", "reason": "curious"}),
        ("replay", {"tick": 0}),
        ("verify", {}),
    ]
    assert {name for name, _ in attempts} == SUBCOMMANDS
    for name, options in attempts:
        assert _host(salt, name, STRANGER, **options) == handlers.HOST_ONLY, name
    assert salt.last_seq() == before
    assert _links(salt) == links


def test_each_host_command_opens_with_its_first_line(salt: Game) -> None:
    campaign = salt.campaign()
    seat_id, seat = _human(salt)
    assert _host(salt, "seats").startswith(campaign)
    assert _host(salt, "invites") == "no invites"
    assert "power" in _host(salt, "flags")  # the world's First Minister is flagged at start
    assert _host(salt, "approve", seat=seat) == "Session Zero is not open in this campaign."
    assert _host(salt, "schedule").startswith(campaign)
    cost = _host(salt, "cost")
    assert cost.startswith("```\ntick ") and cost.endswith("\n```")
    assert _host(salt, "leaks").splitlines()[-1] == "0 leaks across 1 people"
    assert _host(salt, "why", target="nothing") == "nothing found"
    assert _host(salt, "replay", tick=0).startswith("tick 0 ")
    assert _host(salt, "verify").startswith("ok ")
    assert seat_id in _links(salt)


def test_linking_and_unlinking_the_one_human_seat(salt: Game) -> None:
    _, seat = _human(salt)
    taken = _host(salt, "link", user=_user(STRANGER), seat=seat)
    assert taken == f"seat {_human(salt)[0]} is already linked in {salt.campaign()}"
    assert _host(salt, "unlink", seat=seat) == f"Unlinked {seat}."
    assert _host(salt, "unlink", seat=seat) == f"{seat} was not linked."
    assert _host(salt, "link", user=_user(STRANGER), seat=seat) == (
        f"Linked {seat} to Discord user {STRANGER}."
    )
    assert _host(salt, "invites") == "no invites"


def test_link_refuses_unknown_and_non_human_seats(salt: Game) -> None:
    store = salt.store()
    try:
        faction = cmp.faction_seats(load_state(store, salt.campaign()))[0]
        name = load_state(store, salt.campaign()).seats[faction].name
    finally:
        store.close()
    assert _host(salt, "link", user=_user(STRANGER), seat="Nobody") == (
        "No seat called 'Nobody' here."
    )
    assert _host(salt, "link", user=_user(STRANGER), seat=name) == f"{name} is not a human seat."


def test_invites_list_what_the_host_issued(salt: Game) -> None:
    _, seat = _human(salt)
    assert _host(salt, "unlink", seat=seat) == f"Unlinked {seat}."
    assert salt.command("invite").startswith("Invite for")
    assert "open until" in _host(salt, "invites")


def test_link_in_session_zero_points_to_invite(zero: Game) -> None:
    assert "Session Zero is open" in zero.command("establish", HOST, place=FLORENCE)
    assert _host(zero, "link", user=_user(STRANGER), seat="anyone") == (
        "In Session Zero, use /invite."
    )
    assert _host(zero, "flags") == "no goals wait for approval"


def test_report_waits_for_the_season_end(salt: Game) -> None:
    assert salt.command("report") == "The season has not ended."
    assert salt.command("report", STRANGER) == handlers.NOT_LINKED


@pytest.mark.parametrize(
    ("action", "text"),
    [
        (sch.Action.LEAK, "Canary leak: the campaign is paused. Run /host leaks."),
        (sch.Action.FAILED, "The tick failed and resumes on the next run. See /host schedule."),
    ],
)
def test_advance_failures_name_the_discord_commands(
    salt: Game, monkeypatch: pytest.MonkeyPatch, action: sch.Action, text: str
) -> None:
    campaign = salt.campaign()

    def fake_run(store: object, clock: object, **kwargs: object) -> list[sch.Outcome]:
        return [sch.Outcome(campaign, action, "detail")]

    monkeypatch.setattr(sch, "run", fake_run)
    assert salt.command("advance") == text
