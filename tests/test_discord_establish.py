"""Spec §5.2 steps 1-4 on Discord: ``/establish pitch`` reads the card, ``/establish
confirm`` opens it.

The card is private and DMed to the host; nothing goes public until the campaign
opens. A refusal that needs no model call comes back before the drafting heads-up.
"""

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from revisionist.engine.discord.handlers import DRAFT_REFUSED, DRAFTING, HOST_ONLY, NO_CARD
from revisionist.engine.store import load_state
from tests.pg import PgDb
from tests.test_discord_bot import HOST, STRANGER, Game
from tests.test_session_zero import FLORENCE, GM, WASHINGTON, draft


def _dms(game: Game, user: str = HOST) -> list[str]:
    return [m.content for m in game.discord.messages if m.channel_id == f"dm-{user}"]


def _calls(gm: GM, call_class: str) -> list[Any]:
    return [r for r in gm.requests if r.call_class == call_class]


def _bad_washington() -> dict[str, Any]:
    bad = draft("washington-1962.yaml")
    bad["roster"] = bad["roster"][:3]
    return bad


@pytest.fixture
def zero(pg_db: PgDb, tmp_path: Path) -> Any:
    gm = GM(draft("florence-1478.yaml"), draft("washington-1962.yaml"))
    return gm, Game(pg_db, gm, tmp_path / "briefings")


def test_a_stranger_is_refused_at_both_steps(zero: Any) -> None:
    gm, game = zero

    assert game.sub("establish", "pitch", STRANGER, text=FLORENCE) == HOST_ONLY
    assert game.sub("establish", "confirm", STRANGER, choice=1) == HOST_ONLY

    assert gm.requests == []
    assert game.handlers.cards == {}


def test_the_card_is_private_and_dmed_to_the_host(zero: Any) -> None:
    _, game = zero

    reply = game.sub("establish", "pitch", text=FLORENCE)

    assert "1. **Florence, April 1478**, 19 April 1478. Focus: Florence, April 1478." in reply
    assert "   1. The first thing history did next." in reply
    assert "   2. The second." in reply
    assert "Confirm with /establish confirm choice:<n>." in reply
    assert reply in _dms(game)
    assert game.public == []
    card = game.handlers.cards[HOST]
    assert card.card.start_points[0].label == FLORENCE
    assert card.pitch == FLORENCE


def test_a_bad_pitch_says_why_and_keeps_no_card(zero: Any) -> None:
    gm, game = zero

    assert game.sub("establish", "pitch", text="   ") == (
        "Give a pitch: a war, a year, a person, a place."
    )
    assert gm.requests == []
    assert game.handlers.cards == {}


def test_a_new_pitch_replaces_the_card(zero: Any) -> None:
    _, game = zero
    game.sub("establish", "pitch", text=FLORENCE)

    game.sub("establish", "pitch", text=WASHINGTON)

    assert game.handlers.cards[HOST].pitch == WASHINGTON


def test_confirm_with_no_card_says_so_and_drafts_nothing(zero: Any) -> None:
    gm, game = zero

    assert game.sub("establish", "confirm", choice=1) == NO_CARD

    assert gm.requests == []


@pytest.mark.parametrize(
    ("options", "refusal"),
    [
        ({"strike": "x"}, "Strike history items by number, e.g. 2, 4."),
        ({"strike": "9"}, "Strike items from 1 to 2 of that start point's schedule."),
        ({"end": "soon"}, "Give the end date as YYYY-MM-DD, e.g. 1945-05-08."),
        ({"end": "1400-01-01"}, "The end date must come after the start, 1478-04-19."),
        ({"days": 400}, "The real-life length is 1 to 365 days."),
        ({"choice": 2}, "Pick a start point from 1 to 1."),
    ],
)
def test_a_refusal_before_the_draft_has_no_heads_up(
    zero: Any, options: dict[str, Any], refusal: str
) -> None:
    gm, game = zero
    game.sub("establish", "pitch", text=FLORENCE)
    options = {"choice": 1, **options}

    reply = game.sub("establish", "confirm", **options)

    assert reply == refusal
    assert DRAFTING.format(place=FLORENCE) not in reply
    assert _calls(gm, "draft_outline") == []
    assert HOST in game.handlers.cards


def test_a_struck_history_item_is_left_out_of_the_draft(zero: Any) -> None:
    gm, game = zero
    game.sub("establish", "pitch", text=FLORENCE)

    game.sub("establish", "confirm", choice=1, strike="1")

    [schedule] = _calls(gm, "draft_schedule")
    assert "The first thing history did next." not in schedule.prompt
    assert "The second." in schedule.prompt


def test_the_end_date_and_real_life_length_land_in_the_settings(zero: Any, pg_db: PgDb) -> None:
    _, game = zero

    game.sub("establish", "pitch", text=FLORENCE)
    game.sub("establish", "confirm", choice=1, end="1500-01-01", days=30)

    store = game.store()
    try:
        settings = load_state(store, game.campaign()).settings
    finally:
        store.close()
    assert settings.end_date == date(1500, 1, 1)
    assert settings.real_days == 30


def test_progress_lines_reach_the_host_dm_and_not_the_public(zero: Any) -> None:
    _, game = zero

    game.establish(HOST, FLORENCE)

    assert any("Outline done." in text for text in _dms(game))
    assert not any("Outline done." in text for text in game.public)


def test_a_refused_draft_keeps_the_card_for_a_second_confirm(pg_db: PgDb, tmp_path: Path) -> None:
    bad = _bad_washington()
    gm = GM(bad, _bad_washington())
    game = Game(pg_db, gm, tmp_path / "briefings")
    game.sub("establish", "pitch", text=WASHINGTON)

    first = game.sub("establish", "confirm", choice=1)
    assert first == DRAFTING.format(place=WASHINGTON) + DRAFT_REFUSED
    assert HOST in game.handlers.cards

    outlines = len(_calls(gm, "draft_outline"))
    second = game.sub("establish", "confirm", choice=1)

    assert second == DRAFTING.format(place=WASHINGTON) + DRAFT_REFUSED
    assert len(_calls(gm, "draft_outline")) > outlines
    assert len(_calls(gm, "pitch")) == 1
