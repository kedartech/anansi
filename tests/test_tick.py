"""Layer 4: the orchestrator is resumable, idempotent on delivery and reproducible."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from revisionist.core import delta as d
from revisionist.core import events as ev
from revisionist.core.fold import replay, state_hash
from revisionist.core.ids import DeltaId, IntentId, derived_id
from revisionist.core.intent import Intent, Verb
from revisionist.core.ontology import ClockKind, ClockStatus
from revisionist.core.referee import Roll
from revisionist.core.resolution import OutcomeTier, RejectionReason, TickStage
from revisionist.core.state import State
from revisionist.core.visibility import PUBLIC, private_to
from revisionist.engine import tick as tick_mod
from revisionist.engine.providers import (
    FailingResolver,
    KeywordSafety,
    RecordingDeliverer,
    ScriptedResolver,
    TemplateNarrator,
)
from revisionist.engine.store import EventStore, MemoryStore, SqliteStore, load_state
from revisionist.engine.tick import Providers, file_intent, run_tick
from tests import fixtures as fx

STAGES = list(TickStage)


@pytest.fixture(params=["memory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[EventStore]:
    if request.param == "memory":
        s: EventStore = MemoryStore()
        s.append_all(fx.creation_events())
        yield s
    else:
        sq = SqliteStore(tmp_path / "campaign.db")
        sq.append_all(fx.creation_events())
        yield sq
        sq.close()


def _intent(verb: Verb, actor: fx.SeatId, n: int, tick: int = 0, **kw: object) -> Intent:
    return Intent(
        intent_id=IntentId(f"01ARZ3NDEKTSV4RRFFQ69T{n:05d}"),
        actor=actor,
        verb=verb,
        scope=PUBLIC,
        filed_tick=tick,
        **kw,
    )


def _script(state: State, intent: Intent, roll: Roll) -> list[d.Delta]:
    """A deterministic resolver: every intent nudges North's stability and a disposition."""
    n = int(intent.intent_id[-5:])
    sign = -1 if roll.outcome is OutcomeTier.FAIL_COMPLICATION else 1
    return [
        d.MeterDelta(
            delta_id=DeltaId(derived_id("m", state.tick, intent.intent_id)),
            reason=f"{intent.verb} by {intent.actor[-3:]}",
            meter_id=fx.M_NORTH_STABILITY,
            change=sign * (1 + n % 3),
        ),
        d.DispositionDelta(
            delta_id=DeltaId(derived_id("r", state.tick, intent.intent_id)),
            reason="tensions",
            relationship_id=fx.R_NORTH_SOUTH,
            change=-sign,
        ),
    ]


def _providers(resolver: object | None = None) -> tuple[Providers, RecordingDeliverer]:
    deliverer = RecordingDeliverer()
    p = Providers(
        resolver=resolver or ScriptedResolver(_script),  # type: ignore[arg-type]
        narrator=TemplateNarrator(),
        deliverer=deliverer,
        safety=KeywordSafety(),
    )
    return p, deliverer


def _file_some(store: EventStore, tick: int, n0: int) -> None:
    assert file_intent(store, fx.CAMPAIGN, _intent(Verb.DECLARE, fx.NORTH, n0, tick)) is None
    assert (
        file_intent(
            store,
            fx.CAMPAIGN,
            _intent(
                Verb.SABOTAGE,
                fx.SOUTH,
                n0 + 1,
                tick,
                targets=(fx.A_NORTH_ARMY,),
                means=(fx.A_SOUTH_GUARD,),
                duration_ticks=2,
            ),
        )
        is None
    )
    assert file_intent(store, fx.CAMPAIGN, _intent(Verb.WAIT, fx.GUILD, n0 + 2, tick)) is None


# --- one tick ------------------------------------------------------------------


