"""Hypothesis strategies for every core model. Reused by Layers 2 and 3."""

from typing import cast

from hypothesis import strategies as st

from anansi.core import delta as d
from anansi.core import events as ev
from anansi.core.ids import (
    AssetId,
    CampaignId,
    ClockId,
    CommitmentId,
    DeltaId,
    EntityId,
    EventId,
    FactId,
    IntentId,
    MeterId,
    RelationshipId,
    ScheduledEventId,
    SeatId,
)
from anansi.core.intent import Intent, Verb
from anansi.core.ontology import (
    Asset,
    AssetKind,
    Clause,
    ClauseKind,
    Clock,
    ClockKind,
    ClockStatus,
    Commitment,
    Entity,
    EntityKind,
    Fact,
    Meter,
    Persona,
    Relationship,
    ScheduledEvent,
    Seat,
    SeatKind,
)
from anansi.core.settings import Settings
from anansi.core.visibility import Scope, Visibility

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
ulid = st.text(alphabet=CROCKFORD, min_size=26, max_size=26)

campaign_ids = ulid.map(CampaignId)
event_ids = ulid.map(EventId)
entity_ids = ulid.map(EntityId)
asset_ids = ulid.map(AssetId)
relationship_ids = ulid.map(RelationshipId)
meter_ids = ulid.map(MeterId)
clock_ids = ulid.map(ClockId)
fact_ids = ulid.map(FactId)
scheduled_ids = ulid.map(ScheduledEventId)
commitment_ids = ulid.map(CommitmentId)
seat_ids = ulid.map(SeatId)
intent_ids = ulid.map(IntentId)
delta_ids = ulid.map(DeltaId)

short_text = st.text(min_size=1, max_size=40)
tags = st.frozensets(short_text, max_size=3)


@st.composite
def scopes(draw: st.DrawFn) -> Scope:
    vis = draw(st.sampled_from(list(Visibility)))
    if vis is Visibility.PUBLIC:
        return Scope(visibility=vis)
    holders = draw(st.frozensets(seat_ids, min_size=1, max_size=4))
    return Scope(visibility=vis, known_by=holders)


settings = st.builds(Settings, lines=tags.map(tuple), veils=tags.map(tuple), tone=short_text)

entities = st.builds(
    Entity,
    entity_id=entity_ids,
    kind=st.sampled_from(list(EntityKind)),
    name=short_text,
    parent=st.none() | entity_ids,
    location=st.none() | entity_ids,
    tags=tags,
)

assets = st.builds(
    Asset,
    asset_id=asset_ids,
    kind=st.sampled_from(list(AssetKind)),
    name=short_text,
    tier=st.integers(0, 5),
    controller=entity_ids,
    location=st.none() | entity_ids,
    loyalty=st.integers(-5, 5),
    scope=scopes(),
)

relationships = st.builds(
    Relationship,
    relationship_id=relationship_ids,
    a=entity_ids,
    b=entity_ids,
    disposition=st.integers(-5, 5),
    price_tags=tags,
    leverage_tags=tags,
    scope=scopes(),
)

meters = st.builds(
    Meter,
    meter_id=meter_ids,
    owner=entity_ids,
    name=short_text,
    value=st.integers(0, 100),
    max_delta_per_tick=st.integers(1, 25),
    scope=scopes(),
)

clocks = st.builds(
    Clock,
    clock_id=clock_ids,
    kind=st.sampled_from(list(ClockKind)),
    name=short_text,
    owner=st.none() | seat_ids,
    target=st.none() | entity_ids,
    intent_id=st.none() | intent_ids,
    opened_tick=st.integers(0, 200),
    segments_total=st.integers(1, 12),
    segments_filled=st.integers(0, 12),
    eta_tick=st.none() | st.integers(0, 200),
    exposure=st.integers(0, 5),
    holds_slot=st.booleans(),
    scope=scopes(),
)

facts = st.builds(
    Fact,
    fact_id=fact_ids,
    subject=entity_ids,
    predicate=short_text,
    obj=entity_ids | asset_ids | clock_ids | short_text,
    truth=st.booleans(),
    exposure=st.integers(0, 5),
    asserted_tick=st.integers(0, 200),
    scope=scopes(),
    canary=st.none() | ulid,
)

scheduled_events = st.builds(
    ScheduledEvent,
    scheduled_id=scheduled_ids,
    fire_at_tick=st.integers(0, 500),
    name=short_text,
    source_intent=st.none() | intent_ids,
    source_clock=st.none() | clock_ids,
    scope=scopes(),
)

clauses = st.builds(
    Clause,
    kind=st.sampled_from(list(ClauseKind)),
    party=entity_ids,
    beneficiary=entity_ids,
    asset=st.none() | asset_ids,
    verb=st.none() | st.sampled_from(list(Verb)),
    amount=st.none() | st.integers(0, 5),
    until_tick=st.none() | st.integers(0, 500),
)

