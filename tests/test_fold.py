"""Layer 2: the fold is total, deterministic and refuses gaps."""

import pytest
from hypothesis import given
from hypothesis import settings as h_settings
from hypothesis import strategies as st

from anansi.core import delta as d
from anansi.core import events as ev
from anansi.core.fold import FoldError, apply, empty, replay, replay_to, state_hash
from anansi.core.ids import DeltaId
from anansi.core.ontology import AssetStatus, ClockStatus, SeatStatus
from anansi.core.visibility import PUBLIC
from tests import fixtures as fx


def _delta_event(seq: int, delta: d.Delta, *, tick: int = 0) -> ev.Event:
    return fx.envelope(seq, ev.DeltaApplied(intent_id=None, delta=delta), tick=tick)


def _did(n: int) -> DeltaId:
    return DeltaId(f"01ARZ3NDEKTSV4RRFFQ69D{n:05d}")


def test_creation_replays_to_a_populated_state() -> None:
    state = replay(fx.creation_events())
    assert state.last_seq == len(fx.creation_payloads()) - 1
    assert set(state.seats) == {fx.NORTH, fx.SOUTH, fx.GUILD, fx.HERALD}
    assert state.seats[fx.NORTH].agent == fx.E_NORTH_AIDE
    assert state.assets[fx.A_NORTH_SECRET_ARMY].tier == 1
    assert state.facts[fx.F_NORTH_PLOT].canary == fx.CANARY


def test_replay_is_deterministic_and_hash_stable() -> None:
    events = fx.creation_events()
    a, b = replay(events), replay(events)
    assert a == b
    assert state_hash(a) == state_hash(b)


def test_sequence_gap_is_refused() -> None:
    events = fx.creation_events()
    with pytest.raises(FoldError, match="sequence gap"):
        replay([events[0], events[2]])


def test_wrong_campaign_is_refused() -> None:
    events = fx.creation_events()
    other = empty(fx.CampaignId("01ARZ3NDEKTSV4RRFFQ69G5FA1"))
    with pytest.raises(FoldError, match="belongs to campaign"):
        apply(other, events[0])


def test_duplicate_creation_is_refused() -> None:
    events = fx.creation_events()
    dup = events[2].model_copy(update={"seq": len(events)})
    with pytest.raises(FoldError, match="duplicate entity"):
        replay([*events, dup])


def test_record_kinds_change_nothing_but_seq() -> None:
    events = fx.creation_events()
    state = replay(events)
    narration = fx.envelope(
        len(events), ev.NarrationWritten(seat_id=fx.NORTH, text="The hills are quiet.")
    )
    after = apply(state, narration)
    assert after.model_copy(update={"last_seq": state.last_seq}) == state


def test_deltas_apply_and_clamp_to_type_bounds() -> None:
    events = fx.creation_events()
    n = len(events)
    events += [
        _delta_event(
            n, d.AssetTierDelta(delta_id=_did(1), reason="", asset_id=fx.A_NORTH_ARMY, change=2)
        ),
        _delta_event(
            n + 1, d.AssetTierDelta(delta_id=_did(2), reason="", asset_id=fx.A_NORTH_ARMY, change=2)
        ),
        _delta_event(
            n + 2,
            d.MeterDelta(delta_id=_did(3), reason="", meter_id=fx.M_NORTH_STABILITY, change=-80),
        ),
        _delta_event(
            n + 3,
            d.DispositionDelta(
                delta_id=_did(4), reason="", relationship_id=fx.R_NORTH_SOUTH, change=-2
            ),
        ),
        _delta_event(
            n + 4,
            d.DispositionDelta(
                delta_id=_did(5), reason="", relationship_id=fx.R_NORTH_SOUTH, change=-2
            ),
        ),
        _delta_event(
            n + 5,
            d.DispositionDelta(
                delta_id=_did(6), reason="", relationship_id=fx.R_NORTH_SOUTH, change=-2
            ),
        ),
        _delta_event(
            n + 6, d.SeatSlotsDelta(delta_id=_did(7), reason="", seat_id=fx.SOUTH, change=2)
        ),
        _delta_event(
            n + 7, d.SeatSlotsDelta(delta_id=_did(8), reason="", seat_id=fx.SOUTH, change=2)
        ),
    ]
    state = replay(events)
    assert state.assets[fx.A_NORTH_ARMY].tier == 5
    assert state.meters[fx.M_NORTH_STABILITY].value == 0
    assert state.relationships[fx.R_NORTH_SOUTH].disposition == -5
    assert state.seats[fx.SOUTH].slots == state.settings.slot_cap


