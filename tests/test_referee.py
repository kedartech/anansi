"""Layer 3: the referee is pure, bounded and reproducible."""

from collections import Counter

import pytest
from hypothesis import given
from hypothesis import settings as h_settings
from hypothesis import strategies as st

from anansi.core import delta as d
from anansi.core import events as ev
from anansi.core.fold import apply, replay
from anansi.core.ids import ClockId, DeltaId, EntityId, IntentId, derived_id
from anansi.core.intent import FREE_VERBS, Intent, Verb
from anansi.core.ontology import ClockKind, EntityStatus, SeatStatus
from anansi.core.project import project
from anansi.core.referee import (
    Clamped,
    Full,
    Partial,
    clamp,
    rate,
    reveal,
    roll,
    seed_for,
    slots,
    validate,
)
from anansi.core.resolution import (
    EXPOSURE_CHANCE,
    SUCCESS_CHANCE,
    FeasibilityBand,
    OutcomeTier,
    RejectionReason,
)
from anansi.core.settings import Lethality, Plausibility, Settings
from anansi.core.state import State
from anansi.core.visibility import PUBLIC, Scope, private_to
from tests import fixtures as fx
from tests import strategies as s

BANDS = list(FeasibilityBand)


def _intent(
    verb: Verb,
    actor: fx.SeatId = fx.NORTH,
    *,
    targets: tuple[str, ...] = (),
    means: tuple[fx.AssetId, ...] = (),
    counterparty: EntityId | None = None,
    scope: Scope = PUBLIC,
    n: int = 1,
) -> Intent:
    return Intent(
        intent_id=IntentId(f"01ARZ3NDEKTSV4RRFFQ69I{n:05d}"),
        actor=actor,
        verb=verb,
        targets=targets,
        means=means,
        counterparty=counterparty,
        scope=scope,
        filed_tick=0,
    )


def _did(n: int) -> DeltaId:
    return DeltaId(f"01ARZ3NDEKTSV4RRFFQ69D{n:05d}")


@pytest.fixture
def state() -> State:
    return replay(fx.creation_events())


# --- validate ------------------------------------------------------------------


def test_claiming_an_army_you_do_not_hold_is_rejected(state: State) -> None:
    rej = validate(
        state, _intent(Verb.ATTACK, fx.SOUTH, targets=(fx.E_NORTH,), means=(fx.A_NORTH_ARMY,))
    )
    assert rej is not None and rej.reason is RejectionReason.MEANS_NOT_HELD


def test_claiming_a_secret_army_you_cannot_see_is_rejected(state: State) -> None:
    rej = validate(state, _intent(Verb.ATTACK, fx.SOUTH, means=(fx.A_NORTH_SECRET_ARMY,)))
    assert rej is not None and rej.reason is RejectionReason.MEANS_NOT_HELD


def test_own_means_and_known_targets_pass(state: State) -> None:
    assert (
        validate(state, _intent(Verb.ATTACK, targets=(fx.E_SOUTH,), means=(fx.A_NORTH_ARMY,)))
        is None
    )


def test_unknown_and_gone_targets_are_rejected(state: State) -> None:
    rej = validate(state, _intent(Verb.INVESTIGATE, targets=("0" * 26,)))
    assert rej is not None and rej.reason is RejectionReason.TARGET_UNKNOWN
    dead = apply(
        state,
        fx.envelope(
            state.last_seq + 1,
            ev.DeltaApplied(
                intent_id=None,
                delta=d.EntityStatusSet(
                    delta_id=_did(1), reason="", entity_id=fx.E_SOUTH_AIDE, status=EntityStatus.DEAD
                ),
            ),
        ),
    )
    rej = validate(dead, _intent(Verb.RECRUIT, targets=(fx.E_SOUTH_AIDE,)))
    assert rej is not None and rej.reason is RejectionReason.TARGET_UNKNOWN


