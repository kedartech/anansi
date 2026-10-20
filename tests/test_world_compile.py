"""Layer 7: the world compiler turns a world file into a valid campaign or refuses it."""

from pathlib import Path

import pytest
import yaml

from revisionist.core import events as ev
from revisionist.core._base import Model
from revisionist.core.canary import canaries_hidden_from
from revisionist.core.fold import replay
from revisionist.core.ids import AssetId, CampaignId, EntityId, FactId, SeatId
from revisionist.core.ontology import (
    AssetKind,
    ClockKind,
    Controller,
    EntityKind,
    SeatKind,
)
from revisionist.core.project import project
from revisionist.core.settings import Settings
from revisionist.core.visibility import Visibility
from revisionist.core.world import (
    AgentDecl,
    AssetDecl,
    ClockDecl,
    EntityDecl,
    FactDecl,
    MeterDecl,
    RelationshipDecl,
    ScopeDecl,
    SeatDecl,
    WorldFile,
)
from revisionist.core.world_compile import WorldError, compile_world, object_id, resolve_keys
from tests import fixtures as fx

CANARY = "CANARY-VARDA-LEDGER-1a2b3c"
OTHER_CAMPAIGN = CampaignId("01ARZ3NDEKTSV4RRFFQ69G5FB0")
SALT_STRAIT = Path(__file__).resolve().parents[1] / "worlds" / "salt-strait.yaml"
DUO = SALT_STRAIT.with_name("salt-strait-duo.yaml")


def _world() -> WorldFile:
    covert_varda = ScopeDecl(visibility=Visibility.COVERT, known_by=("varda_seat",))
    return WorldFile(
        world="two-rivers",
        name="Two Rivers",
        entities=(
            EntityDecl(key="varda", kind=EntityKind.POLITY, name="Varda"),
            EntityDecl(key="ostrel", kind=EntityKind.POLITY, name="Ostrel"),
            EntityDecl(key="guild", kind=EntityKind.FACTION, name="River Guild", parent="varda"),
            EntityDecl(key="herald", kind=EntityKind.ORG, name="The Herald"),
            EntityDecl(key="mira", kind=EntityKind.PERSON, name="Mira Toll", parent="varda"),
            EntityDecl(key="aske", kind=EntityKind.PERSON, name="General Aske", parent="ostrel"),
        ),
        assets=(
            AssetDecl(
                key="varda_guard",
                kind=AssetKind.FORCE,
                name="Harbour Guard",
                tier=2,
                controller="varda",
            ),
            AssetDecl(
                key="varda_ledger",
                kind=AssetKind.NETWORK,
                name="The Ledger Clerks",
                controller="varda",
                scope=covert_varda,
            ),
        ),
        relationships=(
            RelationshipDecl(
                key="varda_ostrel",
                a="varda",
                b="ostrel",
                disposition=-1,
                leverage_tags=("salt_dependence",),
            ),
        ),
        meters=(MeterDecl(key="varda_stability", owner="varda", name="Stability", value=62),),
        clocks=(
            ClockDecl(
                key="varda_hold",
                kind=ClockKind.OBJECTIVE,
                name="Hold the strait",
                owner="varda_seat",
                segments_total=8,
                scope=covert_varda,
            ),
        ),
        facts=(
            FactDecl(
                key="ledger_skim",
                subject="varda",
                predicate="skims_tolls_through",
                obj="varda_ledger",
                exposure=2,
                scope=covert_varda,
                canary=CANARY,
            ),
        ),
        seats=(
            SeatDecl(
                key="varda_seat",
                kind=SeatKind.PLAYER,
                name="First Minister of Varda",
                embodies="varda",
                controller=Controller.HUMAN,
                objectives=("varda_hold",),
                agent=AgentDecl(
                    entity="mira",
                    name="Mira Toll",
                    role="chief of staff",
                    voice="dry",
                ),
            ),
            SeatDecl(key="guild_seat", kind=SeatKind.FACTION, name="River Guild", embodies="guild"),
            SeatDecl(key="herald_seat", kind=SeatKind.OUTLET, name="The Herald", embodies="herald"),
        ),
    )


def _edit[T: Model](items: tuple[T, ...], index: int, **update: object) -> tuple[T, ...]:
    return (*items[:index], items[index].model_copy(update=update), *items[index + 1 :])


