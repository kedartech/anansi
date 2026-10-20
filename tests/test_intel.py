"""Layer 10 item 1b: intelligence on secret goals (README §7, decision 37).

A rival's goal is learned one piece at a time, by investigation, from engine
code. A partial success can mislead, a forgery can plant a decoy, and nothing
about a goal reaches a person who holds no piece of it.
"""

from collections.abc import Callable, Sequence

import pytest

from revisionist.core import delta as d
from revisionist.core import events as ev
from revisionist.core import goals as g
from revisionist.core import intel
from revisionist.core.canary import scan_humans, surfaces_from_log
from revisionist.core.fold import apply_delta, replay
from revisionist.core.ids import (
    CampaignId,
    ClockId,
    CommitmentId,
    DeltaId,
    GoalId,
    IntentId,
    SeatId,
)
from revisionist.core.intent import Intent, Verb
from revisionist.core.ontology import (
    Clause,
    ClauseKind,
    Clock,
    ClockKind,
    Commitment,
    EntityStatus,
)
from revisionist.core.project import project
from revisionist.core.referee import rate, roll, seed_for
from revisionist.core.resolution import OutcomeTier
from revisionist.core.state import State, View
from revisionist.core.visibility import private_to
from revisionist.core.world import WorldFile
from revisionist.core.world_compile import compile_world, resolve_keys
from revisionist.engine.agent import counterfactual_diff
from revisionist.engine.llm import Reply, Request
from revisionist.engine.providers import RecordingDeliverer, ScriptedResolver
from revisionist.engine.store import MemoryStore, load_state
from revisionist.engine.tick import Narration, Providers, file_intent, run_tick
from tests import fixtures as fx
from tests.test_world_compile import duo_raw

SOUTH_GOAL = GoalId("01ARZ3NDEKTSV4RRFFQ69G0AS0")
SOUTH_CLOCK = ClockId("01ARZ3NDEKTSV4RRFFQ69G0AS1")
TAKE_ARMY = g.ControlsAsset(holder=fx.E_SOUTH, asset=fx.A_NORTH_ARMY)
SEED = "seed"


def _south_goal(state: State) -> State:
    clock = Clock(
        clock_id=SOUTH_CLOCK,
        kind=ClockKind.OBJECTIVE,
        name="Goal",
        owner=fx.SOUTH,
        segments_total=1,
        scope=private_to(fx.SOUTH),
    )
    goal = g.Goal(
        goal_id=SOUTH_GOAL,
        seat_id=fx.SOUTH,
        text="Take the Northern Levies.",
        condition=TAKE_ARMY,
        kind=g.goal_kind(TAKE_ARMY),
        clock_id=SOUTH_CLOCK,
        scope=private_to(fx.SOUTH),
    )
    return state.model_copy(
        update={"clocks": {**state.clocks, SOUTH_CLOCK: clock}, "goals": {SOUTH_GOAL: goal}}
    )


@pytest.fixture
def state() -> State:
    return _south_goal(replay(fx.creation_events()))


def _investigate(n: int = 1, actor: SeatId = fx.NORTH, target: str = fx.E_SOUTH) -> Intent:
    return Intent(
        intent_id=IntentId(f"01ARZ3NDEKTSV4RRFFQ69V{n:04d}"),
        actor=actor,
        verb=Verb.INVESTIGATE,
        targets=(fx.EntityId(target),),
        scope=private_to(actor),
        filed_tick=0,
    )


def _learn(state: State, outcome: OutcomeTier, seed: str = SEED) -> tuple[State, d.FactAssert]:
    n = len(intel.held(state, fx.NORTH, fx.E_SOUTH)) + 1
    found = intel.piece(state, _investigate(n), outcome, seed)
    assert found is not None
    return apply_delta(state, found, state.tick), found


# --- pieces, in order ------------------------------------------------------------


def test_pieces_come_in_a_fixed_order_from_vague_to_exact(state: State) -> None:
    got = []
    for _ in intel.PIECES:
        state, found = _learn(state, OutcomeTier.SUCCESS)
        assert found.truth and found.scope == private_to(fx.NORTH)
        assert found.subject == fx.E_SOUTH
        got.append((found.predicate, found.obj))
    assert got == [
        ("goal_exists", intel.HAS_AIM),
        ("goal_domain", "force"),
        ("goal_object", "Northern Levies"),
        ("goal_condition", "South controls Northern Levies"),
    ]
    assert intel.piece(state, _investigate(9), OutcomeTier.SUCCESS, SEED) is None  # nothing left