def test_clock_fills_resolves_and_releases_its_slot() -> None:
    events = fx.creation_events()
    n = len(events)
    events += [
        _delta_event(
            n, d.ClockAdvance(delta_id=_did(1), reason="", clock_id=fx.C_NORTH_PLOT, segments=2)
        ),
        _delta_event(
            n + 1, d.ClockAdvance(delta_id=_did(2), reason="", clock_id=fx.C_NORTH_PLOT, segments=2)
        ),
    ]
    state = replay(events)
    clock = state.clocks[fx.C_NORTH_PLOT]
    assert clock.status is ClockStatus.RESOLVED
    assert clock.complete
    assert clock.holds_slot is False


def test_clock_status_set_fails_and_releases_slot() -> None:
    events = fx.creation_events()
    events.append(
        _delta_event(
            len(events),
            d.ClockStatusSet(
                delta_id=_did(1), reason="", clock_id=fx.C_NORTH_PLOT, status=ClockStatus.FAILED
            ),
        )
    )
    clock = replay(events).clocks[fx.C_NORTH_PLOT]
    assert clock.status is ClockStatus.FAILED and clock.holds_slot is False


def test_knowledge_grant_widens_scope_and_make_public() -> None:
    events = fx.creation_events()
    n = len(events)
    events += [
        _delta_event(
            n,
            d.KnowledgeGrant(
                delta_id=_did(1), reason="", target=fx.F_NORTH_PLOT, seats=frozenset({fx.SOUTH})
            ),
        ),
        _delta_event(
            n + 1,
            d.KnowledgeGrant(
                delta_id=_did(2),
                reason="",
                target=fx.A_NORTH_SECRET_ARMY,
                seats=frozenset(),
                make_public=True,
            ),
        ),
    ]
    state = replay(events)
    assert state.facts[fx.F_NORTH_PLOT].scope.visible_to(fx.SOUTH)
    assert not state.facts[fx.F_NORTH_PLOT].scope.visible_to(fx.GUILD)
    assert state.assets[fx.A_NORTH_SECRET_ARMY].scope == PUBLIC


def test_grant_on_unknown_target_is_refused() -> None:
    events = fx.creation_events()
    events.append(
        _delta_event(
            len(events),
            d.KnowledgeGrant(
                delta_id=_did(1),
                reason="",
                target=fx.FactId("01ARZ3NDEKTSV4RRFFQ69G5ZZZ"),
                seats=frozenset({fx.SOUTH}),
            ),
        )
    )
    with pytest.raises(FoldError, match="knowledge grant target"):
        replay(events)


def test_schedule_fires_relative_to_tick() -> None:
    events = fx.creation_events()
    sid = fx.ScheduledEventId("01ARZ3NDEKTSV4RRFFQ69G5SE0")
    events.append(
        _delta_event(
            len(events),
            d.ScheduledEventQueue(
                delta_id=_did(1),
                reason="",
                scheduled_id=sid,
                fire_in_ticks=3,
                name="Flood",
                scope=PUBLIC,
            ),
            tick=4,
        )
    )
    events.append(fx.envelope(len(events), ev.ScheduledEventFired(scheduled_id=sid), tick=7))
    state = replay(events)
    assert state.scheduled[sid].fire_at_tick == 7
    assert state.scheduled[sid].fired


def test_seat_blinded_drops_agent_and_asset_status_lost() -> None:
    events = fx.creation_events()
    n = len(events)
    events += [
        fx.envelope(
            n, ev.SeatStatusSet(seat_id=fx.NORTH, status=SeatStatus.BLINDED, cause="Ines was taken")
        ),
        _delta_event(
            n + 1,
            d.AssetStatusSet(
                delta_id=_did(1), reason="", asset_id=fx.A_NORTH_ARMY, status=AssetStatus.LOST
            ),
        ),
    ]
    state = replay(events)
    assert state.seats[fx.NORTH].agent is None
    assert state.seats[fx.NORTH].status is SeatStatus.BLINDED
    assert state.assets[fx.A_NORTH_ARMY].status is AssetStatus.LOST


