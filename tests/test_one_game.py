"""One live game at a time, and the host runs it from Discord (playtest issues 4, 6, 7).

``/establish`` refuses while a game is live, before any model call. ``/retire``
sets a game aside with no reveal. ``/invite`` and ``/channel`` replace the CLI
for play.
"""

from pathlib import Path

from revisionist.engine import scheduler as sch
from revisionist.engine.discord.handlers import BAD_CODE, DRAFTING, HOST_ONLY, JOINED_ZERO
from revisionist.engine.discord.tables import Channels
from tests.pg import PgDb
from tests.test_discord_bot import HOST, STRANGER, Game
from tests.test_discord_duo import FRIEND
from tests.test_session_zero import FLORENCE, GM, WASHINGTON, draft

HEADS_UP = DRAFTING.format(place=FLORENCE)


def _code(reply: str) -> str:
    return reply.splitlines()[1]


def test_a_live_game_blocks_establish_until_it_is_retired(pg_db: PgDb, tmp_path: Path) -> None:
    gm = GM(draft("florence-1478.yaml"), draft("washington-1962.yaml"))
    game = Game(pg_db, gm, tmp_path)
    opened = game.command("establish", HOST, place=FLORENCE)
    assert opened.startswith(HEADS_UP + "Session Zero is open at Florence")
    first = game.campaign()

    refused = game.command("establish", HOST, place=WASHINGTON)
    assert refused == (
        f"Florence, April 1478 ({first}) is still live: one game at a time. "
        f"End it with /season-end, or set it aside with /retire {first}."
    )
    assert len(gm.requests) == 1  # refused before the draft: no model call, no cost

    assert game.command("retire", STRANGER) == HOST_ONLY
    assert game.command("retire", HOST) == (
        f"Live: Florence, April 1478 ({first}). Retire one with /retire <id>."
    )
    assert "No single live game" in game.command("retire", HOST, campaign="Paris")
    assert game.command("retire", HOST, campaign="florence, april 1478").startswith(
        f"Retired Florence, April 1478 ({first})"
    )
    assert game.command("retire", HOST) == "No live game to retire."

    store = game.store()
    try:
        row = sch.register(store, game.clock()).get(first)
        assert row.paused and row.retired_at is not None
        sch.ScheduleTable(store.conn).resume(first)
        assert sch.register(store, game.clock()).get(first).paused  # retired stays down
        assert sch.live_campaigns(store, game.clock()) == []
    finally:
        store.close()
    assert game.command("establish", HOST, place=WASHINGTON).startswith(
        DRAFTING.format(place=WASHINGTON) + "Session Zero is open at Washington"
    )


def test_the_host_invites_from_discord_and_a_retired_games_codes_die(
    pg_db: PgDb, tmp_path: Path
) -> None:
    game = Game(pg_db, GM(draft("florence-1478.yaml")), tmp_path)
    assert game.command("invite", HOST) != ""  # nothing live yet: no crash
    game.command("establish", HOST, place=FLORENCE)
    assert game.command("invite", STRANGER) == HOST_ONLY

    reply = game.command("invite", HOST)
    assert reply.startswith("Invite for a new player in Florence, April 1478, good once until")
    assert game.command("join", FRIEND, code=_code(reply)).startswith(JOINED_ZERO)

    spare = _code(game.command("invite", HOST))
    game.command("retire", HOST, campaign=game.campaign())
    assert game.command("join", STRANGER, code=spare) == BAD_CODE


def test_channel_binds_where_it_is_run(pg_db: PgDb, tmp_path: Path) -> None:
    game = Game(pg_db, GM(draft("florence-1478.yaml")), tmp_path)
    game.command("establish", HOST, place=FLORENCE)
    assert game.command("channel", STRANGER) == HOST_ONLY
    assert game.command("channel", HOST).startswith("Public statements and the press")
    store = game.store()
    try:
        assert Channels(store.conn).public(game.campaign()) == "777777777777777777"
    finally:
        store.close()