def test_one_full_tick(store: EventStore) -> None:
    _file_some(store, 0, 1)
    providers, deliverer = _providers()
    result = run_tick(store, fx.CAMPAIGN, providers)
    assert not result.quiet and result.resumed_from is None
    assert result.applied >= 3 and result.rejected == ()
    assert result.delivered == tuple(sorted((fx.NORTH, fx.SOUTH, fx.HERALD)))
    assert {s for s, _, _ in deliverer.sent} == {fx.NORTH, fx.SOUTH, fx.HERALD}
    state = load_state(store, fx.CAMPAIGN)
    assert state.tick == 1 and state.pending == {} and state.frozen == ()
    kinds = [e.kind for e in store.read(fx.CAMPAIGN)]
    assert kinds.count("tick_stage_completed") == len(STAGES)
    assert kinds[-1] == "tick_closed"
    # replay from scratch reaches the hash the log recorded (tick_closed hashes the
    # state before it closes; the fold also checks this and raises on mismatch)
    events = list(store.read(fx.CAMPAIGN))
    assert state_hash(replay(events[:-1])) == result.state_hash
    # the two-tick sabotage opens an operation for South unless its roll was a failure
    rolled = next(
        e.payload
        for e in events
        if isinstance(e.payload, ev.Rolled) and e.payload.intent_id.endswith("00002")
    )
    ops = [
        c for c in state.clocks.values() if c.kind is ClockKind.OPERATION and c.owner == fx.SOUTH
    ]
    if rolled.outcome is OutcomeTier.FAIL_COMPLICATION:
        assert ops == []
    else:
        assert len(ops) == 1 and ops[0].segments_filled == 1 and ops[0].status is ClockStatus.OPEN


def test_narration_never_contains_the_canary(store: EventStore) -> None:
    _file_some(store, 0, 1)
    providers, deliverer = _providers()
    run_tick(store, fx.CAMPAIGN, providers)
    south_text = next(t for s, t, _ in deliverer.sent if s == fx.SOUTH)
    assert fx.CANARY not in south_text
    assert "North" in south_text or "South" in south_text


def test_filing_rejections_are_logged_and_safety_runs(store: EventStore) -> None:
    bad = _intent(Verb.ATTACK, fx.SOUTH, 7, means=(fx.A_NORTH_ARMY,))
    rej = file_intent(store, fx.CAMPAIGN, bad)
    assert rej is not None and rej.reason is RejectionReason.MEANS_NOT_HELD
    hot = _intent(Verb.DECLARE, fx.NORTH, 8, spin="we will use torture on them")
    rej = file_intent(store, fx.CAMPAIGN, hot, safety=KeywordSafety(extra=("torture",)))
    assert rej is not None and rej.reason is RejectionReason.SAFETY_LINE
    kinds = [e.kind for e in store.read(fx.CAMPAIGN)]
    assert kinds[-2:] == ["intent_rejected", "intent_rejected"]
    assert load_state(store, fx.CAMPAIGN).pending == {}


def test_quiet_tick_carries_intents_forward(store: EventStore) -> None:
    _file_some(store, 0, 1)
    failing = FailingResolver("model down")
    providers, deliverer = _providers(failing)
    result = run_tick(store, fx.CAMPAIGN, providers)
    assert result.quiet and "model down" in result.quiet_reason
    assert failing.calls == 2  # one retry
    assert deliverer.sent == []
    state = load_state(store, fx.CAMPAIGN)
    assert state.tick == 1 and len(state.pending) == 3 and state.frozen == ()
    # next tick with a working resolver picks them up
    good, _ = _providers()
    result2 = run_tick(store, fx.CAMPAIGN, good)
    assert not result2.quiet and result2.applied >= 3
    assert load_state(store, fx.CAMPAIGN).pending == {}


# --- resume ------------------------------------------------------------------


@pytest.mark.parametrize("crash_after", STAGES)
def test_crash_after_every_stage_then_resume(store: EventStore, crash_after: TickStage) -> None:
    _file_some(store, 0, 1)
    providers, deliverer = _providers()
    partial = run_tick(store, fx.CAMPAIGN, providers, stop_after=crash_after)
    assert partial.stopped_after is crash_after
    assert load_state(store, fx.CAMPAIGN).tick == 0  # not closed
    resumed = run_tick(store, fx.CAMPAIGN, providers)
    assert resumed.resumed_from is crash_after
    assert not resumed.quiet

    # An uninterrupted run on a fresh store reaches the identical hash and log shape.
    clean: EventStore = MemoryStore()
    clean.append_all(fx.creation_events())
    _file_some(clean, 0, 1)
    clean_providers, clean_deliverer = _providers()
    straight = run_tick(clean, fx.CAMPAIGN, clean_providers)
    assert resumed.state_hash == straight.state_hash
    assert [e.kind for e in store.read(fx.CAMPAIGN)] == [e.kind for e in clean.read(fx.CAMPAIGN)]
    # exactly one delivery per human seat, whichever path was taken
    assert sorted(s for s, _, _ in deliverer.sent) == sorted(s for s, _, _ in clean_deliverer.sent)
    assert len(deliverer.sent) == 3