def test_a_failure_or_a_seat_without_a_goal_yields_nothing(state: State) -> None:
    assert intel.piece(state, _investigate(), OutcomeTier.FAIL_COMPLICATION, SEED) is None
    assert intel.piece(state, _investigate(target=fx.E_GUILD), OutcomeTier.SUCCESS, SEED) is None
    own = _investigate(actor=fx.SOUTH, target=fx.E_SOUTH)
    assert intel.piece(state, own, OutcomeTier.SUCCESS, SEED) is None
    leak = _investigate().model_copy(update={"verb": Verb.LEAK})
    assert intel.piece(state, leak, OutcomeTier.SUCCESS, SEED) is None


def _misleading_seed(state: State) -> str:
    for n in range(64):
        seed = f"s{n}"
        found = intel.piece(state, _investigate(3), OutcomeTier.PARTIAL, seed)
        if found is not None and not found.truth:
            return seed
    raise AssertionError("no seed misleads")


def test_a_partial_success_can_name_the_wrong_object_of_the_right_kind(state: State) -> None:
    state, _ = _learn(state, OutcomeTier.PARTIAL)
    state, domain = _learn(state, OutcomeTier.PARTIAL)
    assert domain.truth and domain.obj == "force"  # the domain is never the lie
    seed = _misleading_seed(state)
    state, wrong = _learn(state, OutcomeTier.PARTIAL, seed)
    assert not wrong.truth and wrong.obj != "Northern Levies"
    forces = {a.name for a in state.assets.values() if a.kind.value == "force"}
    assert wrong.obj in forces  # right domain, wrong object
    honest = [
        s
        for s in (f"s{n}" for n in range(64))
        if (p := intel.piece(state, _investigate(4), OutcomeTier.PARTIAL, s)) and p.truth
    ]
    assert honest  # a partial is not always a lie


def _plant(state: State) -> State:
    forge = Intent(
        intent_id=IntentId("01ARZ3NDEKTSV4RRFFQ69F0001"),
        actor=fx.SOUTH,
        verb=Verb.FORGE,
        targets=(fx.E_SOUTH, fx.A_GUILD_NETWORK),
        scope=private_to(fx.SOUTH),
        filed_tick=0,
    )
    made = intel.decoys(state, forge, OutcomeTier.SUCCESS)
    assert [(x.predicate, x.obj, x.truth) for x in made] == [
        ("goal_domain", "network", False),
        ("goal_object", "Guild Factors", False),
    ]
    for x in made:
        assert x.scope.visibility.value == "covert" and x.scope.known_by == {fx.SOUTH}
        state = apply_delta(state, x, state.tick)
    return state


def test_a_forged_decoy_is_what_a_partial_success_finds(state: State) -> None:
    state = _plant(state)
    assert intel.held(state, fx.NORTH, fx.E_SOUTH) == frozenset()  # decoys are not North's
    state, _ = _learn(state, OutcomeTier.PARTIAL)
    state, domain = _learn(state, OutcomeTier.PARTIAL)
    assert (domain.obj, domain.truth) == ("network", False)
    state, obj = _learn(state, OutcomeTier.PARTIAL)
    assert (obj.obj, obj.truth) == ("Guild Factors", False)
    _, cond = _learn(state, OutcomeTier.PARTIAL)
    assert (cond.obj, cond.truth) == ("South controls Guild Factors", False)


def test_a_full_success_sees_through_the_decoy(state: State) -> None:
    state = _plant(state)
    for _ in range(3):
        state, found = _learn(state, OutcomeTier.SUCCESS)
    assert (found.obj, found.truth) == ("Northern Levies", True)


def test_a_forgery_needs_a_goal_your_own_entity_and_bait(state: State) -> None:
    base = Intent(
        intent_id=IntentId("01ARZ3NDEKTSV4RRFFQ69F0002"),
        actor=fx.NORTH,
        verb=Verb.FORGE,
        targets=(fx.E_NORTH, fx.A_GUILD_NETWORK),
        scope=private_to(fx.NORTH),
        filed_tick=0,
    )
    assert intel.decoys(state, base, OutcomeTier.SUCCESS) == []  # North has no goal
    south = base.model_copy(update={"actor": fx.SOUTH, "targets": (fx.A_GUILD_NETWORK,)})
    assert intel.decoys(state, south, OutcomeTier.SUCCESS) == []  # not about itself
    bare = base.model_copy(update={"actor": fx.SOUTH, "targets": (fx.E_SOUTH,)})
    assert intel.decoys(state, bare, OutcomeTier.SUCCESS) == []  # nothing to point at
    failed = base.model_copy(update={"actor": fx.SOUTH, "targets": (fx.E_SOUTH, fx.E_GUILD)})
    assert intel.decoys(state, failed, OutcomeTier.FAIL_COMPLICATION) == []


# --- in a tick -------------------------------------------------------------------


