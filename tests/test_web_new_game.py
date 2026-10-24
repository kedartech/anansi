"""A new game from the web (realism, Batch 5): a pitch read into start points, a
generated cast, people built as they are picked, other players invited, and the
rest of the cast built once the host begins. Replayed from the hand-written 1962
recording; no model is called."""

import json
from typing import Any

import pytest

from revisionist.engine.llm import RecordedTransport
from revisionist.engine.store import load_state
from revisionist.engine.web.handlers import JOINED_ZERO
from tests.cast_scenario import KENNEDY_VOICE, PITCH
from tests.handwritten import CAST
from tests.test_web import Client, Web, web  # noqa: F401

CASTRO = "Fidel Castro"


def new(client: Client, action: str, /, **body: Any) -> dict[str, Any]:
    status, out = client.call(f"/api/new/{action}", body)
    assert status == 200, out
    return dict(out)


@pytest.fixture
def cast_web(web: Web) -> Web:  # noqa: F811
    transport = RecordedTransport(CAST)
    web.app.transport_factory = lambda: transport
    web.app.spawn = lambda run: run()
    return web


def started(web: Web) -> tuple[Client, Client]:  # noqa: F811
    """The host reads the pitch, takes the start point and invites one player."""
    host, _ = web.signed_up("hostess")
    out = new(host, "pitch", text=PITCH)
    assert not out["refused"]
    (point,) = out["card"]["start_points"]
    assert point["label"] == "Washington, 16 October 1962"
    assert not new(host, "cast", choice=1)["refused"]
    code = new(host, "invite")["code"]
    guest, _ = web.signed_up("guest")
    assert guest.seat("join", code=code) == JOINED_ZERO
    return host, guest


def test_the_ready_made_game_is_offered(cast_web: Web) -> None:
    client, _ = cast_web.signed_up("ada")
    assert new(client, "worlds")["worlds"] == [
        {"key": "autumn-1941", "name": "Autumn 1941", "start": "Washington, September 1941"}
    ]


def test_two_players_pick_and_the_host_begins(cast_web: Web) -> None:
    host, guest = started(cast_web)
    game = host.me()["game"]
    assert game["phase"] == "session_zero" and game["ruleset"] == "realism"
    assert game["host"] and game["waiting"] == 2
    assert not guest.me()["game"]["host"]
    roster = {c["name"]: c for c in game["view"]["roster"]}
    assert set(roster) == {CASTRO, "John F. Kennedy", "Nikita Khrushchev", "Robert McNamara"}

    assert new(host, "begin") == {"reply": "Still to choose: 2 player(s).", "refused": True}
    assert new(host, "pick", name="John F. Kennedy")["reply"] == "You are John F. Kennedy."
    assert new(guest, "pick", name="John F. Kennedy")["refused"]
    assert new(guest, "invite") == {"reply": "Only the host can do that.", "refused": True}
    assert new(guest, "pick", name="nikita khrushchev")["reply"] == "You are Nikita Khrushchev."
    assert new(guest, "begin")["refused"]

    out = new(host, "begin")
    assert not out["refused"], out
    store = cast_web.app.open()
    try:
        state = load_state(store, host.me()["game"]["id"])
    finally:
        store.close()
    assert set(state.cards) == set(state.roster)
    for client in (host, guest):
        me = client.me()
        assert me["game"]["phase"] == "play" and "realism" in me["game"]["view"]
        assert KENNEDY_VOICE not in json.dumps(me)
    # Two humans: only the host advances.
    status, out = guest.call("/api/realism/advance", {})
    assert status == 200 and out["refused"] and "host" in out["reply"]
    status, out = host.call("/api/realism/advance", {})
    assert status == 200 and not out["refused"], out


def test_the_cast_shows_no_engine_words(cast_web: Web) -> None:
    host, _ = started(cast_web)
    roster = host.me()["game"]["view"]["roster"]
    for c in roster:
        for text in (c["name"], c["pitch"], c["reach"]):
            assert "_" not in text


def test_a_seated_account_cannot_start_another_game(cast_web: Web) -> None:
    host, _ = started(cast_web)
    out = new(host, "pitch", text=PITCH)
    assert out["refused"] and "already" in out["reply"]


def test_the_cast_waits_for_a_pitch_and_a_real_start_point(cast_web: Web) -> None:
    client, _ = cast_web.signed_up("ada")
    assert new(client, "cast", choice=1) == {"reply": "Write your pitch first.", "refused": True}
    new(client, "pitch", text=PITCH)
    assert new(client, "cast", choice=2)["refused"]
    assert client.me()["game"] is None


def test_a_ready_made_game_seats_without_building(cast_web: Web) -> None:
    client, _ = cast_web.signed_up("ada")
    assert not new(client, "ready", world="autumn-1941")["refused"]
    assert new(client, "pick", name="Franklin D. Roosevelt")["reply"] == (
        "You are Franklin D. Roosevelt."
    )
    assert not new(client, "begin")["refused"]
    assert client.me()["game"]["phase"] == "play"
    assert new(client, "ready", world="salt-strait")["refused"]
