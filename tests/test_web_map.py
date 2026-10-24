"""The map in ``/api/me`` (realism, Batch 6b): only what the seat's own pages place,
as of when they placed it. A move in the truth reaches the map only with a page."""

import json
from pathlib import Path
from typing import Any

import pytest

from revisionist.core import events as ev
from revisionist.core.ids import EntityId, EventId, FeatureId, derived_id
from revisionist.core.visibility import PUBLIC
from revisionist.engine import campaign as cmp
from revisionist.engine.store import load_state
from revisionist.engine.web.realism_api import MapMark, MapPlace, SeatMap
from tests.test_web import Client, Web, web  # noqa: F401
from tests.test_web_realism import Realism, post, realism_of

CANARY = "CANARY-FLEET-TRUTH-3e9d"


@pytest.fixture
def fdr(web: Web) -> tuple[Realism, Client]:  # noqa: F811
    game = Realism(web, "autumn-1941")
    client = game.seat("ada", "roosevelt")
    game.open_play()
    game.first_tick()
    return game, client


def set_feature(game: Realism, key: str, **update: Any) -> None:
    """Change one feature in the truth, as a resolver would, without a page."""
    store = game.web.app.open()
    try:
        state = load_state(store, game.campaign)
        f = state.features[FeatureId(game.ids[key])]
        store.append_all(
            [
                ev.Event(
                    event_id=EventId(derived_id(game.campaign, "test-feature", key)),
                    campaign_id=game.campaign,
                    seq=state.last_seq + 1,
                    tick=state.tick,
                    scope=PUBLIC,
                    payload=ev.FeatureSet(feature=f.model_copy(update=update)),
                    versions=cmp.versions(),
                )
            ]
        )
    finally:
        store.close()


def marks(client: Client) -> dict[str, dict[str, Any]]:
    return {m["name"]: m for m in realism_of(client)["map"]["marks"]}


def test_the_map_shows_what_the_seats_pages_place(fdr: tuple[Realism, Client]) -> None:
    _, ada = fdr
    seen = marks(ada)
    assert set(seen) == {
        "Arctic convoys",
        "HX convoys",
        "Home Fleet",
        "SC convoys",
        "U-boat arm",
        "US Atlantic Fleet",
        "US Pacific Fleet",
    }
    atlantic = seen["US Atlantic Fleet"]
    assert (atlantic["side"], atlantic["certainty"], atlantic["side_name"]) == (
        "own",
        "known",
        "United States",
    )
    assert [p["name"] for p in atlantic["places"]] == ["Norfolk"]
    assert seen["U-boat arm"]["side"] == "other" and seen["U-boat arm"]["certainty"] == "reported"
    assert atlantic["source"] == "Harold R. Stark"
    # the page it came from opens
    status, page = post(ada, "read", doc_id=atlantic["document"])
    assert status == 200 and any(b["label"] == "US Atlantic Fleet" for b in page["body"])


def test_a_feature_no_page_places_is_nowhere_in_a_response(fdr: tuple[Realism, Client]) -> None:
    game, ada = fdr
    set_feature(game, "combined_fleet", name=CANARY)
    text = json.dumps(ada.me())
    for absent in (CANARY, "Combined Fleet", "Army Group Centre", "The Eastern Front"):
        assert absent not in text
    text = json.dumps(post(ada, "advance")[1]) + json.dumps(ada.me())
    assert CANARY not in text


def test_a_move_in_the_truth_waits_for_a_page(fdr: tuple[Realism, Client]) -> None:
    game, ada = fdr
    store = game.web.app.open()
    try:
        today = load_state(store, game.campaign).in_world_date
    finally:
        store.close()
    reykjavik = EntityId(game.ids["reykjavik"])
    set_feature(game, "us_atlantic_fleet", places=(reykjavik,), as_of=today)
    assert [p["name"] for p in marks(ada)["US Atlantic Fleet"]["places"]] == ["Norfolk"]
    post(ada, "advance")
    after = marks(ada)["US Atlantic Fleet"]
    assert [p["name"] for p in after["places"]] == ["Reykjavik"]


def test_map_words_carry_no_engine_words(fdr: tuple[Realism, Client]) -> None:
    _, ada = fdr
    m = realism_of(ada)["map"]
    for mark in m["marks"]:
        for text in (
            mark["name"],
            mark["side_name"],
            mark["source"],
            *(p["name"] for p in mark["places"]),
        ):
            assert "_" not in text and "truth" not in text.lower()


def test_the_fixture_map_has_the_snapshot_shape() -> None:
    fixture = Path(__file__).parents[1] / "web" / "test" / "fixtures" / "realism-seat.json"
    m = json.loads(fixture.read_text())["map"]
    assert set(m) == set(SeatMap.__annotations__)
    for p in m["places"]:
        assert set(p) == set(MapPlace.__annotations__)
    for mark in m["marks"]:
        assert set(mark) == set(MapMark.__annotations__)
        for p in mark["places"]:
            assert set(p) == set(MapPlace.__annotations__)
