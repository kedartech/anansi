"""Playtest #35: picking a character should not need the roster's exact spelling.

Drives the real ``/character`` command on the Florence roster (two Pazzis, two
Francescos, two Medici brothers). A typed name that fits one listed person claims
that person; one that fits two returns the short list and claims nobody; a miss
still reaches ``pick``'s model route; a listed person is never added a second time.
"""

from typing import Any

import pytest

from revisionist.engine.store import load_state
from tests.test_discord_bot import HOST, Game
from tests.test_discord_duo import FRIEND
from tests.test_discord_public import _seated, zero  # noqa: F401


def _roster_size(game: Game) -> int:
    store = game.store()
    try:
        return len(load_state(store, game.campaign()).roster)
    finally:
        store.close()


def _claimed(game: Game) -> dict[str, str]:
    store = game.store()
    try:
        state = load_state(store, game.campaign())
    finally:
        store.close()
    return {
        state.entities[c].name: str(d.claimed_by) for c, d in state.roster.items() if d.claimed_by
    }


@pytest.mark.parametrize("action", ["claim", "pick"])
@pytest.mark.parametrize(
    ("typed", "listed"),
    [
        ("Lorenzo Medici", "Lorenzo de' Medici"),
        ("lorenzo de medici", "Lorenzo de' Medici"),
        ("Lucrezia", "Lucrezia Tornabuoni"),
        ("Francesco Pazzi", "Francesco de' Pazzi"),
        ("Jacopo Pazzi", "Jacopo de' Pazzi"),
        ("Salviati", "Francesco Salviati"),
    ],
)
def test_a_name_that_fits_one_listed_person_claims_that_person(
    zero: Any,  # noqa: F811
    action: str,
    typed: str,
    listed: str,
) -> None:
    _seated(zero)
    size = _roster_size(zero)

    reply = zero.command("character", FRIEND, action=action, text=typed)

    assert reply == f"You play {listed}."
    assert _roster_size(zero) == size  # a listed person is never added twice
    assert "character_pick" not in zero.transport.calls


@pytest.mark.parametrize("action", ["claim", "pick"])
@pytest.mark.parametrize(
    ("typed", "both"),
    [
        ("Pazzi", ("Francesco de' Pazzi", "Jacopo de' Pazzi")),
        ("Francesco", ("Francesco de' Pazzi", "Francesco Salviati")),
        ("Medici", ("Lorenzo de' Medici", "Giuliano de' Medici")),
    ],
)
def test_a_name_that_fits_two_people_lists_them_and_claims_nobody(
    zero: Any,  # noqa: F811
    action: str,
    typed: str,
    both: tuple[str, str],
) -> None:
    _seated(zero)

    reply = zero.command("character", FRIEND, action=action, text=typed)

    assert all(name in reply for name in both)
    assert not reply.startswith("You play")
    assert _claimed(zero) == {}
    assert "character_pick" not in zero.transport.calls


def test_a_name_nobody_listed_still_reaches_the_model_route(zero: Any) -> None:  # noqa: F811
    _seated(zero)

    reply = zero.command("character", HOST, action="pick", text="Luca Pitti")

    assert reply == "You play Luca Pitti."
    assert "character_pick" in zero.transport.calls