def test_slots_fill_then_no_slot(state: State) -> None:
    # North already holds one open operation (the plot); 3 starting slots.
    assert slots(state, fx.NORTH).used == 1
    pending = state
    for i in range(2):
        intent = _intent(Verb.SABOTAGE, targets=(fx.A_SOUTH_TREASURY,), n=10 + i)
        assert validate(pending, intent) is None
        pending = apply(pending, fx.envelope(pending.last_seq + 1, ev.IntentFiled(intent=intent)))
    rej = validate(pending, _intent(Verb.SABOTAGE, targets=(fx.A_SOUTH_TREASURY,), n=20))
    assert rej is not None and rej.reason is RejectionReason.NO_SLOT
    # Talk is still free.
    assert validate(pending, _intent(Verb.NEGOTIATE, counterparty=fx.E_SOUTH, n=21)) is None


def test_blinded_seat_has_one_slot_and_no_irreversible_verbs(state: State) -> None:
    blinded = apply(
        state,
        fx.envelope(
            state.last_seq + 1,
            ev.SeatStatusSet(seat_id=fx.NORTH, status=SeatStatus.BLINDED, cause="x"),
        ),
    )
    assert slots(blinded, fx.NORTH).cap == 1 and slots(blinded, fx.NORTH).free == 0
    rej = validate(blinded, _intent(Verb.DECLARE))
    assert rej is not None and rej.reason is RejectionReason.OUT_OF_REACH


def test_outlets_report_and_fallen_seats_do_nothing(state: State) -> None:
    rej = validate(state, _intent(Verb.ATTACK, fx.HERALD, targets=(fx.E_NORTH,)))
    assert rej is not None and rej.reason is RejectionReason.OUT_OF_REACH
    assert validate(state, _intent(Verb.INVESTIGATE, fx.HERALD, targets=(fx.E_NORTH,))) is None
    fallen = apply(
        state,
        fx.envelope(
            state.last_seq + 1,
            ev.SeatStatusSet(seat_id=fx.SOUTH, status=SeatStatus.FALLEN, cause="x"),
        ),
    )
    rej = validate(fallen, _intent(Verb.WAIT, fx.SOUTH))
    assert rej is not None and rej.reason is RejectionReason.SEAT_INACTIVE


def test_safety_hook_is_called_last(state: State) -> None:
    calls: list[IntentId] = []

    def refuse(state: State, intent: Intent) -> str | None:
        calls.append(intent.intent_id)
        return "line: real person"

    rej = validate(state, _intent(Verb.DECLARE), safety=refuse)
    assert rej is not None and rej.reason is RejectionReason.SAFETY_LINE
    assert calls
    calls.clear()
    validate(state, _intent(Verb.ATTACK, means=(fx.A_SOUTH_TREASURY,)), safety=refuse)
    assert not calls  # means check fails first; the classifier never runs


def test_duplicate_intent_id_is_rejected(state: State) -> None:
    intent = _intent(Verb.WAIT)
    filed = apply(state, fx.envelope(state.last_seq + 1, ev.IntentFiled(intent=intent)))
    rej = validate(filed, intent)
    assert rej is not None and rej.reason is RejectionReason.DUPLICATE


# --- rate ----------------------------------------------------------------------


def test_free_verbs_are_assured_and_impossible_is_structural(state: State) -> None:
    assert rate(state, _intent(Verb.NEGOTIATE, counterparty=fx.E_SOUTH)) is FeasibilityBand.ASSURED
    assert rate(state, _intent(Verb.ATTACK, targets=("0" * 26,))) is FeasibilityBand.IMPOSSIBLE
    assert (
        rate(state, _intent(Verb.ATTACK, fx.SOUTH, means=(fx.A_NORTH_ARMY,)))
        is FeasibilityBand.IMPOSSIBLE
    )


def test_rate_never_assured_without_means_on_a_means_verb(state: State) -> None:
    for verb in Verb:
        if verb in FREE_VERBS or verb is Verb.TRAVEL:
            continue
        band = rate(state, _intent(verb, targets=(fx.E_SOUTH,)))
        assert band in (FeasibilityBand.LONG_SHOT, FeasibilityBand.IMPOSSIBLE), verb


