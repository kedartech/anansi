"""The character rule in code (spec §5.5): who may be offered a character."""

from pathlib import Path

import yaml

from revisionist.core.fold import replay
from revisionist.core.ids import EntityId, SeatId
from revisionist.core.ontology import AssetKind, AssetStatus, Character, CharacterSide, EntityKind
from revisionist.core.roster import nearest, problems, qualifies
from revisionist.core.state import State
from revisionist.core.world import AssetDecl, EntityDecl, WorldFile
from revisionist.core.world_compile import compile_world
from tests import fixtures as fx

SALT_STRAIT = Path(__file__).resolve().parents[1] / "worlds" / "salt-strait.yaml"
WAR = {
    "key": "war",
    "kind": "rivalry",
    "name": "War",
    "prize": "the strait",
    "sides": ["varda", "ostrel"],
    "countdown_days": 30,
}


def _state(
    *,
    assets: tuple[AssetDecl, ...] = (),
    people: tuple[EntityDecl, ...] = (),
    contests: bool = True,
) -> State:
    raw: dict[str, object] = yaml.safe_load(SALT_STRAIT.read_text())
    raw["start_date"] = "1500-03-01"
    raw["contests"] = [WAR] if contests else []
    world = WorldFile.model_validate(raw)
    world = world.model_copy(
        update={
            "assets": (*world.assets, *assets),
            "entities": (*world.entities, *people),
        }
    )
    return replay(compile_world(world, fx.CAMPAIGN, fx.VERSIONS))


def _force(key: str, controller: str, tier: int = 2) -> AssetDecl:
    return AssetDecl(key=key, kind=AssetKind.FORCE, name=key, tier=tier, controller=controller)


def _id(state: State, name: str) -> EntityId:
    return next(e.entity_id for e in state.entities.values() if e.name == name)


def _roster(state: State, *names: str, claimed: tuple[str, ...] = ()) -> State:
    chars = {}
    for name in names:
        who = _id(state, name)
        claimed_by = SeatId("seat-claiming") if name in claimed else None
        chars[who] = Character(
            entity_id=who, pitch="p", reach="r", composite=False, claimed_by=claimed_by
        )
    return state.model_copy(update={"roster": chars})


def test_a_tier_two_force_under_a_side_qualifies() -> None:
    state = _state(assets=(_force("aske_host", "general_aske"),))
    assert qualifies(state, _id(state, "General Aske"))


def test_money_information_and_facilities_alone_do_not_qualify() -> None:
    assets = tuple(
        AssetDecl(key=f"mira_{k.value}", kind=k, name=k.value, tier=3, controller="mira")
        for k in (AssetKind.WEALTH, AssetKind.INFO, AssetKind.FACILITY)
    )
    state = _state(assets=assets)
    assert not qualifies(state, _id(state, "Mira Toll"))


def test_tier_one_does_not_qualify() -> None:
    state = _state(assets=(_force("aske_band", "general_aske", tier=1),))
    assert not qualifies(state, _id(state, "General Aske"))


def test_a_lost_asset_does_not_qualify() -> None:
    state = _state(assets=(_force("aske_host", "general_aske"),))
    host = next(a for a in state.assets.values() if a.name == "aske_host")
    lost = host.model_copy(update={"status": AssetStatus.LOST})
    state = state.model_copy(update={"assets": {host.asset_id: lost}})
    assert not qualifies(state, _id(state, "General Aske"))


def test_a_person_outside_every_contest_does_not_qualify() -> None:
    state = _state(assets=(_force("abbess_host", "abbess_ren"),))
    assert not qualifies(state, _id(state, "Abbess Ren"))


def test_with_no_contests_any_person_with_such_an_asset_qualifies() -> None:
    state = _state(assets=(_force("abbess_host", "abbess_ren"),), contests=False)
    assert not state.contests
    assert qualifies(state, _id(state, "Abbess Ren"))
    assert not qualifies(state, _id(state, "Mira Toll"))