def _problems(world: WorldFile) -> tuple[str, ...]:
    with pytest.raises(WorldError) as info:
        compile_world(world, fx.CAMPAIGN, fx.VERSIONS)
    return info.value.problems


def test_compiles_and_replays() -> None:
    events = compile_world(_world(), fx.CAMPAIGN, fx.VERSIONS)
    state = replay(events)
    assert len(state.entities) == 6
    assert len(state.assets) == 2
    assert len(state.seats) == 3
    assert state.tick == 0
    assert all(e.tick == 0 for e in events)
    assert state.seats[SeatId(object_id(fx.CAMPAIGN, "seat", "varda_seat"))].agent == EntityId(
        object_id(fx.CAMPAIGN, "entity", "mira")
    )


def test_creation_order_is_fixed() -> None:
    events = compile_world(_world(), fx.CAMPAIGN, fx.VERSIONS)
    assert [e.kind for e in events] == [
        "campaign_created",
        "settings_set",
        *["entity_created"] * 6,
        *["asset_created"] * 2,
        "relationship_set",
        "meter_created",
        "clock_created",
        "fact_asserted",
        *["seat_created"] * 3,
        "agent_assigned",
    ]


def test_compile_is_deterministic() -> None:
    first = compile_world(_world(), fx.CAMPAIGN, fx.VERSIONS)
    second = compile_world(_world(), fx.CAMPAIGN, fx.VERSIONS)
    assert first == second


def test_other_campaign_gives_other_ids() -> None:
    here = compile_world(_world(), fx.CAMPAIGN, fx.VERSIONS)
    there = compile_world(_world(), OTHER_CAMPAIGN, fx.VERSIONS)
    assert {e.event_id for e in here}.isdisjoint({e.event_id for e in there})
    assert object_id(fx.CAMPAIGN, "entity", "varda") != object_id(OTHER_CAMPAIGN, "entity", "varda")


def test_seq_is_dense_and_event_ids_unique() -> None:
    events = compile_world(_world(), fx.CAMPAIGN, fx.VERSIONS)
    assert [e.seq for e in events] == list(range(len(events)))
    assert len({e.event_id for e in events}) == len(events)


def test_first_events_are_campaign_then_settings() -> None:
    events = compile_world(_world(), fx.CAMPAIGN, fx.VERSIONS)
    assert isinstance(events[0].payload, ev.CampaignCreated)
    assert events[0].payload.world == "two-rivers"
    assert isinstance(events[1].payload, ev.SettingsSet)


def test_covert_objects_are_invisible_to_other_seats() -> None:
    state = replay(compile_world(_world(), fx.CAMPAIGN, fx.VERSIONS))
    guild = SeatId(object_id(fx.CAMPAIGN, "seat", "guild_seat"))
    view = project(state, guild)
    assert AssetId(object_id(fx.CAMPAIGN, "asset", "varda_ledger")) not in view.assets
    assert AssetId(object_id(fx.CAMPAIGN, "asset", "varda_guard")) in view.assets
    assert view.facts == {}
    assert view.clocks == {}


def test_covert_objects_are_visible_to_their_holder() -> None:
    state = replay(compile_world(_world(), fx.CAMPAIGN, fx.VERSIONS))
    view = project(state, SeatId(object_id(fx.CAMPAIGN, "seat", "varda_seat")))
    assert AssetId(object_id(fx.CAMPAIGN, "asset", "varda_ledger")) in view.assets
    assert len(view.facts) == 1
    assert len(view.clocks) == 1


def test_canary_is_hidden_from_other_seat() -> None:
    state = replay(compile_world(_world(), fx.CAMPAIGN, fx.VERSIONS))
    hidden = canaries_hidden_from(state, SeatId(object_id(fx.CAMPAIGN, "seat", "guild_seat")))
    assert CANARY in hidden
    assert hidden[CANARY] == object_id(fx.CAMPAIGN, "fact", "ledger_skim")


def test_seat_slots_default_from_settings() -> None:
    world = _world().model_copy(update={"settings": Settings(starting_slots=2)})
    state = replay(compile_world(world, fx.CAMPAIGN, fx.VERSIONS))
    assert {s.slots for s in state.seats.values()} == {2}


