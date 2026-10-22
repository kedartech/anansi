"""Playtest #34: a command must never act on a game the player is not in.

Walks the bot's registered command tree and runs every command in three states:
only a retired game; a live game the player has not joined; a live game they hold a
seat in. A retired game must never answer, never be written to, and never cost a
model call. A new command that is not classified here fails the first test.
"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from revisionist.engine import scheduler as sch
from revisionist.engine.discord.handlers import BROKEN, NO_GAME
from tests.pg import PgDb
from tests.test_discord_bot import HOST, STRANGER, Game
from tests.test_discord_duo import FRIEND
from tests.test_session_zero import FLORENCE, GM, WASHINGTON, draft

SEAT = {
    "agent", "briefing", "bug", "character", "dashboard", "dispatch", "dossier",
    "goal", "history", "order", "report", "status",
}  # fmt: skip
"""Commands that act as the player's seat (``_as_seat``)."""

HOST_TOOLS = {
    "advance", "begin", "channel", "invite", "pause", "resume", "season-end",
    "host seats", "host invites", "host link", "host unlink", "host flags",
    "host approve", "host cost", "host leaks", "host why", "host replay", "host verify",
}  # fmt: skip
"""Host commands that act on 'the current game' (``_as_host``)."""

OTHER = {"establish pitch", "establish confirm", "retire", "host schedule", "join", "key"}
"""Commands with their own game rules, covered by their own tests."""

OPTIONS: dict[str, dict[str, Any]] = {
    "agent": {"name": "Ser Agnolo", "role": "chancellor"},
    "bug": {"notes": "it broke"},
    "character": {"action": "roster"},
    "goal": {"action": "history"},
    "order": {"text": "Hold the line."},
    "host link": {"user": SimpleNamespace(id=int(FRIEND)), "seat": "Lorenzo de' Medici"},
    "host unlink": {"seat": "Lorenzo de' Medici"},
    "host approve": {"seat": "Lorenzo de' Medici"},
    "host why": {"target": "Lorenzo de' Medici"},
    "host replay": {"tick": 1},
}

LEAKS = ("Florence", "Medici", "Lorenzo")
"""What only the retired game could say."""


def _paths(game: Game) -> set[str]:
    found: set[str] = set()
    for top in game.bot.tree.get_commands(guild=game.bot.guild):
        if isinstance(top, discord.app_commands.Group):
            found |= {f"{top.name} {sub.name}" for sub in top.commands}
        else:
            found.add(top.name)
    return found


def _run(game: Game, path: str, user: str) -> str:
    options = OPTIONS.get(path, {})
    group, _, name = path.partition(" ")
    if name:
        return game.sub(group, name, user, **options)
    found = game.bot.tree.get_command(path, guild=game.bot.guild)
    assert isinstance(found, discord.app_commands.Command)
    return game._run(found, user, options)  # `command()` takes `name` itself; /agent has one too


def _invite(game: Game) -> str:
    return game.command("invite", HOST).splitlines()[1].strip()


class Rig:
    """A Florence game with FRIEND seated, then retired; Washington optional after.

    The game is retired in the schedule only, and the seat link is left in place: that
    is how the live table looked when #34 was found (older games linked and never
    unlinked, or unlinked by ``/retire`` leaving an even older link behind).
    """

    def __init__(self, pg_db: PgDb, tmp_path: Path) -> None:
        self.gm = GM(draft("florence-1478.yaml"), draft("washington-1962.yaml"))
        self.game = Game(pg_db, self.gm, tmp_path / "briefings")
        self.game.establish(HOST, FLORENCE)
        self.old = self.game.campaign()
        for user in (HOST, FRIEND):
            self.game.command("join", user, code=_invite(self.game))
        self.game.command("character", FRIEND, action="claim", text="Lorenzo de' Medici")
        store = self.game.store()
        try:
            sch.ScheduleTable(store.conn).retire(self.old, self.game.clock())
        finally:
            store.close()

    def last_seq(self) -> int:
        store = self.game.store()
        try:
            return int(store.last_seq(self.old))
        finally:
            store.close()

    def open_washington(self) -> None:
        self.game.establish(HOST, WASHINGTON)

    def join_washington(self, user: str) -> None:
        self.game.command("join", user, code=_invite(self.game))


@pytest.fixture
def rig(pg_db: PgDb, tmp_path: Path) -> Rig:
    return Rig(pg_db, tmp_path)


def test_every_registered_command_is_classified(rig: Rig) -> None:
    assert not SEAT & HOST_TOOLS and not (SEAT | HOST_TOOLS) & OTHER
    assert _paths(rig.game) == SEAT | HOST_TOOLS | OTHER


def _assert_old_game_untouched(rig: Rig, before_seq: int, before_calls: int, replies: dict) -> None:  # type: ignore[type-arg]
    for path, reply in replies.items():
        assert reply != BROKEN, path
        assert not any(word in reply for word in LEAKS), (path, reply)
    assert rig.last_seq() == before_seq, "the retired game was written to"
    assert len(rig.gm.requests) == before_calls, "a model call was spent on a game not in play"


@pytest.mark.parametrize("path", sorted(SEAT))
def test_a_seat_command_in_a_retired_game_answers_for_no_game(rig: Rig, path: str) -> None:
    seq, calls = rig.last_seq(), len(rig.gm.requests)

    reply = _run(rig.game, path, FRIEND)

    _assert_old_game_untouched(rig, seq, calls, {path: reply})
    assert reply.strip() != ""


@pytest.mark.parametrize("path", sorted(SEAT))
def test_a_seat_command_without_a_seat_in_the_live_game_says_to_join(rig: Rig, path: str) -> None:
    rig.open_washington()
    seq, calls = rig.last_seq(), len(rig.gm.requests)

    reply = _run(rig.game, path, FRIEND)

    _assert_old_game_untouched(rig, seq, calls, {path: reply})
    assert "/join" in reply, reply


@pytest.mark.parametrize("path", sorted(HOST_TOOLS))
def test_host_tools_with_no_live_game_do_not_fall_back_to_a_retired_one(
    rig: Rig, path: str
) -> None:
    seq, calls = rig.last_seq(), len(rig.gm.requests)

    reply = _run(rig.game, path, HOST)

    _assert_old_game_untouched(rig, seq, calls, {path: reply})
    assert reply == NO_GAME


@pytest.mark.parametrize("path", sorted(SEAT))
def test_a_seated_player_never_reaches_the_retired_game(rig: Rig, path: str) -> None:
    rig.open_washington()
    rig.join_washington(FRIEND)
    seq = rig.last_seq()

    reply = _run(rig.game, path, FRIEND)

    assert not any(word in reply for word in LEAKS), (path, reply)
    assert rig.last_seq() == seq


def test_a_stranger_is_still_told_to_ask_for_a_link(rig: Rig) -> None:
    rig.open_washington()
    assert "seat" in _run(rig.game, "status", STRANGER).lower() or "/join" in _run(
        rig.game, "status", STRANGER
    )