def test_nearest_prefers_the_same_parent() -> None:
    teo = EntityDecl(key="teo", kind=EntityKind.PERSON, name="Lord Teo", parent="ostrel")
    state = _state(
        assets=(
            _force("aske_host", "general_aske"),
            _force("teo_host", "teo"),
            _force("m", "mira"),
        ),
        people=(teo,),
    )
    state = _roster(state, "General Aske", "Lord Teo", "Mira Toll")
    assert nearest(state, _id(state, "General Aske")) == _id(state, "Lord Teo")


def test_nearest_falls_back_to_a_shared_contest() -> None:
    state = _state(assets=(_force("aske_host", "general_aske"), _force("m", "mira")))
    state = _roster(state, "General Aske", "Mira Toll")
    assert nearest(state, _id(state, "Mira Toll")) == _id(state, "General Aske")


def test_nearest_skips_the_claimed_and_returns_none_when_no_one_qualifies() -> None:
    teo = EntityDecl(key="teo", kind=EntityKind.PERSON, name="Lord Teo", parent="ostrel")
    state = _state(
        assets=(
            _force("aske_host", "general_aske"),
            _force("teo_host", "teo"),
            _force("m", "mira"),
        ),
        people=(teo,),
    )
    state = _roster(state, "General Aske", "Lord Teo", "Mira Toll", claimed=("General Aske",))
    assert nearest(state, _id(state, "Lord Teo")) == _id(state, "Mira Toll")
    assert nearest(state, _id(state, "Mira Toll")) == _id(state, "Lord Teo")
    empty = _roster(state, "Mira Toll", "Abbess Ren")
    assert nearest(empty, _id(empty, "Mira Toll")) is None


def test_nearest_for_an_unknown_entity_is_the_first_qualified_by_name() -> None:
    state = _state(assets=(_force("aske_host", "general_aske"), _force("m", "mira")))
    state = _roster(state, "Mira Toll", "General Aske")
    assert nearest(state, EntityId("nobody")) == _id(state, "General Aske")


def test_problems_name_each_character_who_fails_in_name_order() -> None:
    state = _state(assets=(_force("aske_host", "general_aske"),))
    state = _roster(state, "Mira Toll", "General Aske", "Abbess Ren")
    assert problems(state) == [
        "character Abbess Ren: controls no force, office or network of tier 2+ in a contest",
        "character Mira Toll: controls no force, office or network of tier 2+ in a contest",
    ]


def test_problems_are_empty_without_contests() -> None:
    state = _state(contests=False)
    state = _roster(state, "Mira Toll")
    assert problems(state) == []


def _declared(state: State, name: str, side: str | None) -> State:
    who = _id(state, name)
    contest_id = next(iter(state.contests))
    sides = (CharacterSide(contest_id=contest_id, side=_id(state, side)),) if side else ()
    char = Character(entity_id=who, pitch="p", reach="r", composite=False, sides=sides)
    return state.model_copy(update={"roster": {who: char}})


def test_a_declared_side_counts_for_a_person_under_no_side() -> None:
    gov = EntityDecl(key="gov", kind=EntityKind.PERSON, name="Governor Border")
    state = _state(
        assets=(
            AssetDecl(key="gov_office", kind=AssetKind.OFFICE, name="o", tier=3, controller="gov"),
        ),
        people=(gov,),
    )
    assert not qualifies(_declared(state, "Governor Border", None), _id(state, "Governor Border"))
    declared = _declared(state, "Governor Border", "Varda")
    assert qualifies(declared, _id(state, "Governor Border"))
    assert problems(declared) == []


def test_a_declared_side_still_needs_a_qualifying_asset() -> None:
    gov = EntityDecl(key="gov", kind=EntityKind.PERSON, name="Governor Border")
    state = _state(
        assets=(
            AssetDecl(key="gov_ledger", kind=AssetKind.WEALTH, name="w", tier=4, controller="gov"),
        ),
        people=(gov,),
    )
    assert not qualifies(
        _declared(state, "Governor Border", "Varda"), _id(state, "Governor Border")
    )
