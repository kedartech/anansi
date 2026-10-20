"""Layer 9b: two humans on Discord. Invites, ``/join``, ``/dispatch`` and held briefings.

The host plays Varda and links it from the CLI; a friend takes Ostrel with an
invite code. Same fake gateway and fake REST as the 9a tests; no network, no
model.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from revisionist import cli
from revisionist.core import events as ev
from revisionist.core.ids import SeatId
from revisionist.core.project import project
from revisionist.engine import campaign as cmp
from revisionist.engine.discord import handlers
from revisionist.engine.discord.rest import HELD_LABEL
from revisionist.engine.discord.tables import SeatLinks
from revisionist.engine.llm import Reply, Request
from revisionist.engine.store import load_state
from tests.pg import PgDb
from tests.test_cli import FakeClaude
from tests.test_discord_bot import GUILD, HOST, ORDER, STRANGER, Game
from tests.test_scheduler import T0

FRIEND = "555555555555555555"
VARDA = "First Minister of Varda"
OSTREL = "Ostrel War Council"

runner = CliRunner()


class ReaderClaude(FakeClaude):
    """Narrations that name their reader, so two seats' briefings differ."""

    def call(self, request: Request) -> Reply:
        if request.call_class != "narrator":
            return super().call(request)
        reader = request.prompt.splitlines()[0].removeprefix("## Reader: ").split(" (")[0]
        return Reply({"text": f"Briefing for {reader}."}, request.model, 100, 20, 0, 0.001)


@dataclass
class Duo:
    game: Game
    code: str
    """An open invite for Ostrel, issued at T0 for 72 hours."""

    def seat(self, name: str) -> SeatId:
        store = self.game.store()
        try:
            return cmp.resolve_seat(load_state(store, self.game.campaign()), name)
        finally:
            store.close()

    def invite(self, *args: str) -> str:
        result = runner.invoke(cli.app, ["discord", "invite", "--seat", OSTREL, *args])
        assert result.exit_code == 0, result.output
        return result.output.splitlines()[1].strip()


@pytest.fixture
def duo(pg_db: PgDb, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Duo]:
    monkeypatch.setenv("REVISIONIST_DB", pg_db.app_dsn)
    monkeypatch.setenv("REVISIONIST_DISCORD_GUILD_ID", GUILD)
    monkeypatch.setenv("REVISIONIST_BRIEFINGS_DIR", str(tmp_path / "briefings"))
    monkeypatch.setattr(cli, "transport_factory", ReaderClaude)
    game = Game(pg_db, ReaderClaude(), tmp_path / "briefings")
    monkeypatch.setattr(cli, "clock", game.clock)
    out = runner.invoke(cli.app, ["new", "salt-strait-duo"]).output
    assert "human seats:" in out and VARDA in out and OSTREL in out
    linked = runner.invoke(cli.app, ["discord", "link", "--user", HOST, "--seat", VARDA])
    assert linked.exit_code == 0, linked.output
    d = Duo(game, "")
    d.code = d.invite()
    yield d


def _pending_actors(duo: Duo) -> set[SeatId]:
    store = duo.game.store()
    try:
        return {i.actor for i in load_state(store, duo.game.campaign()).pending.values()}
    finally:
        store.close()


def test_a_friend_joins_and_each_human_orders_for_their_own_seat(duo: Duo) -> None:
    game = duo.game
    reply = game.command("join", FRIEND, code=duo.code)
    assert reply.startswith(f"You hold {OSTREL}.") and "test DM" in reply
    welcome = [m for m in game.discord.messages if m.channel_id == f"dm-{FRIEND}"]
    assert [m.content for m in welcome] == [handlers.WELCOME.format(seat=OSTREL)]

    assert game.command("order", FRIEND, text=ORDER).startswith("filed ")
    assert _pending_actors(duo) == {duo.seat(OSTREL)}
    assert game.command("order", HOST, text=ORDER).startswith("filed ")
    assert _pending_actors(duo) == {duo.seat(OSTREL), duo.seat(VARDA)}
    assert game.command("status", FRIEND).startswith(f"tick 0  seat {OSTREL}")


