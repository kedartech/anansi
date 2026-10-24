"""The autumn-1941 realism world: it compiles, nobody is pre-seated, the eight
playable cards are the roster, and every figure is cited or a gap."""

from revisionist.core.fold import replay
from revisionist.core.ids import CampaignId
from revisionist.core.world import WorldFile
from revisionist.core.world_compile import check_history, compile_world
from revisionist.engine import campaign as cmp

CAMPAIGN = CampaignId("01ARZ3NDEKTSV4RRFFQ69G5FAV")

PLAYABLE = {
    "roosevelt",
    "churchill",
    "stalin",
    "hitler",
    "konoe",
    "tojo",
    "mussolini",
    "chiang",
}

# Every value in the Phase 0b source note (SOURCES.md); a quantity may hold only these.
SOURCED_VALUES = {
    203,  # House, draft extension, yeas
    202,  # nays
    259,  # House, arming merchant ships
    138,
    50,  # Senate, Neutrality revision
    37,
    212,  # House concurrence
    194,
    66,  # Senate, 77th Congress
    28,
    2,
    267,  # House, 77th Congress
    162,
    5,
    5_985_000_000,  # second Lend-Lease appropriation, USD
    209_835,  # Allied merchant losses to U-boats, GRT: Sep, Oct, Nov
    176_622,
    120_541,
    19_433,  # US aircraft production, 1941
    103,  # US merchant ship deliveries, 1941
    1_159_765,  # DWT
    30,  # U-boats operational, Jul-Sep average
    55,  # Gallup, survey 6/26-7/1: about right
    57,  # Gallup 8 Oct: about right, too far, not far enough
    27,
    16,
    26,  # Gallup 22 Nov: for war on Germany, against
    63,
}


def _world() -> WorldFile:
    return cmp.load_world(cmp.world_path("autumn-1941"))


def test_it_compiles_with_no_seat_and_a_clean_draft() -> None:
    world = _world()
    assert world.ruleset == "realism" and world.seats == ()
    assert check_history(world) == []
    state = replay(compile_world(world, CAMPAIGN, cmp.versions()))
    assert not state.seats and state.in_world_date == world.start_date


def test_the_eight_playable_cards_are_the_roster() -> None:
    world = _world()
    playable = {c.person for c in world.cards if c.playable}
    assert len(playable) == 8 == len(world.roster)
    assert playable == PLAYABLE == {c.entity for c in world.roster}


def test_every_quantity_is_cited_a_gap_or_computed() -> None:
    world = _world()
    declared = {s.key for s in world.sources}
    for q in world.quantities:
        if q.computed:
            assert q.value is None and q.source is None and not q.gap, q.key
        elif q.gap:
            assert q.value is None and q.source is None, q.key
        else:
            assert q.source in declared and q.value is not None, q.key


def test_no_quantity_holds_a_value_outside_the_source_note() -> None:
    values = {q.key: q.value for q in _world().quantities if q.value is not None}
    stray = {k: v for k, v in values.items() if v not in SOURCED_VALUES}
    assert not stray


def test_every_card_lever_names_a_resolver_and_a_card_pipeline() -> None:
    for c in _world().cards:
        assert all(lv.pipeline in c.pipelines for lv in c.levers), c.person
    assert all(2 <= len(c.levers) <= 6 for c in _world().cards if c.playable)
