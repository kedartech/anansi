"""The map's engine half (realism, Batch 6a): places with positions, features that
are truth, feeds that sight them, and the movement resolver. No seat sees a
feature except as a document line."""

from datetime import date, timedelta
from typing import Any

import pytest

from revisionist.core.fold import FoldError, replay, state_hash
from revisionist.core.ids import CampaignId, DocumentId, EntityId, FeatureId
from revisionist.core.ontology import Entity, EntityKind
from revisionist.core.project import project
from revisionist.core.realism.document import BodyField, DocumentTruth
from revisionist.core.realism.geography import FeatureKind, km_between
from revisionist.core.realism.pipeline import OrderStatus
from revisionist.core.realism.resolvers import movement
from revisionist.core.report import leak_report
from revisionist.core.state import State
from revisionist.core.world import WorldFile
from revisionist.core.world_compile import WorldError, compile_world, resolve_keys
from revisionist.engine import campaign as cmp
from revisionist.engine import realism_tick
from revisionist.engine.store import MemoryStore, load_state
from revisionist.engine.web import realism_snapshot
from tests.test_realism_resolvers import Rig, rig

CAMPAIGN = CampaignId("01ARZ3NDEKTSV4RRFFQ69G5FAV")
NAVAL = [date(1941, 9, 3), date(1941, 9, 5)]


def feature(r: Rig, key: str) -> Any:
    return r.state.features[FeatureId(r.ids[key])]


def place(r: Rig, key: str) -> EntityId:
    return EntityId(r.ids[key])


def day(r: Rig, today: date) -> None:
    r.fold_changes(movement.on_day(r.state, today))


def move(r: Rig, person: str, lever: str, pipeline: str, days: list[date], **p: object) -> Any:
    order = r.enact(person, lever, pipeline, days[0] - timedelta(days=2), days, **p)
    r.fold_changes(movement.resolve(r.state, order))
    return order


# --- the world -----------------------------------------------------------------------


def test_the_1941_map_compiles_with_its_forces_fronts_and_routes() -> None:
    r = Rig()
    kinds = sorted(f.kind.value for f in r.state.features.values())
    assert kinds.count("fleet") == 5 and kinds.count("army") == 2
    assert kinds.count("front") == 1 and kinds.count("route") == 3
    for f in r.state.features.values():
        for p in f.places:
            assert r.state.entities[p].kind is EntityKind.LOCATION
            assert r.state.entities[p].at is not None


@pytest.mark.parametrize(
    ("change", "said"),
    [
        (
            {
                "features": [
                    {
                        "key": "f",
                        "kind": "fleet",
                        "name": "F",
                        "side": "a",
                        "places": ["p", "q"],
                        "gap": True,
                    }
                ]
            },
            "a force is at one place",
        ),
        (
            {
                "features": [
                    {"key": "f", "kind": "front", "name": "F", "places": ["p"], "gap": True}
                ]
            },
            "joins two places",
        ),
        (
            {
                "features": [
                    {"key": "f", "kind": "fleet", "name": "F", "places": ["p"], "gap": True}
                ]
            },
            "a force belongs to a side",
        ),
        (
            {
                "features": [
                    {
                        "key": "f",
                        "kind": "fleet",
                        "name": "F",
                        "side": "a",
                        "places": ["nowhere"],
                        "gap": True,
                    }
                ]
            },
            "not a place with a position",
        ),
        (
            {
                "features": [
                    {
                        "key": "f",
                        "kind": "fleet",
                        "name": "F",
                        "side": "a",
                        "places": ["p"],
                        "gap": True,
                        "legs": [{"since": "1900-01-01", "places": ["q"]}],
                    }
                ]
            },
            "legs run forward",
        ),
        (
            {
                "features": [
                    {
                        "key": "f",
                        "kind": "fleet",
                        "name": "F",
                        "side": "p",
                        "places": ["p"],
                        "gap": True,
                    }
                ]
            },
            "not a polity or body",
        ),
        (
            {"entities_extra": {"key": "x", "kind": "polity", "name": "X", "at": [1, 2]}},
            "only a place has a position",
        ),
        (
            {
                "ruleset": "legacy",
                "features": [
                    {
                        "key": "f",
                        "kind": "fleet",
                        "name": "F",
                        "side": "a",
                        "places": ["p"],
                        "gap": True,
                    }
                ],
            },
            "only a realism world has features",
        ),
    ],
)
def test_a_bad_feature_is_refused(change: dict[str, Any], said: str) -> None:
    raw: dict[str, Any] = {
        "world": "t",
        "name": "T",
        "ruleset": "realism",
        "start_date": "1941-09-01",
        "entities": [
            {"key": "a", "kind": "polity", "name": "A"},
            {"key": "p", "kind": "location", "name": "P", "at": [10, 10]},
            {"key": "q", "kind": "location", "name": "Q", "at": [11, 11]},
            {"key": "nowhere", "kind": "location", "name": "Nowhere"},
        ],
    }
    extra = change.pop("entities_extra", None)
    if extra:
        raw["entities"].append(extra)
    if change.get("ruleset") == "legacy":
        raw.pop("start_date")
    raw.update(change)
    with pytest.raises(WorldError) as caught:
        compile_world(WorldFile.model_validate(raw), CAMPAIGN, cmp.versions())
    assert any(said in p for p in caught.value.problems), caught.value.problems