def test_rate_is_monotone_in_means(state: State) -> None:
    weak = rate(state, _intent(Verb.ATTACK, targets=(fx.E_SOUTH,), means=(fx.A_NORTH_SECRET_ARMY,)))
    strong = rate(
        state,
        _intent(
            Verb.ATTACK, targets=(fx.E_SOUTH,), means=(fx.A_NORTH_ARMY, fx.A_NORTH_SECRET_ARMY)
        ),
    )
    assert BANDS.index(strong) >= BANDS.index(weak)


def test_perceived_odds_differ_from_true_odds(state: State) -> None:
    """South attacks North seeing only the public levies; the truth includes the hill irregulars."""
    intent = _intent(Verb.ATTACK, fx.SOUTH, targets=(fx.E_NORTH,), means=(fx.A_SOUTH_GUARD,))
    perceived = rate(project(state, fx.SOUTH), intent)
    true = rate(state, intent)
    assert perceived is FeasibilityBand.LIKELY
    assert true is FeasibilityBand.CONTESTED


def test_plausibility_and_disposition_move_the_band(state: State) -> None:
    intent = _intent(Verb.SABOTAGE, targets=(fx.A_SOUTH_TREASURY,), means=(fx.A_NORTH_ARMY,))
    base = rate(state, intent)
    pulp = state.model_copy(update={"settings": Settings(plausibility=Plausibility.PULP)})
    strict = state.model_copy(update={"settings": Settings(plausibility=Plausibility.STRICT)})
    assert BANDS.index(rate(pulp, intent)) >= BANDS.index(base) >= BANDS.index(rate(strict, intent))
    deal = _intent(Verb.LOBBY, counterparty=fx.E_SOUTH, means=(fx.A_NORTH_ARMY,))
    rel = state.relationships[fx.R_NORTH_SOUTH]
    warm = state.model_copy(
        update={"relationships": {rel.relationship_id: rel.model_copy(update={"disposition": 3})}}
    )
    cold = state.model_copy(
        update={"relationships": {rel.relationship_id: rel.model_copy(update={"disposition": -3})}}
    )
    assert BANDS.index(rate(warm, deal)) > BANDS.index(rate(cold, deal))


@h_settings(max_examples=200, deadline=None)
@given(s.intents, st.data())
def test_rate_is_total_over_any_intent(intent: Intent, data: st.DataObject) -> None:
    state = replay(fx.creation_events())
    assert rate(state, intent) in BANDS
    seat = data.draw(st.sampled_from(sorted(state.seats)))
    assert rate(project(state, seat), intent) in BANDS


# --- roll ----------------------------------------------------------------------


def test_roll_is_reproducible_and_seed_is_blake2b() -> None:
    seed = seed_for(fx.CAMPAIGN, 3, IntentId("01ARZ3NDEKTSV4RRFFQ69I00001"))
    assert len(seed) == 32
    a = roll(seed, FeasibilityBand.CONTESTED, exposure=3)
    b = roll(seed, FeasibilityBand.CONTESTED, exposure=3)
    assert a == b
    other = seed_for(fx.CAMPAIGN, 4, IntentId("01ARZ3NDEKTSV4RRFFQ69I00001"))
    assert other != seed


def test_impossible_always_fails_and_assured_rarely() -> None:
    seeds = [
        seed_for(fx.CAMPAIGN, t, IntentId(f"01ARZ3NDEKTSV4RRFFQ69I{t:05d}")) for t in range(2000)
    ]
    assert all(
        roll(x, FeasibilityBand.IMPOSSIBLE).outcome is OutcomeTier.FAIL_COMPLICATION for x in seeds
    )
    for band in (
        FeasibilityBand.LONG_SHOT,
        FeasibilityBand.CONTESTED,
        FeasibilityBand.LIKELY,
        FeasibilityBand.ASSURED,
    ):
        hits = sum(roll(x, band).outcome is not OutcomeTier.FAIL_COMPLICATION for x in seeds)
        assert abs(hits / len(seeds) - SUCCESS_CHANCE[band]) < 0.04, band