commitments = st.builds(
    Commitment,
    commitment_id=commitment_ids,
    parties=st.frozensets(entity_ids, min_size=2, max_size=4),
    clauses=st.lists(clauses, min_size=1, max_size=4).map(tuple),
    scope=scopes(),
)

personas = st.builds(
    Persona,
    name=short_text,
    role=short_text,
    voice=short_text,
    backstory=short_text,
    relationship=short_text,
)

seats = st.builds(
    Seat,
    seat_id=seat_ids,
    kind=st.sampled_from(list(SeatKind)),
    name=short_text,
    embodies=entity_ids,
    slots=st.integers(0, 5),
    agent=st.none() | entity_ids,
    persona=st.none() | personas,
    objectives=st.frozensets(clock_ids, max_size=3),
    standing_orders=st.lists(short_text, max_size=3).map(tuple),
    joined_tick=st.integers(0, 200),
)

world_object_ids = (
    entity_ids | asset_ids | relationship_ids | meter_ids | clock_ids | fact_ids | commitment_ids
)

intents = st.builds(
    Intent,
    intent_id=intent_ids,
    actor=seat_ids,
    verb=st.sampled_from(list(Verb)),
    targets=st.lists(world_object_ids, max_size=4).map(tuple),
    means=st.lists(asset_ids, max_size=3).map(tuple),
    counterparty=st.none() | entity_ids,
    duration_ticks=st.integers(1, 12),
    scope=scopes(),
    spin=st.none() | st.text(max_size=280),
    condition=st.none() | short_text,
    filed_tick=st.integers(0, 500),
)

_delta_common = {"delta_id": delta_ids, "reason": st.text(max_size=200)}

deltas = st.one_of(
    st.builds(d.AssetTierDelta, asset_id=asset_ids, change=st.integers(-2, 2), **_delta_common),
    st.builds(d.AssetStatusSet, asset_id=asset_ids, **_delta_common),
    st.builds(d.AssetControllerSet, asset_id=asset_ids, controller=entity_ids, **_delta_common),
    st.builds(
        d.AssetCreate,
        asset_id=asset_ids,
        name=short_text,
        tier=st.integers(0, 1),
        controller=entity_ids,
        scope=scopes(),
        **_delta_common,
    ),
    st.builds(d.MeterDelta, meter_id=meter_ids, change=st.integers(-50, 50), **_delta_common),
    st.builds(
        d.DispositionDelta,
        relationship_id=relationship_ids,
        change=st.integers(-2, 2),
        **_delta_common,
    ),
    st.builds(
        d.RelationshipTagSet,
        relationship_id=relationship_ids,
        add_price=tags,
        add_leverage=tags,
        remove_leverage=tags,
        **_delta_common,
    ),
    st.builds(d.ClockAdvance, clock_id=clock_ids, segments=st.integers(-2, 2), **_delta_common),
    st.builds(
        d.ClockCreate,
        clock_id=clock_ids,
        name=short_text,
        owner=st.none() | seat_ids,
        segments_total=st.integers(1, 12),
        exposure=st.integers(0, 5),
        scope=scopes(),
        **_delta_common,
    ),
    st.builds(
        d.ClockStatusSet,
        clock_id=clock_ids,
        status=st.sampled_from([ClockStatus.FAILED, ClockStatus.ABANDONED]),
        **_delta_common,
    ),
    st.builds(d.ClockExposureDelta, clock_id=clock_ids, change=st.integers(-2, 2), **_delta_common),
    st.builds(
        d.FactAssert,
        fact_id=fact_ids,
        subject=entity_ids,
        predicate=short_text,
        obj=entity_ids | asset_ids | clock_ids | short_text,
        exposure=st.integers(0, 5),
        scope=scopes(),
        **_delta_common,
    ),
    st.builds(
        d.KnowledgeGrant,
        target=fact_ids | asset_ids | clock_ids | commitment_ids,
        seats=st.frozensets(seat_ids, min_size=1, max_size=4),
        **_delta_common,
    ),
    st.builds(d.EntityStatusSet, entity_id=entity_ids, **_delta_common),
    st.builds(
        d.CommitmentCreate,
        commitment_id=commitment_ids,
        parties=st.frozensets(entity_ids, min_size=2, max_size=4),
        clauses=st.lists(clauses, min_size=1, max_size=6).map(tuple),
        scope=scopes(),
        **_delta_common,
    ),
    st.builds(d.CommitmentStatusSet, commitment_id=commitment_ids, **_delta_common),
    st.builds(
        d.ScheduledEventQueue,
        scheduled_id=scheduled_ids,
        fire_in_ticks=st.integers(1, 24),
        name=short_text,
        source_clock=st.none() | clock_ids,
        scope=scopes(),
        **_delta_common,
    ),
    st.builds(d.SeatSlotsDelta, seat_id=seat_ids, change=st.integers(-2, 2), **_delta_common),
)