def test_explicit_seat_slots_are_kept() -> None:
    base = _world()
    world = base.model_copy(update={"seats": _edit(base.seats, 1, slots=1)})
    state = replay(compile_world(world, fx.CAMPAIGN, fx.VERSIONS))
    assert state.seats[SeatId(object_id(fx.CAMPAIGN, "seat", "guild_seat"))].slots == 1
    assert state.seats[SeatId(object_id(fx.CAMPAIGN, "seat", "varda_seat"))].slots == 3


def test_fact_object_key_resolves_to_id() -> None:
    state = replay(compile_world(_world(), fx.CAMPAIGN, fx.VERSIONS))
    fact = state.facts[FactId(object_id(fx.CAMPAIGN, "fact", "ledger_skim"))]
    assert fact.obj == object_id(fx.CAMPAIGN, "asset", "varda_ledger")


def test_fact_literal_object_stays_literal() -> None:
    base = _world()
    world = base.model_copy(update={"facts": _edit(base.facts, 0, obj="the hill passes")})
    state = replay(compile_world(world, fx.CAMPAIGN, fx.VERSIONS))
    fact = state.facts[FactId(object_id(fx.CAMPAIGN, "fact", "ledger_skim"))]
    assert fact.obj == "the hill passes"


def test_resolve_keys_matches_compiled_ids() -> None:
    world = _world()
    keys = resolve_keys(world, fx.CAMPAIGN)
    assert len(keys) == 6 + 2 + 1 + 1 + 1 + 1 + 3
    state = replay(compile_world(world, fx.CAMPAIGN, fx.VERSIONS))
    compiled = (
        set(state.entities)
        | set(state.assets)
        | set(state.relationships)
        | set(state.meters)
        | set(state.clocks)
        | set(state.facts)
        | set(state.seats)
    )
    assert set(keys.values()) == compiled


def test_resolve_keys_rejects_duplicates() -> None:
    world = _world().model_copy(
        update={"meters": (MeterDecl(key="varda", owner="varda", name="X", value=1),)}
    )
    with pytest.raises(WorldError) as info:
        resolve_keys(world, fx.CAMPAIGN)
    assert info.value.problems == ("meter varda: duplicate key",)


def test_duplicate_key_is_reported() -> None:
    world = _world().model_copy(
        update={"meters": (MeterDecl(key="varda", owner="varda", name="X", value=1),)}
    )
    assert "meter varda: duplicate key" in _problems(world)


def test_unknown_reference_is_reported() -> None:
    base = _world()
    world = base.model_copy(update={"assets": _edit(base.assets, 0, controller="nobody")})
    assert "asset varda_guard: unknown entity nobody" in _problems(world)


def test_wrong_section_reference_is_reported() -> None:
    base = _world()
    world = base.model_copy(update={"assets": _edit(base.assets, 0, controller="varda_seat")})
    assert "asset varda_guard: varda_seat is a seat, not a entity" in _problems(world)


def test_scope_needs_a_named_seat() -> None:
    base = _world()
    empty = ScopeDecl(visibility=Visibility.COVERT, known_by=())
    world = base.model_copy(update={"assets": _edit(base.assets, 1, scope=empty)})
    assert "asset varda_ledger: bad scope" in _problems(world)


def test_public_scope_declared_as_mapping_is_bad() -> None:
    base = _world()
    public = ScopeDecl(visibility=Visibility.PUBLIC, known_by=("varda_seat",))
    world = base.model_copy(update={"assets": _edit(base.assets, 1, scope=public)})
    assert "asset varda_ledger: bad scope" in _problems(world)


def test_agent_must_be_a_person() -> None:
    base = _world()
    agent = AgentDecl(entity="varda", name="Mira", role="chief of staff")
    world = base.model_copy(update={"seats": _edit(base.seats, 0, agent=agent)})
    assert "seat varda_seat: agent must be a person" in _problems(world)


def test_duplicate_canary_is_reported_on_second_fact() -> None:
    base = _world()
    twin = base.facts[0].model_copy(update={"key": "ledger_skim_twin"})
    world = base.model_copy(update={"facts": (*base.facts, twin)})
    assert _problems(world) == ("fact ledger_skim_twin: duplicate canary",)


def test_too_many_human_seats_is_reported() -> None:
    base = _world()
    second = base.seats[0].model_copy(
        update={"key": "south_seat", "embodies": "ostrel", "agent": None}
    )
    world = base.model_copy(
        update={"seats": (*base.seats, second), "settings": Settings(max_human_seats=1)}
    )
    assert "seat south_seat: too many human seats" in _problems(world)