def test_success_split_and_exposure_match_the_tables() -> None:
    seeds = [
        seed_for(fx.CAMPAIGN, t, IntentId(f"01ARZ3NDEKTSV4RRFFQ69I{t:05d}")) for t in range(4000)
    ]
    tiers = Counter(roll(x, FeasibilityBand.ASSURED).outcome for x in seeds)
    successes = sum(v for k, v in tiers.items() if k is not OutcomeTier.FAIL_COMPLICATION)
    assert abs(tiers[OutcomeTier.PARTIAL] / successes - 0.40) < 0.04
    assert abs(tiers[OutcomeTier.SUCCESS] / successes - 0.45) < 0.04
    assert abs(tiers[OutcomeTier.SUCCESS_OPPORTUNITY] / successes - 0.15) < 0.04
    for exposure, pct in EXPOSURE_CHANCE.items():
        exposed = sum(roll(x, FeasibilityBand.CONTESTED, exposure).exposed for x in seeds)
        assert abs(exposed / len(seeds) - pct / 100) < 0.03, exposure


# --- clamp ---------------------------------------------------------------------


def _ids(c: Clamped) -> set[DeltaId]:
    return {x.delta_id for x in c.kept}


def test_clamp_trims_tier_disposition_slots_and_logs_each_cut(state: State) -> None:
    intent = _intent(
        Verb.ATTACK, targets=(fx.E_SOUTH,), means=(fx.A_NORTH_ARMY,), counterparty=fx.E_SOUTH
    )
    proposal: list[d.Delta] = [
        d.AssetTierDelta(delta_id=_did(1), reason="", asset_id=fx.A_SOUTH_TREASURY, change=-2),
        d.DispositionDelta(
            delta_id=_did(2), reason="", relationship_id=fx.R_NORTH_SOUTH, change=-2
        ),
        d.SeatSlotsDelta(delta_id=_did(3), reason="", seat_id=fx.SOUTH, change=-2),
    ]
    out = clamp(state, intent, proposal, OutcomeTier.SUCCESS)
    assert [x.delta_id for x in out.kept] == [_did(1), _did(2), _did(3)]
    assert out.kept[0].change == -1  # type: ignore[union-attr]
    assert out.kept[1].change == -2  # type: ignore[union-attr]  # public intent, direct pair: ±2 allowed
    assert out.kept[2].change == -1  # type: ignore[union-attr]
    assert {c.delta_id for c in out.cut} == {_did(1), _did(3)}
    assert all(c.reason is RejectionReason.CLAMPED for c in out.cut)
    covert = intent.model_copy(update={"scope": private_to(fx.NORTH, covert=True)})
    out2 = clamp(state, covert, proposal, OutcomeTier.SUCCESS)
    assert out2.kept[1].change == -1  # type: ignore[union-attr]


def test_failure_only_lowers_the_actors_own_assets(state: State) -> None:
    intent = _intent(Verb.ATTACK, targets=(fx.E_SOUTH,), means=(fx.A_NORTH_ARMY,))
    proposal: list[d.Delta] = [
        d.AssetTierDelta(delta_id=_did(1), reason="", asset_id=fx.A_SOUTH_TREASURY, change=-1),
        d.AssetTierDelta(delta_id=_did(2), reason="", asset_id=fx.A_NORTH_ARMY, change=1),
        d.AssetTierDelta(delta_id=_did(3), reason="", asset_id=fx.A_NORTH_ARMY, change=-1),
        d.ClockAdvance(delta_id=_did(4), reason="", clock_id=fx.C_NORTH_PLOT, segments=1),
    ]
    out = clamp(state, intent, proposal, OutcomeTier.FAIL_COMPLICATION)
    assert _ids(out) == {_did(3)}
    reasons = {c.delta_id: c.reason for c in out.cut}
    assert reasons[_did(1)] is RejectionReason.TIER_DISALLOWS
    assert reasons[_did(2)] is RejectionReason.TIER_DISALLOWS
    assert reasons[_did(4)] is RejectionReason.CLAMPED