versions = st.builds(ev.Versions, engine=short_text, schema_version=short_text, prompt=short_text)

call_costs = st.builds(
    ev.CallCost,
    call_class=short_text,
    model=short_text,
    input_tokens=st.integers(0, 10**6),
    output_tokens=st.integers(0, 10**6),
    cached_tokens=st.integers(0, 10**6),
    usd=st.floats(0, 100, allow_nan=False),
)

payloads = st.one_of(
    st.builds(ev.CampaignCreated, name=short_text, world=short_text),
    st.builds(ev.SettingsSet, settings=settings),
    st.builds(ev.EntityCreated, entity=entities),
    st.builds(ev.AssetCreated, asset=assets),
    st.builds(ev.RelationshipSet, relationship=relationships),
    st.builds(ev.MeterCreated, meter=meters),
    st.builds(ev.ClockCreated, clock=clocks),
    st.builds(ev.FactAsserted, fact=facts),
    st.builds(ev.CommitmentCreated, commitment=commitments),
    st.builds(ev.SeatCreated, seat=seats),
    st.builds(ev.AgentAssigned, seat_id=seat_ids, agent=entity_ids, persona=personas),
    st.builds(ev.SeatStatusSet, seat_id=seat_ids, cause=short_text),
    st.builds(ev.OrderReceived, seat_id=seat_ids, text=st.text(max_size=400)),
    st.builds(ev.IntentFiled, intent=intents, from_order=st.none() | event_ids),
    st.builds(ev.IntentRejected, intent_id=intent_ids, detail=short_text),
    st.builds(ev.ClarificationAsked, seat_id=seat_ids, from_order=event_ids, question=short_text),
    st.builds(ev.FeasibilityRated, intent_id=intent_ids),
    st.builds(
        ev.Rolled,
        intent_id=intent_ids,
        seed=ulid,
        exposure_roll=st.integers(0, 99),
        exposed=st.booleans(),
    ),
    st.builds(
        ev.DeltaProposed,
        intent_id=st.none() | intent_ids,
        deltas=st.lists(deltas, max_size=4).map(tuple),
    ),
    st.builds(ev.DeltaRejected, delta_id=delta_ids, detail=short_text),
    st.builds(ev.DeltaApplied, intent_id=st.none() | intent_ids, delta=deltas),
    st.builds(ev.OperationOpened, intent_id=intent_ids, clock_id=clock_ids, seat_id=seat_ids),
    st.builds(ev.OperationResolved, intent_id=intent_ids, clock_id=clock_ids, seat_id=seat_ids),
    st.builds(ev.ScheduledEventFired, scheduled_id=scheduled_ids),
    st.builds(
        ev.NarrationWritten,
        seat_id=seat_ids,
        text=st.text(max_size=400),
        from_deltas=st.lists(delta_ids, max_size=4).map(tuple),
    ),
    st.builds(
        ev.BriefingDelivered,
        seat_id=seat_ids,
        narration=event_ids,
        channel=short_text,
        idempotency_key=ulid,
    ),
    st.builds(ev.TickOpened, intents_frozen=st.lists(intent_ids, max_size=6).map(tuple)),
    st.builds(ev.TickStageCompleted),
    st.builds(
        ev.TickClosed,
        quiet=st.booleans(),
        costs=st.lists(call_costs, max_size=3).map(tuple),
        state_hash=ulid,
    ),
    st.builds(
        ev.AdminRead,
        actor=short_text,
        events=st.lists(event_ids, max_size=4).map(tuple),
        purpose=short_text,
    ),
)

events = st.builds(
    ev.Event,
    event_id=event_ids,
    campaign_id=campaign_ids,
    seq=st.integers(0, 10**6),
    tick=st.integers(0, 500),
    actor=st.none() | seat_ids,
    scope=scopes(),
    payload=payloads,
    versions=versions,
    payload_enc=st.none() | st.binary(max_size=32),
)

ALL_MODEL_STRATEGIES: dict[str, st.SearchStrategy[object]] = {
    "Settings": cast(st.SearchStrategy[object], settings),
    "Entity": cast(st.SearchStrategy[object], entities),
    "Asset": cast(st.SearchStrategy[object], assets),
    "Relationship": cast(st.SearchStrategy[object], relationships),
    "Meter": cast(st.SearchStrategy[object], meters),
    "Clock": cast(st.SearchStrategy[object], clocks),
    "Fact": cast(st.SearchStrategy[object], facts),
    "ScheduledEvent": cast(st.SearchStrategy[object], scheduled_events),
    "Commitment": cast(st.SearchStrategy[object], commitments),
    "Seat": cast(st.SearchStrategy[object], seats),
    "Intent": cast(st.SearchStrategy[object], intents),
    "Event": cast(st.SearchStrategy[object], events),
}