def test_overfilled_clock_is_reported() -> None:
    base = _world()
    world = base.model_copy(update={"clocks": _edit(base.clocks, 0, segments_filled=9)})
    assert "clock varda_hold: overfilled" in _problems(world)


def test_meter_value_out_of_range_is_reported() -> None:
    base = _world()
    world = base.model_copy(update={"meters": _edit(base.meters, 0, value=150)})
    assert "meter varda_stability: out of range" in _problems(world)


def test_meter_with_empty_range_is_reported() -> None:
    base = _world()
    world = base.model_copy(update={"meters": _edit(base.meters, 0, lo=50, hi=50)})
    assert "meter varda_stability: out of range" in _problems(world)


def test_all_problems_are_reported_together() -> None:
    base = _world()
    world = base.model_copy(
        update={
            "assets": _edit(base.assets, 0, controller="nobody"),
            "meters": _edit(base.meters, 0, value=150),
        }
    )
    problems = _problems(world)
    assert problems == tuple(sorted(problems))
    assert problems == (
        "asset varda_guard: unknown entity nobody",
        "meter varda_stability: out of range",
    )


def test_bundled_salt_strait_compiles_and_replays() -> None:
    world = WorldFile.model_validate(yaml.safe_load(SALT_STRAIT.read_text()))
    events = compile_world(world, fx.CAMPAIGN, fx.VERSIONS)
    state = replay(events)

    polities = [e for e in state.entities.values() if e.kind is EntityKind.POLITY]
    assert len(polities) == 4
    ai_factions = [
        s
        for s in state.seats.values()
        if s.kind is SeatKind.FACTION and s.controller is Controller.AI
    ]
    assert len(ai_factions) == 3
    assert sum(s.controller is Controller.HUMAN for s in state.seats.values()) == 1
    assert sum(s.kind is SeatKind.OUTLET for s in state.seats.values()) == 1
    assert len(state.commitments) == 1

    # The brief says every canary is hidden from the human seat, but varda_skims_tolls is
    # scoped to varda_seat itself. Assert what the file actually does.
    varda = SeatId(object_id(fx.CAMPAIGN, "seat", "varda_seat"))
    canaries = {f.canary for f in state.facts.values() if f.canary}
    held = {f.canary for f in state.facts.values() if f.canary and f.scope.visible_to(varda)}
    assert len(canaries) == 4
    assert held == {"CANARY-VARDA-SKIM-55d0"}
    assert set(canaries_hidden_from(state, varda)) == canaries - held


def test_the_duo_world_is_salt_strait_with_ostrel_played_by_a_human() -> None:
    """Keeps worlds/salt-strait-duo.yaml in step with salt-strait.yaml."""
    solo = yaml.safe_load(SALT_STRAIT.read_text())
    duo = yaml.safe_load(DUO.read_text())
    assert (duo.pop("world"), duo.pop("name")) == ("salt-strait-duo", "Salt Strait (two players)")
    solo.pop("world"), solo.pop("name")
    agent = next(e for e in duo["entities"] if e["key"] == "sefa")
    duo["entities"].remove(agent)
    assert agent["kind"] == "person" and agent["parent"] == "ostrel"
    seat = next(s for s in duo["seats"] if s["key"] == "ostrel_seat")
    assert seat.pop("kind") == "player" and seat.pop("controller") == "human"
    assert seat.pop("agent")["entity"] == "sefa"
    solo_seat = next(s for s in solo["seats"] if s["key"] == "ostrel_seat")
    assert solo_seat.pop("kind") == "faction"
    goal = duo["goals"].pop()
    assert goal["seat"] == "ostrel_seat"  # a faction has no goal; the human does
    assert duo == solo

    campaign = CampaignId("01ARZ3NDEKTSV4RRFFQ69G5DU0")
    state = replay(compile_world(WorldFile.model_validate(duo_raw()), campaign, fx.VERSIONS))
    humans = sorted(s.name for s in state.seats.values() if s.controller.value == "human")
    assert humans == ["First Minister of Varda", "Ostrel War Council"]
    ostrel = next(s for s in state.seats.values() if s.name == "Ostrel War Council")
    varda = next(s for s in state.seats.values() if s.name == "First Minister of Varda")
    assert canaries_hidden_from(state, ostrel.seat_id) != canaries_hidden_from(state, varda.seat_id)
    assert "CANARY-OSTREL-HARROW-4c1e" in canaries_hidden_from(state, varda.seat_id)
    assert "CANARY-OSTREL-HARROW-4c1e" not in canaries_hidden_from(state, ostrel.seat_id)
    assert "CANARY-OSTREL-GOAL-2d90" in canaries_hidden_from(state, varda.seat_id)
    assert "CANARY-VARDA-GOAL-71a3" in canaries_hidden_from(state, ostrel.seat_id)