def test_deliver_twice_sends_once(store: EventStore) -> None:
    _file_some(store, 0, 1)
    providers, deliverer = _providers()
    run_tick(store, fx.CAMPAIGN, providers, stop_after=TickStage.NARRATE)
    # Simulate a crash mid-DELIVER: one briefing landed, the stage did not complete.
    state = load_state(store, fx.CAMPAIGN)
    em = tick_mod._Emitter(store, state, tick_mod._VERSIONS)
    narrations = tick_mod._narrations(
        em, store, fx.CAMPAIGN, tick_mod._tail(list(store.read(fx.CAMPAIGN)))
    )
    seat, (narration_id, text) = sorted(narrations.items())[0]
    key = derived_id(fx.CAMPAIGN, state.tick, "deliver", seat)
    deliverer.deliver(seat, text, key)
    em.commit(
        [
            em.event(
                ev.BriefingDelivered(
                    seat_id=seat, narration=narration_id, channel="memory", idempotency_key=key
                ),
                actor=seat,
                scope=private_to(seat),
            )
        ]
    )
    run_tick(store, fx.CAMPAIGN, providers)
    keys = [k for _, _, k in deliverer.sent]
    assert len(keys) == len(set(keys)) == 3


# --- ten ticks ----------------------------------------------------------------


def test_ten_ticks_with_two_crashes_replay_to_the_same_hashes(store: EventStore) -> None:
    providers, deliverer = _providers()
    hashes: list[str] = []
    for tick in range(10):
        _file_some(store, tick, 100 + tick * 10)
        if tick == 3:
            run_tick(store, fx.CAMPAIGN, providers, stop_after=TickStage.PROPOSE)
        if tick == 7:
            run_tick(store, fx.CAMPAIGN, providers, stop_after=TickStage.APPLY)
        result = run_tick(store, fx.CAMPAIGN, providers)
        assert not result.quiet
        hashes.append(result.state_hash)
    assert load_state(store, fx.CAMPAIGN).tick == 10
    # Replay checks every tick_closed hash on the way (FoldError otherwise).
    events = list(store.read(fx.CAMPAIGN))
    final = replay(events)
    assert state_hash(replay(events[:-1])) == hashes[-1]
    closes = [e.payload for e in events if isinstance(e.payload, ev.TickClosed)]
    assert [c.state_hash for c in closes] == hashes
    assert len(deliverer.sent) == 30
    assert len({k for _, _, k in deliverer.sent}) == 30
    # The South operations resolved over their two ticks.
    resolved = [c for c in final.clocks.values() if c.kind is ClockKind.OPERATION]
    assert resolved and all(c.status is ClockStatus.RESOLVED for c in resolved[:-1])
    # Costs plumbed: one resolver call per tick, three narrator calls per tick.
    assert all(len(c.costs) == 4 for c in closes)
    assert all(sum(x.usd for x in c.costs) == 0 for c in closes)


# --- Layer 6: re-rolls and outlets ---------------------------------------------


def test_a_failed_reroll_abandons_the_operation(store: EventStore) -> None:
    _file_some(store, 0, 1)
    providers, _ = _providers()
    first = run_tick(store, fx.CAMPAIGN, providers)
    assert not first.quiet
    opened = [
        c
        for c in load_state(store, fx.CAMPAIGN).clocks.values()
        if c.kind is ClockKind.OPERATION and c.intent_id is not None
    ]
    if not opened:
        return  # the opening roll was itself a failure; nothing to re-roll
    _file_some(store, 1, 20)
    failure = Roll(
        seed="0" * 32, outcome=OutcomeTier.FAIL_COMPLICATION, exposure_roll=0, exposed=False
    )
    second = run_tick(store, fx.CAMPAIGN, providers, roll_for=failure)
    assert not second.quiet
    state = load_state(store, fx.CAMPAIGN)
    clock = state.clocks[opened[0].clock_id]
    assert clock.status is ClockStatus.FAILED and clock.holds_slot is False