def test_tick_open_close_round_trip_with_hash() -> None:
    events = fx.creation_events()
    n = len(events)
    before = replay(events)
    events.append(fx.envelope(n, ev.TickOpened(intents_frozen=()), tick=0))
    opened = replay(events)
    events.append(fx.envelope(n + 1, ev.TickClosed(state_hash=state_hash(opened)), tick=0))
    closed = replay(events)
    assert closed.tick == 1
    assert before.tick == 0
    bad = events[-1].model_copy(
        update={"payload": ev.TickClosed(state_hash="0" * 32), "seq": n + 2}
    )
    with pytest.raises(FoldError, match="state hash mismatch"):
        replay([*events, bad])


def test_replay_to_stops_at_tick() -> None:
    events = fx.creation_events()
    n = len(events)
    events += [
        fx.envelope(n, ev.TickOpened(intents_frozen=()), tick=0),
        _delta_event(
            n + 1,
            d.AssetTierDelta(delta_id=_did(1), reason="", asset_id=fx.A_NORTH_ARMY, change=1),
            tick=1,
        ),
    ]
    assert replay_to(events, 0).assets[fx.A_NORTH_ARMY].tier == 2
    assert replay(events).assets[fx.A_NORTH_ARMY].tier == 3


@h_settings(max_examples=100, deadline=None)
@given(st.lists(st.integers(-2, 2), min_size=1, max_size=20))
def test_tier_never_leaves_bounds_under_any_delta_sequence(changes: list[int]) -> None:
    events = fx.creation_events()
    n = len(events)
    for i, change in enumerate(changes):
        events.append(
            _delta_event(
                n + i,
                d.AssetTierDelta(
                    delta_id=_did(i), reason="", asset_id=fx.A_NORTH_ARMY, change=change
                ),
            )
        )
    tier = replay(events).assets[fx.A_NORTH_ARMY].tier
    assert 0 <= tier <= 5


def test_hash_is_independent_of_set_insertion_order() -> None:
    events = fx.creation_events()
    state = replay(events)
    rel = state.relationships[fx.R_NORTH_SOUTH]
    a = rel.model_copy(update={"leverage_tags": frozenset(["b", "a", "c"])})
    b = rel.model_copy(update={"leverage_tags": frozenset(["c", "a", "b"])})
    sa = state.model_copy(update={"relationships": {rel.relationship_id: a}})
    sb = state.model_copy(update={"relationships": {rel.relationship_id: b}})
    assert state_hash(sa) == state_hash(sb)
    assert '"leverage_tags":["a","b","c"]' in sa.model_dump_json()