def test_a_movement_lever_offers_only_forces_and_placed_places() -> None:
    world = cmp.load_world(cmp.world_path("autumn-1941"))
    cards = list(world.cards)
    fdr = next(c for c in cards if c.person == "roosevelt")
    lever = next(lv for lv in fdr.levers if lv.resolver == "movement")
    force, to = lever.params
    bad = lever.model_copy(
        update={
            "params": (
                force.model_copy(update={"choices": ("eastern_front",), "choice_labels": ()}),
                to.model_copy(update={"choices": ("washington", "usa"), "choice_labels": ()}),
            )
        }
    )
    cards[cards.index(fdr)] = fdr.model_copy(
        update={"levers": tuple(bad if lv is lever else lv for lv in fdr.levers)}
    )
    with pytest.raises(WorldError) as caught:
        compile_world(world.model_copy(update={"cards": tuple(cards)}), CAMPAIGN, cmp.versions())
    problems = caught.value.problems
    assert any("eastern_front is not a force" in p for p in problems)
    assert any("usa is not a place" in p for p in problems)
    assert not any("washington" in p for p in problems)


def test_a_feature_may_not_stand_on_something_that_is_not_a_place() -> None:
    r = Rig()
    fleet = feature(r, "home_fleet")
    with pytest.raises(FoldError, match="is not a place"):
        r.fold_changes((fleet.model_copy(update={"places": (place(r, "uk"),)}),))
    with pytest.raises(FoldError, match="cannot go back"):
        Rig().fold_changes((fleet.model_copy(update={"as_of": date(1941, 8, 1)}),))


# --- movement --------------------------------------------------------------------------


def test_an_order_sends_a_fleet_at_its_pace_and_it_arrives() -> None:
    r = rig()
    move(
        r,
        "roosevelt",
        "move_fleet",
        "naval_order",
        NAVAL,
        force="us_atlantic_fleet",
        to="reykjavik",
    )
    fleet = feature(r, "us_atlantic_fleet")
    km = km_between((36.9, -76.3), (64.1, -21.9))
    assert km == 4667
    assert fleet.places == (place(r, "norfolk"),) and fleet.bound_for == place(r, "reykjavik")
    assert fleet.arrives == NAVAL[-1] + timedelta(days=11)  # 4,667 km at 450 a day
    day(r, fleet.arrives - timedelta(days=1))
    assert feature(r, "us_atlantic_fleet").places == (place(r, "norfolk"),)
    day(r, fleet.arrives)
    there = feature(r, "us_atlantic_fleet")
    assert there.places == (place(r, "reykjavik"),) and there.bound_for is None
    assert there.as_of == fleet.arrives


