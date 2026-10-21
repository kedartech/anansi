"""V2 probe: /host link with no live game binds a player to a seat in a retired campaign."""

from tests.test_discord_bot import HOST, STRANGER, Game
from tests.test_discord_host import _human, _links, salt  # noqa: F401


def test_link_refuses_a_retired_game(salt: Game) -> None:  # noqa: F811
    _, seat = _human(salt)
    campaign = salt.campaign()
    assert "Retired" in salt.handlers.retire(HOST, campaign)
    assert _links(salt) == {}
    out = salt.handlers.host_link(HOST, STRANGER, seat)
    assert _links(salt) == {}, f"linked into a retired game: {out}"