def test_clock_progress_follows_outcome_and_new_clocks_are_rationed(state: State) -> None:
    intent = _intent(Verb.SABOTAGE, targets=(fx.A_SOUTH_TREASURY,), means=(fx.A_NORTH_ARMY,))
    new = ClockId("01ARZ3NDEKTSV4RRFFQ69G5CZ1")

    def mk(kind: ClockKind) -> d.ClockCreate:
        return d.ClockCreate(
            delta_id=_did(9),
            reason="",
            clock_id=new,
            clock_kind=kind,
            name="x",
            segments_total=4,
            scope=PUBLIC,
        )

    advance = d.ClockAdvance(delta_id=_did(1), reason="", clock_id=fx.C_NORTH_PLOT, segments=2)
    partial = clamp(state, intent, [advance, mk(ClockKind.OPPORTUNITY)], OutcomeTier.PARTIAL)
    assert partial.kept[0].segments == 1 and len(partial.kept) == 1  # type: ignore[union-attr]
    opp = clamp(
        state, intent, [advance, mk(ClockKind.OPPORTUNITY)], OutcomeTier.SUCCESS_OPPORTUNITY
    )
    assert len(opp.kept) == 2 and opp.kept[0].segments == 2  # type: ignore[union-attr]
    fail = clamp(state, intent, [mk(ClockKind.THREAT)], OutcomeTier.FAIL_COMPLICATION)
    assert len(fail.kept) == 1
    op = clamp(state, intent, [mk(ClockKind.OPERATION)], OutcomeTier.SUCCESS_OPPORTUNITY)
    assert not op.kept and op.cut[0].detail.startswith("operations are opened")


def test_meter_cap_is_summed_across_the_tick(state: State) -> None:
    intent = _intent(Verb.LOBBY, counterparty=fx.E_SOUTH, means=(fx.A_NORTH_ARMY,))
    proposal: list[d.Delta] = [
        d.MeterDelta(delta_id=_did(1), reason="", meter_id=fx.M_NORTH_STABILITY, change=-7),
        d.MeterDelta(delta_id=_did(2), reason="", meter_id=fx.M_NORTH_STABILITY, change=-7),
        d.MeterDelta(delta_id=_did(3), reason="", meter_id=fx.M_NORTH_STABILITY, change=-7),
    ]
    out = clamp(state, intent, proposal, OutcomeTier.SUCCESS)
    assert [x.change for x in out.kept] == [-7, -3]  # type: ignore[union-attr]
    assert {c.delta_id for c in out.cut} == {_did(2), _did(3)}


def test_lethality_rules(state: State) -> None:
    intent = _intent(Verb.ATTACK, targets=(fx.E_SOUTH_AIDE,), means=(fx.A_NORTH_ARMY,))
    kill = d.EntityStatusSet(
        delta_id=_did(1), reason="", entity_id=fx.E_SOUTH_AIDE, status=EntityStatus.DEAD
    )
    capture = d.EntityStatusSet(
        delta_id=_did(2), reason="", entity_id=fx.E_SOUTH_AIDE, status=EntityStatus.CAPTURED
    )
    # No threat clock at risk: a kill is refused; a capture is allowed.
    out = clamp(state, intent, [kill, capture], OutcomeTier.SUCCESS)
    assert _ids(out) == {_did(2)}
    assert out.cut[0].reason is RejectionReason.LETHALITY_DISALLOWS
    # Put the aide at risk.
    threat = ClockId("01ARZ3NDEKTSV4RRFFQ69G5CT0")
    at_risk = apply(
        state,
        fx.envelope(
            state.last_seq + 1,
            ev.DeltaApplied(
                intent_id=None,
                delta=d.ClockCreate(
                    delta_id=_did(5),
                    reason="",
                    clock_id=threat,
                    clock_kind=ClockKind.THREAT,
                    name="Hunt",
                    target=fx.E_SOUTH_AIDE,
                    segments_total=4,
                    scope=PUBLIC,
                ),
            ),
        ),
    )
    assert _ids(clamp(at_risk, intent, [kill], OutcomeTier.SUCCESS)) == {_did(1)}
    soft = at_risk.model_copy(update={"settings": Settings(lethality=Lethality.SOFT)})
    assert not clamp(soft, intent, [kill], OutcomeTier.SUCCESS).kept
    no_harm = at_risk.model_copy(update={"settings": Settings(agent_harm=False)})
    assert not clamp(no_harm, intent, [kill, capture], OutcomeTier.SUCCESS).kept