def test_an_order_for_another_sides_force_changes_nothing() -> None:
    r = rig()
    order = r.enact(
        "roosevelt",
        "move_fleet",
        "naval_order",
        date(1941, 9, 1),
        NAVAL,
        force="home_fleet",
        to="reykjavik",
    )
    assert movement.resolve(r.state, order) == ()


def test_a_refused_order_moves_nothing_and_a_force_sent_home_halts() -> None:
    r = rig()
    refused = r.draft(
        "roosevelt",
        "move_fleet",
        "naval_order",
        date(1941, 9, 1),
        force="us_atlantic_fleet",
        to="argentia",
    )
    assert movement.resolve(r.state, r.settle(refused, OrderStatus.REFUSED)) == ()
    move(
        r, "roosevelt", "move_fleet", "naval_order", NAVAL, force="us_atlantic_fleet", to="argentia"
    )
    assert feature(r, "us_atlantic_fleet").bound_for == place(r, "argentia")
    later = [d + timedelta(days=3) for d in NAVAL]
    move(
        r, "roosevelt", "move_fleet", "naval_order", later, force="us_atlantic_fleet", to="norfolk"
    )
    halted = feature(r, "us_atlantic_fleet")
    assert halted.places == (place(r, "norfolk"),) and halted.bound_for is None


def test_history_moves_a_force_and_an_order_drops_what_history_had_left() -> None:
    r = rig(seat="hitler")
    day(r, date(1941, 10, 7))
    assert feature(r, "army_group_centre").places == (place(r, "vyazma"),)
    assert feature(r, "eastern_front").places[1] == place(r, "smolensk")
    day(r, date(1941, 9, 26))  # an earlier day changes nothing already passed
    day(r, date(1941, 10, 8))
    front = feature(r, "eastern_front")
    assert [r.state.entities[p].name for p in front.places] == [
        "Leningrad",
        "Smolensk",
        "Poltava",
        "Odessa",
    ]
    days = [date(1941, 10, 10), date(1941, 10, 12)]
    move(r, "hitler", "move_army", "okw_directive", days, force="army_group_centre", to="moscow")
    group = feature(r, "army_group_centre")
    assert group.legs == () and group.arrives == days[-1] + timedelta(days=9)  # 218 km at 25
    day(r, date(1941, 10, 24))  # history had it at Mozhaisk today; the order holds
    assert feature(r, "army_group_centre").places == (place(r, "moscow"),)


def test_a_collapsed_stretch_moves_as_a_slow_one() -> None:
    fast, slow = rig(seat="hitler"), rig(seat="hitler")
    day(fast, date(1941, 10, 30))
    d = date(1941, 9, 2)
    while d <= date(1941, 10, 30):
        day(slow, d)
        d += timedelta(days=1)
    for key in ("army_group_centre", "soviet_western_front", "eastern_front"):
        assert feature(fast, key).places == feature(slow, key).places
        assert feature(fast, key).legs == feature(slow, key).legs == ()


# --- sighting -----------------------------------------------------------------------------


def ticked(seat: str, ticks: int = 2) -> tuple[MemoryStore, State]:
    store = MemoryStore()
    world = cmp.load_world(cmp.world_path("autumn-1941"))
    store.append_all(compile_world(world, CAMPAIGN, cmp.versions()))
    realism_tick.seat_player(store, CAMPAIGN, EntityId(resolve_keys(world, CAMPAIGN)[seat]))
    for _ in range(ticks):
        realism_tick.run_realism_tick(store, CAMPAIGN, versions=cmp.versions())
    return store, load_state(store, CAMPAIGN)


