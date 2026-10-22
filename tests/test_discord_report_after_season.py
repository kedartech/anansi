"""A season that is over but not retired: players reach /report and nothing else."""

from revisionist.engine.discord import handlers
from tests.test_discord_bot import HOST
from tests.test_discord_duo import FRIEND, Duo, duo  # noqa: F401  (the fixture)


def test_report_answers_after_the_season_ends_but_other_seat_commands_do_not(
    duo: Duo,  # noqa: F811
) -> None:
    assert duo.game.command("join", FRIEND, code=duo.code).startswith("You hold")
    assert "No one met their goal." in duo.game.command("season-end", HOST)
    report = duo.game.command("report", FRIEND)

    assert report not in (handlers.NO_GAME, handlers.NOT_LINKED, handlers.BROKEN)
    assert "No one met their goal." in report
    assert duo.game.command("status", FRIEND) == handlers.NO_GAME
    assert duo.game.command("goal", FRIEND, action="history") == handlers.NO_GAME