def test_a_code_works_once_and_every_bad_code_reads_the_same(duo: Duo) -> None:
    game = duo.game
    short = duo.invite("--hours", "1")
    game.clock.now = T0 + timedelta(hours=2)
    refusals = {
        game.command("join", FRIEND, code="NOT-A-CODE"),
        game.command("join", FRIEND, code=short),  # expired
    }
    assert game.command("join", FRIEND, code=duo.code.lower().replace("-", " ")).startswith(
        "You hold"
    )  # case, dashes and spaces do not matter
    refusals.add(game.command("join", STRANGER, code=duo.code))  # spent
    assert refusals == {handlers.BAD_CODE}
    listed = runner.invoke(cli.app, ["discord", "invites"]).output
    assert f"used by {FRIEND}" in listed and "expired" in listed


def test_a_refused_link_does_not_spend_the_code(duo: Duo) -> None:
    game = duo.game
    store = game.store()
    links = SeatLinks(store.conn)
    links.link(game.campaign(), duo.seat(OSTREL), STRANGER, GUILD)  # someone got there first
    assert game.command("join", FRIEND, code=duo.code) == handlers.BAD_CODE
    links.unlink(game.campaign(), duo.seat(OSTREL))
    store.close()
    assert game.command("join", FRIEND, code=duo.code).startswith("You hold")


def test_a_seated_user_cannot_take_a_second_seat(duo: Duo) -> None:
    game = duo.game
    assert game.command("join", HOST, code=duo.code) == handlers.ALREADY_SEATED
    assert game.command("join", FRIEND, code=duo.code).startswith("You hold")  # still unspent


def test_the_cli_refuses_invites_for_linked_or_ai_seats(duo: Duo) -> None:
    linked = runner.invoke(cli.app, ["discord", "invite", "--seat", VARDA])
    assert linked.exit_code == cli.EXIT_REFUSED and "already linked" in linked.output
    ai = runner.invoke(cli.app, ["discord", "invite", "--seat", "Synod of Kesh"])
    assert ai.exit_code == cli.EXIT_REFUSED and "not a human seat" in ai.output
    no_seat = runner.invoke(cli.app, ["discord", "invite"])
    assert no_seat.exit_code == cli.EXIT_ERROR and "pick one with --seat" in no_seat.output


def test_the_friend_is_not_the_host(duo: Duo) -> None:
    game = duo.game
    game.command("join", FRIEND, code=duo.code)
    for command in ("advance", "pause", "resume"):
        assert game.command(command, FRIEND) == handlers.HOST_ONLY


def test_a_held_briefing_reaches_only_its_owner(duo: Duo) -> None:
    game = duo.game
    game.command("join", FRIEND, code=duo.code)
    game.discord.closed_dms.add(FRIEND)
    assert "ticked" in game.command("advance", HOST)

    store = game.store()
    channels: dict[str, Any] = {}
    for e in store.read(game.campaign()):
        if isinstance(e.payload, ev.BriefingDelivered):
            channels[e.payload.seat_id] = e.payload.channel
    store.close()
    assert channels[duo.seat(OSTREL)] == HELD_LABEL
    assert channels[duo.seat(VARDA)] == "discord"

    mine = game.command("dispatch", FRIEND)
    assert mine == f"Tick 0:\nBriefing for {OSTREL}."
    assert game.command("dispatch", HOST) == f"Tick 0:\nBriefing for {VARDA}."
    assert f"Briefing for {OSTREL}" not in game.discord.text()  # never sent anywhere
    assert game.command("dispatch", STRANGER) == handlers.NOT_LINKED


def test_dispatch_before_any_tick(duo: Duo) -> None:
    assert duo.game.command("dispatch", HOST) == handlers.NO_DISPATCH


def test_a_covert_order_stays_with_its_seat(duo: Duo) -> None:
    game = duo.game
    game.command("join", FRIEND, code=duo.code)
    reply = game.command("order", FRIEND, text=ORDER, visibility="covert", spin="Nothing moves.")
    assert reply.endswith("(covert)")
    store = game.store()
    state = load_state(store, game.campaign())
    [filed] = [e for e in store.read(game.campaign()) if isinstance(e.payload, ev.IntentFiled)]
    store.close()
    assert filed.scope.visibility.value == "covert"
    assert filed.scope.known_by == {duo.seat(OSTREL)}
    intent_id = filed.payload.intent.intent_id  # type: ignore[union-attr]
    assert intent_id not in project(state, duo.seat(VARDA)).model_dump_json()
    assert intent_id not in game.command("status", HOST)