def test_a_feed_places_what_it_sights_in_words_and_in_its_sight() -> None:
    _, state = ticked("hitler")
    seat = next(iter(state.seats))
    view = project(state, seat)
    lines = [b for d in view.documents.values() for b in d.body if b.sight is not None]
    group = next(b for b in lines if b.label == "Army Group Centre")
    assert group.value == "Smolensk" and group.unit == ""
    assert group.sight is not None and group.sight.kind is FeatureKind.ARMY
    front = next(b for b in lines if b.label == "The Eastern Front")
    assert front.value == " \N{EN DASH} ".join(["Leningrad", "Smolensk", "Kiev", "Odessa"])
    assert len(front.sight.places) == 4  # type: ignore[union-attr]
    # Words, not figures: a position never reaches the situation board.
    labels = {f["label"] for f in realism_snapshot.situation(view)["figures"]}
    assert not labels & {b.label for b in lines}


def test_a_seat_never_sees_a_feature_its_paper_does_not_sight() -> None:
    store, state = ticked("hitler")
    seat = next(iter(state.seats))
    dumped = project(state, seat).model_dump_json()
    assert "features" not in dumped
    sighted = {b.label for d in project(state, seat).documents.values() for b in d.body if b.sight}
    for f in state.features.values():
        if f.name not in sighted:
            assert f.name not in dumped, f.name
    assert "US Pacific Fleet" not in dumped and "Combined Fleet" not in dumped
    assert leak_report(state, store.read(CAMPAIGN)) == []


def test_the_truth_behind_a_sighting_stays_in_the_state() -> None:
    _, state = ticked("roosevelt")
    sighted = [d for d in state.documents.values() if d.truth.sightings]
    assert sighted
    for doc in sighted:
        lines = [b for b in doc.view.body if b.sight is not None]
        assert [s.places for s in doc.truth.sightings] == [b.sight.places for b in lines]  # type: ignore[union-attr]


def test_replaying_a_moving_world_gives_the_same_state() -> None:
    a, sa = ticked("hitler", ticks=4)
    _, sb = ticked("hitler", ticks=4)
    assert state_hash(sa) == state_hash(sb)
    assert state_hash(replay(a.read(CAMPAIGN))) == state_hash(sa)
    assert sa.in_world_date is not None and sa.in_world_date >= date(1941, 10, 7)
    group = next(f for f in sa.features.values() if f.name == "Army Group Centre")
    assert sa.entities[group.places[0]].name != "Smolensk"  # the walk ran history's legs


# --- logs from before the map ---------------------------------------------------------------


def test_the_new_fields_are_left_out_when_empty() -> None:
    """Campaigns logged before Batch 6 keep their state hash."""
    e = Entity(entity_id=EntityId("E"), kind=EntityKind.PERSON, name="Ada")
    assert "at" not in e.model_dump()
    assert "sight" not in BodyField(label="x", value=1).model_dump()
    assert "sightings" not in DocumentTruth(doc_id=DocumentId("D")).model_dump()
    bare = next(f for f in Rig().state.feeds.values() if not f.sights)
    assert "sights" not in bare.model_dump()
    assert "features" not in State(campaign_id=CAMPAIGN).model_dump()


# --- the seat's map (the snapshot) ----------------------------------------------------------


def test_the_seat_map_ages_and_sides_its_marks() -> None:
    _, state = ticked("hitler")
    view = project(state, next(iter(state.seats)))
    fresh = {m["name"]: m for m in realism_snapshot.seat_map(view)["marks"]}  # type: ignore[index]
    assert fresh["Army Group Centre"]["certainty"] == "known"
    assert fresh["Soviet Western Front"]["certainty"] == "reported"
    assert fresh["The Eastern Front"]["side"] == "none"
    assert fresh["The Eastern Front"]["side_name"] == ""
    assert view.in_world_date is not None
    later = view.model_copy(
        update={
            "in_world_date": view.in_world_date + timedelta(days=realism_snapshot.STALE_DAYS + 3)
        }
    )
    aged = realism_snapshot.seat_map(later)
    assert aged is not None and {m["certainty"] for m in aged["marks"]} == {"stale"}


def test_a_world_without_positions_has_no_map() -> None:
    r = Rig("realism-toy")
    r.seat("ada")
    assert realism_snapshot.seat_map(project(r.state, r.seats["ada"])) is None