def test_outlet_narration_is_public_and_cannot_see_secrets(store: EventStore) -> None:
    _file_some(store, 0, 1)
    providers, deliverer = _providers()
    assert not run_tick(store, fx.CAMPAIGN, providers).quiet
    events = list(store.read(fx.CAMPAIGN))
    herald = next(
        e
        for e in events
        if isinstance(e.payload, ev.NarrationWritten) and e.payload.seat_id == fx.HERALD
    )
    assert herald.scope.visible_to(fx.SOUTH) and herald.scope.visible_to(fx.NORTH)
    assert fx.CANARY not in herald.payload.text  # type: ignore[union-attr]
    sent = next(text for seat, text, _ in deliverer.sent if seat == fx.HERALD)
    assert fx.CANARY not in sent and fx.A_NORTH_SECRET_ARMY not in sent


def test_a_delta_the_fold_would_refuse_is_cut_not_fatal(store: EventStore) -> None:
    """Live failure, salt-strait tick 1: the resolver asserted a fact about an asset.

    The fold wants an entity as a fact's subject. Before the admissibility pass the
    delta survived clamp, the APPLY batch failed to fold, and every retry failed the
    same way. Now that one delta is rejected and the rest of the tick lands.
    """

    def script(state: State, intent: Intent, roll: Roll) -> list[d.Delta]:
        return [
            d.FactAssert(
                delta_id=DeltaId(derived_id("bad", state.tick, intent.intent_id)),
                reason="names an asset where an entity belongs",
                fact_id=fx.FactId(derived_id("f", state.tick, intent.intent_id)),
                subject=fx.EntityId(fx.A_NORTH_ARMY),
                predicate="is_weak_to",
                obj="storm surge",
                scope=PUBLIC,
            ),
            d.FactAssert(
                delta_id=DeltaId(derived_id("dup", state.tick, intent.intent_id)),
                reason="reuses an existing fact ID",
                fact_id=fx.F_PUBLIC_TREATY,
                subject=fx.E_NORTH,
                predicate="signed",
                obj="treaty",
                scope=PUBLIC,
            ),
            *_script(state, intent, roll),
        ]

    _file_some(store, 0, 1)
    providers, _ = _providers(ScriptedResolver(script))
    result = run_tick(store, fx.CAMPAIGN, providers)
    assert not result.quiet and result.applied >= 1
    events = list(store.read(fx.CAMPAIGN))
    refused = [
        e.payload
        for e in events
        if isinstance(e.payload, ev.DeltaRejected)
        and e.payload.reason is RejectionReason.TARGET_UNKNOWN
    ]
    assert any("unknown entity" in r.detail for r in refused)
    assert any("duplicate fact" in r.detail for r in refused)
    assert load_state(store, fx.CAMPAIGN).tick == 1
    assert state_hash(replay(events[:-1])) == result.state_hash


def test_spin_goes_public_once_when_the_order_freezes(store: EventStore) -> None:
    declare = _intent(Verb.DECLARE, fx.NORTH, 1, spin="The passes stay open.")
    sabotage = _intent(
        Verb.SABOTAGE,
        fx.SOUTH,
        2,
        targets=(fx.A_NORTH_ARMY,),
        means=(fx.A_SOUTH_GUARD,),
        duration_ticks=3,
        spin="We seek only peace.",
    ).model_copy(update={"scope": private_to(fx.SOUTH, covert=True)})
    quiet = _intent(Verb.WAIT, fx.GUILD, 3)
    for intent in (declare, sabotage, quiet):
        assert file_intent(store, fx.CAMPAIGN, intent) is None
    for _ in range(3):  # the sabotage may run as an operation, frozen again each tick
        run_tick(store, fx.CAMPAIGN, _providers()[0])
    statements = [e for e in store.read(fx.CAMPAIGN) if isinstance(e.payload, ev.StatementIssued)]
    assert sorted((e.payload.seat_id, e.payload.text) for e in statements) == sorted(  # type: ignore[union-attr]
        [(fx.NORTH, "The passes stay open."), (fx.SOUTH, "We seek only peace.")]
    )
    assert all(e.tick == 0 and e.scope == PUBLIC for e in statements)