class SeenNarrator:
    """Echoes each seat's view and the deltas it is given, so a leak would show."""

    def __init__(self) -> None:
        self.given: dict[SeatId, list[d.Delta]] = {}

    def narrate(self, view: View, deltas: Sequence[d.Delta]) -> Narration:
        self.given[view.viewer] = list(deltas)
        body = view.model_dump_json() + "".join(x.model_dump_json() for x in deltas)
        return Narration(text=body)


def _goal_events(state: State) -> list[ev.Event]:
    events = fx.creation_events()
    n = len(events)
    return [
        *events,
        fx.envelope(
            n, ev.ClockCreated(clock=state.clocks[SOUTH_CLOCK]), scope=private_to(fx.SOUTH)
        ),
        fx.envelope(n + 1, ev.GoalSet(goal=state.goals[SOUTH_GOAL]), scope=private_to(fx.SOUTH)),
    ]


def _lucky(state: State, make: Callable[[int], Intent]) -> Intent:
    """The first intent whose seeded roll this tick is a plain success."""
    for n in range(500):
        intent = make(n)
        seed = seed_for(state.campaign_id, state.tick, intent.intent_id)
        if roll(seed, rate(state, intent)).outcome is OutcomeTier.SUCCESS:
            return intent
    raise AssertionError("no lucky intent")


def test_an_investigation_in_a_tick_tells_only_the_seeker(state: State) -> None:
    store = MemoryStore()
    store.append_all(_goal_events(state))
    spy = _lucky(load_state(store, fx.CAMPAIGN), _investigate)
    assert file_intent(store, fx.CAMPAIGN, spy) is None
    narrator = SeenNarrator()
    providers = Providers(ScriptedResolver(), narrator, RecordingDeliverer())
    run_tick(store, fx.CAMPAIGN, providers)
    after = load_state(store, fx.CAMPAIGN)
    pieces = [f for f in after.facts.values() if f.predicate in intel.PIECES]
    assert [(f.predicate, f.scope) for f in pieces] == [("goal_exists", private_to(fx.NORTH))]
    applied = [e for e in store.read(fx.CAMPAIGN) if isinstance(e.payload, ev.DeltaApplied)]
    assert any(e.payload.intent_id == spy.intent_id for e in applied)  # type: ignore[union-attr]
    assert [x.predicate for x in narrator.given[fx.NORTH] if isinstance(x, d.FactAssert)] == [
        "goal_exists"
    ]
    assert not any(isinstance(x, d.FactAssert) for x in narrator.given[fx.SOUTH])
    assert pieces[0].fact_id not in project(after, fx.SOUTH).facts


# --- leak tests ------------------------------------------------------------------


class Echo:
    """A deputy that attacks whenever a goal piece is anywhere in its prompt."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def call(self, request: Request) -> Reply:
        self.prompts.append(request.prompt)
        verb = "attack" if intel.HAS_AIM in request.prompt else "defend"
        return Reply({"intents": [{"verb": verb, "means": []}]}, "m", 1, 1, 0, 0.0)


def _with_standing_orders(state: State, *seats: SeatId) -> State:
    update = {s: state.seats[s].model_copy(update={"standing_orders": ("hold",)}) for s in seats}
    return state.model_copy(update={"seats": {**state.seats, **update}})


def test_removing_a_goal_piece_changes_only_the_holder(state: State) -> None:
    state, found = _learn(state, OutcomeTier.SUCCESS)
    state = _with_standing_orders(state, fx.NORTH, fx.GUILD, fx.SOUTH)
    for seat in (fx.GUILD, fx.SOUTH):  # no piece in their view: nothing can change
        assert counterfactual_diff(state, seat, found.fact_id, Echo()) == []
    assert counterfactual_diff(state, fx.NORTH, found.fact_id, Echo()) != []  # the test can fail


def test_goal_text_never_reaches_a_person_without_a_piece() -> None:
    """The duo world: Varda's spies learn all four pieces of Ostrel's goal, tick by tick,
    with every narration echoing its whole view. Ostrel's goal text and canary never
    reach Varda's person; neither goal reaches anyone else."""
    world = WorldFile.model_validate(duo_raw())
    campaign = CampaignId("01ARZ3NDEKTSV4RRFFQ69G5DU1")
    ids = resolve_keys(world, campaign)
    varda, ostrel = SeatId(ids["varda_seat"]), SeatId(ids["ostrel_seat"])
    store = MemoryStore()
    store.append_all(compile_world(world, campaign, fx.VERSIONS))
    for tick in range(4):

        def spy(n: int, tick: int = tick) -> Intent:
            return Intent(
                intent_id=IntentId(f"01ARZ3NDEKTSV4RRFFQ69W{tick:01d}{n:03d}"),
                actor=varda,
                verb=Verb.INVESTIGATE,
                targets=(fx.EntityId(ids["ostrel"]),),
                means=(fx.AssetId(ids["varda_whisperers"]),),
                scope=private_to(varda),
                filed_tick=tick,
            )

        lucky = _lucky(load_state(store, campaign), spy)
        assert file_intent(store, campaign, lucky) is None
        providers = Providers(ScriptedResolver(), SeenNarrator(), RecordingDeliverer())
        run_tick(store, campaign, providers)
    state = load_state(store, campaign)
    learned = sorted(
        (f.predicate, str(f.obj))
        for f in state.facts.values()
        if f.predicate in intel.PIECES and f.scope.visible_to(varda)
    )
    assert [p for p, _ in learned] == sorted(intel.PIECES)
    assert ("goal_condition", "Ostrel controls The Saltworks") in learned
    holdings = {"host": frozenset({varda}), "friend": frozenset({ostrel})}
    seen = surfaces_from_log(store.read(campaign), holdings)
    assert scan_humans(state, holdings, seen) == []
    varda_text = "\n".join(t for _, t in seen["host"])
    assert "CANARY-OSTREL-GOAL-2d90" not in varda_text
    assert "Take the Saltworks for Ostrel" not in varda_text  # the words never travel
    assert "Ostrel controls The Saltworks" in varda_text  # the piece does