def test_remaining_delta_kinds_apply() -> None:
    from anansi.core.ids import AssetId, ClockId, CommitmentId, FactId
    from anansi.core.ontology import (
        AssetKind,
        Clause,
        ClauseKind,
        ClockKind,
        CommitmentStatus,
        EntityStatus,
    )

    events = fx.creation_events()
    n = len(events)
    new_asset = AssetId("01ARZ3NDEKTSV4RRFFQ69G5AZ0")
    new_clock = ClockId("01ARZ3NDEKTSV4RRFFQ69G5CZ0")
    new_fact = FactId("01ARZ3NDEKTSV4RRFFQ69G5FZ0")
    new_cm = CommitmentId("01ARZ3NDEKTSV4RRFFQ69G5KZ0")
    deltas: list[d.Delta] = [
        d.AssetControllerSet(
            delta_id=_did(1), reason="", asset_id=fx.A_NORTH_ARMY, controller=fx.E_SOUTH
        ),
        d.AssetCreate(
            delta_id=_did(2),
            reason="",
            asset_id=new_asset,
            asset_kind=AssetKind.INFO,
            name="Dossier",
            tier=1,
            controller=fx.E_GUILD,
            scope=PUBLIC,
        ),
        d.RelationshipTagSet(
            delta_id=_did(3),
            reason="",
            relationship_id=fx.R_NORTH_SOUTH,
            add_price=frozenset({"grain"}),
            add_leverage=frozenset({"hostage", "debt"}),
            remove_leverage=frozenset({"debt"}),
        ),
        d.ClockCreate(
            delta_id=_did(4),
            reason="",
            clock_id=new_clock,
            clock_kind=ClockKind.OPERATION,
            name="Bribe the ferrymen",
            owner=fx.SOUTH,
            segments_total=3,
            scope=PUBLIC,
        ),
        d.FactAssert(
            delta_id=_did(5),
            reason="",
            fact_id=new_fact,
            subject=fx.E_SOUTH,
            predicate="funds",
            obj=fx.E_GUILD,
            truth=False,
            scope=PUBLIC,
        ),
        d.EntityStatusSet(
            delta_id=_did(6), reason="", entity_id=fx.E_NORTH_AIDE, status=EntityStatus.CAPTURED
        ),
        d.CommitmentCreate(
            delta_id=_did(7),
            reason="",
            commitment_id=new_cm,
            parties=frozenset({fx.E_NORTH, fx.E_SOUTH}),
            clauses=(Clause(kind=ClauseKind.ABSTAIN, party=fx.E_NORTH, beneficiary=fx.E_SOUTH),),
            scope=PUBLIC,
        ),
        d.CommitmentStatusSet(delta_id=_did(8), reason="", commitment_id=new_cm, status="accepted"),
        d.ClockExposureDelta(delta_id=_did(9), reason="", clock_id=new_clock, change=2),
        d.KnowledgeGrant(delta_id=_did(10), reason="", target=new_cm, seats=frozenset({fx.GUILD})),
        d.KnowledgeGrant(
            delta_id=_did(11), reason="", target=new_clock, seats=frozenset({fx.GUILD})
        ),
    ]
    for i, delta in enumerate(deltas):
        events.append(_delta_event(n + i, delta))
    state = replay(events)
    assert state.assets[fx.A_NORTH_ARMY].controller == fx.E_SOUTH
    assert state.assets[new_asset].name == "Dossier"
    rel = state.relationships[fx.R_NORTH_SOUTH]
    assert rel.price_tags == {"grain"} and rel.leverage_tags == {"hostage"}
    assert state.clocks[new_clock].holds_slot is True
    assert state.clocks[new_clock].exposure == 2
    assert state.facts[new_fact].truth is False
    assert state.entities[fx.E_NORTH_AIDE].status is EntityStatus.CAPTURED
    assert state.commitments[new_cm].status is CommitmentStatus.ACCEPTED
    assert state.commitments[new_cm].scope.visible_to(fx.GUILD)
    assert state.clocks[new_clock].scope == PUBLIC


def test_intents_file_freeze_and_clear() -> None:
    from anansi.core.ids import IntentId
    from anansi.core.intent import Intent, Verb
    from anansi.core.resolution import RejectionReason

    events = fx.creation_events()
    n = len(events)
    i1 = IntentId("01ARZ3NDEKTSV4RRFFQ69G5I10")
    i2 = IntentId("01ARZ3NDEKTSV4RRFFQ69G5I20")

    def mk(iid: IntentId) -> Intent:
        return Intent(
            intent_id=iid, actor=fx.NORTH, verb=Verb.INVESTIGATE, scope=PUBLIC, filed_tick=0
        )

    events += [
        fx.envelope(n, ev.IntentFiled(intent=mk(i1))),
        fx.envelope(n + 1, ev.IntentFiled(intent=mk(i2))),
        fx.envelope(n + 2, ev.IntentRejected(intent_id=i2, reason=RejectionReason.NO_SLOT)),
        fx.envelope(n + 3, ev.TickOpened(intents_frozen=(i1,))),
    ]
    opened = replay(events)
    assert opened.frozen == (i1,) and set(opened.pending) == {i1}
    events.append(fx.envelope(n + 4, ev.TickClosed(state_hash=state_hash(opened))))
    closed = replay(events)
    assert closed.pending == {} and closed.frozen == ()


@pytest.mark.parametrize(
    "delta",
    [
        d.AssetTierDelta(delta_id=_did(1), reason="", asset_id=fx.AssetId("0" * 26), change=1),
        d.MeterDelta(delta_id=_did(1), reason="", meter_id=fx.MeterId("0" * 26), change=1),
        d.ClockAdvance(delta_id=_did(1), reason="", clock_id=fx.ClockId("0" * 26), segments=1),
        d.EntityStatusSet(
            delta_id=_did(1),
            reason="",
            entity_id=fx.EntityId("0" * 26),
            status=fx.EntityStatus.DEAD,
        ),
        d.SeatSlotsDelta(delta_id=_did(1), reason="", seat_id=fx.SeatId("0" * 26), change=1),
    ],
)
def test_unknown_ids_are_refused(delta: d.Delta) -> None:
    events = fx.creation_events()
    events.append(_delta_event(len(events), delta))
    with pytest.raises(FoldError, match="unknown"):
        replay(events)