def test_only_leak_or_declare_may_go_public(state: State) -> None:
    grant = d.KnowledgeGrant(
        delta_id=_did(1), reason="", target=fx.F_NORTH_PLOT, seats=frozenset(), make_public=True
    )
    narrow = d.KnowledgeGrant(
        delta_id=_did(2), reason="", target=fx.F_NORTH_PLOT, seats=frozenset({fx.SOUTH})
    )
    investigate = _intent(
        Verb.INVESTIGATE, fx.SOUTH, targets=(fx.E_NORTH,), means=(fx.A_SOUTH_TREASURY,)
    )
    out = clamp(state, investigate, [grant, narrow], OutcomeTier.SUCCESS)
    assert _ids(out) == {_did(2)}
    leak = _intent(Verb.LEAK, fx.SOUTH, targets=(fx.E_NORTH,), means=(fx.A_SOUTH_TREASURY,))
    assert _ids(clamp(state, leak, [grant], OutcomeTier.SUCCESS)) == {_did(1)}


def test_at_most_six_objects_per_intent(state: State) -> None:
    intent = _intent(Verb.LEGISLATE, means=(fx.A_NORTH_ARMY,))
    proposal: list[d.Delta] = [
        d.FactAssert(
            delta_id=_did(i),
            reason="",
            fact_id=fx.FactId(f"01ARZ3NDEKTSV4RRFFQ69F{i:05d}"),
            subject=fx.E_NORTH,
            predicate="decreed",
            obj="tax",
            scope=PUBLIC,
        )
        for i in range(8)
    ]
    out = clamp(state, intent, proposal, OutcomeTier.SUCCESS)
    assert len(out.kept) == 6 and len(out.cut) == 2


@h_settings(max_examples=200, deadline=None)
@given(st.lists(s.deltas, max_size=10), st.sampled_from(list(OutcomeTier)), st.booleans())
def test_clamp_is_idempotent_and_never_widens(
    proposal: list[d.Delta], outcome: OutcomeTier, with_intent: bool
) -> None:
    state = replay(fx.creation_events())
    intent = (
        _intent(Verb.SABOTAGE, targets=(fx.A_SOUTH_TREASURY,), means=(fx.A_NORTH_ARMY,))
        if with_intent
        else None
    )
    once = clamp(state, intent, proposal, outcome)
    twice = clamp(state, intent, list(once.kept), outcome)
    assert twice.kept == once.kept
    assert not twice.cut
    by_id = {x.delta_id: x for x in proposal}
    for kept in once.kept:
        original = by_id[kept.delta_id]
        for field in ("change", "segments"):
            if hasattr(kept, field):
                assert abs(getattr(kept, field)) <= abs(getattr(original, field))


# --- reveal --------------------------------------------------------------------


def test_reveal_full_and_partial_are_deterministic(state: State) -> None:
    full = reveal(state, Full(), tick=10)
    assert {g.target for g in full} == {fx.F_NORTH_PLOT, fx.C_NORTH_PLOT}
    assert all(g.make_public for g in full)
    assert reveal(state, Full(), tick=10) == full
    assert full[0].delta_id == derived_id(fx.CAMPAIGN, 10, "reveal", full[0].target)
    half = reveal(state, Partial(fraction=0.5), tick=10)
    assert len(half) == 1 and half == reveal(state, Partial(fraction=0.5), tick=10)
    assert reveal(state, Partial(fraction=0.5, min_age_ticks=20), tick=10) == ()
    revealed = state
    for g in full:
        revealed = apply(
            revealed,
            fx.envelope(revealed.last_seq + 1, ev.DeltaApplied(intent_id=None, delta=g), tick=10),
        )
    assert fx.F_NORTH_PLOT in project(revealed, fx.SOUTH).facts
    assert reveal(revealed, Full(), tick=11) == ()