def test_pieces_are_facts_a_seat_can_pass_on(state: State) -> None:
    state, found = _learn(state, OutcomeTier.SUCCESS)
    grant = d.KnowledgeGrant(
        delta_id=DeltaId("01ARZ3NDEKTSV4RRFFQ69G0KG0"),
        reason="shared",
        target=found.fact_id,
        seats=frozenset({fx.GUILD}),
    )
    shared = apply_delta(state, grant, state.tick)
    assert found.fact_id in project(shared, fx.GUILD).facts
    assert intel.held(shared, fx.GUILD, fx.E_SOUTH) == {"goal_exists"}


def test_every_check_has_a_domain_a_name_and_a_rendering(state: State) -> None:
    treaty = CommitmentId("01ARZ3NDEKTSV4RRFFQ69G0TR0")
    commitment = Commitment(
        commitment_id=treaty,
        parties=frozenset({fx.E_NORTH, fx.E_SOUTH}),
        clauses=(Clause(kind=ClauseKind.ABSTAIN, party=fx.E_NORTH, beneficiary=fx.E_SOUTH),),
        scope=private_to(fx.NORTH),
    )
    world = state.model_copy(update={"commitments": {treaty: commitment}})
    cases: list[tuple[g.Leaf, str, str]] = [
        (
            g.HoldsOffice(holder=fx.E_SOUTH, office=fx.A_NORTH_ARMY),
            "office",
            "South holds Northern Levies",
        ),
        (
            g.EntityIs(entity=fx.E_NORTH_AIDE, status=EntityStatus.DEAD),
            "person",
            "Ines dead",
        ),
        (
            g.MeterAt(meter=fx.M_NORTH_STABILITY, op="le", value=10),
            "standing",
            "Stability at most 10",
        ),
        (
            g.CommitmentInForce(commitment=treaty),
            "a treaty",
            "a treaty of North and South in force",
        ),
        (
            g.DispositionAt(relationship=fx.R_NORTH_SOUTH, op="ge", value=3),
            "a relationship",
            "ties between North and South at least 3",
        ),
    ]
    for leaf, dom, text in cases:
        assert intel.domain(world, leaf) == dom
        assert intel.render(world, leaf) == text
    either = g.AnyOf(conditions=(cases[2][0], cases[4][0]))
    assert (
        intel.render(world, either)
        == "Stability at most 10 or ties between North and South at least 3"
    )
    long = g.AllOf(conditions=tuple(c for c, _, _ in cases))
    rendered = intel.render(world, long)
    assert len(rendered) > intel.LABEL_MAX
    piece = intel._label(rendered)
    assert len(piece) == intel.LABEL_MAX and piece.endswith("...")


def test_a_decoy_is_of_the_same_kind_or_there_is_none(state: State) -> None:
    aide = g.EntityIs(entity=fx.E_NORTH_AIDE, status=EntityStatus.DEAD)
    people = {fx.E_NORTH_AIDE, fx.E_SOUTH_AIDE}
    assert intel._decoy(state, aide, "s") in people - {fx.E_NORTH_AIDE}
    meter = g.MeterAt(meter=fx.M_NORTH_STABILITY, op="ge", value=1)
    assert intel._decoy(state, meter, "s") is None  # the only meter
    ties = g.DispositionAt(relationship=fx.R_NORTH_SOUTH, op="ge", value=1)
    assert intel._decoy(state, ties, "s") is None
    assert intel._decoy(state, g.CommitmentInForce(commitment=CommitmentId("X" * 26)), "s") is None
    assert intel._name(state, "nothing") == "something unknown"