def duo_raw() -> dict[str, object]:
    loaded: dict[str, object] = yaml.safe_load(DUO.read_text())
    return loaded


# --- goals (Layer 10 item 1a) ----------------------------------------------------


def _duo_with(goals: list[dict[str, object]]) -> WorldFile:
    raw = duo_raw()
    raw["goals"] = goals
    return WorldFile.model_validate(raw)


def _goal_problems(goal: dict[str, object]) -> tuple[str, ...]:
    with pytest.raises(WorldError) as info:
        compile_world(_duo_with([goal]), OTHER_CAMPAIGN, fx.VERSIONS)
    return info.value.problems


TAKE = {"check": "controls_asset", "holder": "ostrel", "asset": "varda_saltworks"}


def test_goals_compile_with_their_own_private_objective_clock() -> None:
    world = WorldFile.model_validate(duo_raw())
    state = replay(compile_world(world, OTHER_CAMPAIGN, fx.VERSIONS))
    ids = resolve_keys(world, OTHER_CAMPAIGN)
    varda, ostrel = SeatId(ids["varda_seat"]), SeatId(ids["ostrel_seat"])
    goals = {goal.seat_id: goal for goal in state.goals.values()}
    assert set(goals) == {varda, ostrel}
    for seat, goal in goals.items():
        clock = state.clocks[goal.clock_id]
        assert clock.kind is ClockKind.OBJECTIVE and clock.owner == seat
        assert goal.clock_id in state.seats[seat].objectives
        assert clock.scope.known_by == goal.scope.known_by == {seat}
        assert goal.kind.value == "undoable" and goal.met_tick is None
    assert state.clocks[goals[varda].clock_id].segments_total == 2
    assert state.clocks[goals[ostrel].clock_id].segments_total == 1
    assert goals[ostrel].condition.holder == EntityId(ids["ostrel"])  # type: ignore[union-attr]


def test_a_goal_is_refused_for_a_faction_and_a_second_goal_for_one_seat() -> None:
    problems = _goal_problems(
        {"key": "kesh_goal", "seat": "kesh_seat", "text": "t", "condition": TAKE}
    )
    assert problems == ("goal kesh_goal: only a player seat has a goal",)
    with pytest.raises(WorldError, match="already has a goal"):
        compile_world(
            _duo_with(
                [
                    {"key": "a", "seat": "ostrel_seat", "text": "t", "condition": TAKE},
                    {"key": "b", "seat": "ostrel_seat", "text": "t", "condition": TAKE},
                ]
            ),
            OTHER_CAMPAIGN,
            fx.VERSIONS,
        )


def test_a_goal_names_real_keys_of_the_right_kind() -> None:
    condition = {
        "check": "any_of",
        "conditions": [
            {"check": "holds_office", "holder": "ostrel", "office": "varda_treasury"},
            {"check": "meter_at", "meter": "nowhere", "op": "ge", "value": 1},
            {"check": "entity_is", "entity": "varda_guard", "status": "dead"},
        ],
    }
    problems = _goal_problems(
        {"key": "g", "seat": "ostrel_seat", "text": "t", "condition": condition}
    )
    assert problems == (
        "goal g: unknown meter nowhere",
        "goal g: varda_guard is a asset, not a entity",
        "goal g: varda_treasury is not an office",
    )


def test_a_goal_already_met_at_the_start_is_refused() -> None:
    held = {"check": "controls_asset", "holder": "varda", "asset": "varda_saltworks"}
    problems = _goal_problems({"key": "g", "seat": "ostrel_seat", "text": "t", "condition": held})
    assert problems == ("goal g: already met at tick 0",)


def test_goal_canaries_are_unique_across_facts() -> None:
    problems = _goal_problems(
        {
            "key": "g",
            "seat": "ostrel_seat",
            "text": "t",
            "condition": TAKE,
            "canary": "CANARY-OSTREL-HARROW-4c1e",
        }
    )
    assert problems == ("goal g: duplicate canary",)
