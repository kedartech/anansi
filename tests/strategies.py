"""Hypothesis strategies for every core model. Reused by Layers 2 and 3."""

from datetime import date, timedelta
from typing import cast

from hypothesis import strategies as st

from revisionist.core import delta as d
from revisionist.core import events as ev
from revisionist.core import goals as g
from revisionist.core.ids import (
    AssetId,
    CampaignId,
    ClockId,
    CommitmentId,
    ContestId,
    DeltaId,
    DocumentId,
    EntityId,
    EventId,
    FactId,
    FeedId,
    GoalId,
    IntentId,
    MeterId,
    OrderId,
    PipelineId,
    QuantityId,
    RelationshipId,
    ScheduledEventId,
    SeatId,
)
from revisionist.core.intent import Intent, Verb
from revisionist.core.ontology import (
    Asset,
    AssetKind,
    Character,
    Clause,
    ClauseKind,
    Clock,
    ClockKind,
    ClockStatus,
    Commitment,
    Contest,
    ContestKind,
    Entity,
    EntityKind,
    EntityStatus,
    Fact,
    Meter,
    Persona,
    Relationship,
    ScheduledEvent,
    ScheduleSource,
    Seat,
    SeatKind,
)
from revisionist.core.realism import card as rc
from revisionist.core.realism import document as rd
from revisionist.core.realism import pipeline as rp
from revisionist.core.realism.quantity import RESOLVER_KEYS, Quantity
from revisionist.core.settings import Settings
from revisionist.core.visibility import Scope, Visibility

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
contest_ids = ulid.map(ContestId)
commitment_ids = ulid.map(CommitmentId)
seat_ids = ulid.map(SeatId)
intent_ids = ulid.map(IntentId)
delta_ids = ulid.map(DeltaId)
goal_ids = ulid.map(GoalId)
document_ids = ulid.map(DocumentId)
order_ids = ulid.map(OrderId)
quantity_ids = ulid.map(QuantityId)
pipeline_ids = ulid.map(PipelineId)
feed_ids = ulid.map(FeedId)

short_text = st.text(min_size=1, max_size=40)
tags = st.frozensets(short_text, max_size=3)


@st.composite
def scopes(draw: st.DrawFn) -> Scope:
    vis = draw(st.sampled_from(list(Visibility)))
    if vis is Visibility.PUBLIC:
        return Scope(visibility=vis)
    holders = draw(st.frozensets(seat_ids, min_size=1, max_size=4))
    return Scope(visibility=vis, known_by=holders)


in_world_dates = st.dates(min_value=date(1, 1, 1), max_value=date(2025, 12, 31))

settings = st.builds(
    Settings,
    lines=tags.map(tuple),
    veils=tags.map(tuple),
    tone=short_text,
    end_date=st.none() | in_world_dates,
    real_days=st.none() | st.integers(1, 365),
)

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
        intent_id=st.none() | intent_ids,
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

ops = st.sampled_from(["ge", "le"])

condition_leaves = st.one_of(
    st.builds(g.HoldsOffice, holder=entity_ids, office=asset_ids),
    st.builds(g.ControlsAsset, holder=entity_ids, asset=asset_ids),
    st.builds(g.EntityIs, entity=entity_ids, status=st.sampled_from(list(EntityStatus))),
    st.builds(g.MeterAt, meter=meter_ids, op=ops, value=st.integers(-50, 150)),
    st.builds(g.CommitmentInForce, commitment=commitment_ids),
    st.builds(g.DispositionAt, relationship=relationship_ids, op=ops, value=st.integers(-5, 5)),
)


def _combine(
    children: st.SearchStrategy[g.WinCondition],
) -> st.SearchStrategy[g.WinCondition]:
    group = st.lists(children, min_size=1, max_size=3).map(tuple)
    return st.builds(g.AllOf, conditions=group) | st.builds(g.AnyOf, conditions=group)


conditions: st.SearchStrategy[g.WinCondition] = st.recursive(
    condition_leaves, _combine, max_leaves=6
).filter(lambda c: g.depth(c) <= g.MAX_DEPTH and len(g.leaves(c)) <= g.MAX_PARTS)


@st.composite
def goals(draw: st.DrawFn) -> g.Goal:
    condition = draw(conditions)
    seat = draw(seat_ids)
    return g.Goal(
        goal_id=draw(goal_ids),
        seat_id=seat,
        text=draw(short_text),
        condition=condition,
        kind=g.goal_kind(condition),
        clock_id=draw(clock_ids),
        met_tick=draw(st.none() | st.integers(0, 500)),
        holding=draw(st.booleans()),
        scope=Scope(visibility=Visibility.PRIVATE, known_by=frozenset({seat})),
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


@st.composite
def contests(draw: st.DrawFn) -> Contest:
    kind = draw(st.sampled_from(list(ContestKind)))
    n = 1 if kind is ContestKind.TRIAL else draw(st.integers(2, 4))
    sides = tuple(draw(st.lists(entity_ids, min_size=n, max_size=n, unique=True)))
    lo = 0 if kind is ContestKind.TRIAL else -100
    decided = draw(st.booleans())
    winner = draw(st.none() | st.sampled_from(sides)) if decided else None
    return Contest(
        contest_id=draw(contest_ids),
        kind=kind,
        name=draw(short_text),
        prize=draw(short_text),
        sides=sides,
        track=draw(st.integers(lo, 100)),
        momentum=draw(st.integers(-20, 20)),
        countdown_days=draw(st.integers(0, 3650)),
        decided=decided,
        winner=winner,
        scope=draw(scopes()),
    )


history_events = st.builds(
    ScheduledEvent,
    scheduled_id=scheduled_ids,
    fire_at_tick=st.integers(0, 500),
    name=short_text,
    scope=scopes(),
    source=st.just(ScheduleSource.HISTORY),
    on_date=in_world_dates,
    condition=st.none() | conditions,
    weight=st.integers(1, 3),
)

any_scheduled_events = scheduled_events | history_events

# --- realism ------------------------------------------------------------------

channels = st.sampled_from(list(rc.ChannelKind))
labels = st.text(min_size=1, max_size=60)


@st.composite
def param_specs(draw: st.DrawFn) -> rc.ParamSpec:
    kind = draw(st.sampled_from(list(rc.ParamKind)))
    name = draw(labels)
    if kind is rc.ParamKind.INT:
        lo = draw(st.integers(-1000, 1000))
        return rc.ParamSpec(name=name, kind=kind, lo=lo, hi=lo + draw(st.integers(0, 1000)))
    if kind is rc.ParamKind.ENUM:
        choices = draw(st.lists(labels, min_size=1, max_size=4, unique=True))
        return rc.ParamSpec(name=name, kind=kind, choices=tuple(choices))
    return rc.ParamSpec(name=name, kind=kind)


levers = st.builds(
    rc.Lever,
    name=labels,
    pipeline=pipeline_ids,
    resolver=st.sampled_from(RESOLVER_KEYS),
    params=st.lists(param_specs(), max_size=3, unique_by=lambda p: p.name).map(tuple),
)

seat_cards = st.builds(
    rc.SeatCard,
    person=entity_ids,
    office=labels,
    playable=st.booleans(),
    powers=st.lists(labels, max_size=3).map(tuple),
    contacts=st.lists(st.builds(rc.Contact, person=entity_ids, channel=channels), max_size=3).map(
        tuple
    ),
    principals=st.lists(entity_ids, max_size=2).map(tuple),
    levers=st.lists(levers, max_size=2).map(tuple),
    feeds=st.lists(feed_ids, max_size=2).map(tuple),
    pipelines=st.lists(pipeline_ids, max_size=2).map(tuple),
    constraints=st.lists(labels, max_size=2).map(tuple),
    personal=st.lists(labels, max_size=2).map(tuple),
    successor=st.none() | entity_ids,
    horizon_days=st.integers(1, 365),
    voice=st.text(max_size=200),
    loyalty=st.integers(-5, 5),
    doctrine=st.lists(labels, max_size=2).map(tuple),
    interests=st.lists(labels, max_size=2).map(tuple),
)

biases = st.builds(
    rd.Bias,
    skew_pct=st.integers(-100, 100),
    noise_pct=st.integers(0, 100),
    omit_pct=st.integers(0, 100),
    delay_days=st.integers(0, 365),
)

feeds = st.builds(
    rd.Feed,
    feed_id=feed_ids,
    name=labels,
    author=entity_ids,
    kind=st.sampled_from(list(rd.DocumentKind)),
    channel=channels,
    classification=st.sampled_from(list(rd.Classification)),
    every_days=st.none() | st.integers(1, 365),
    latency_days=st.none() | st.integers(0, 365),
    reports=st.lists(quantity_ids, max_size=3).map(tuple),
    bias=biases,
)


@st.composite
def steps(draw: st.DrawFn) -> rp.Step:
    kind = draw(st.sampled_from(list(rp.StepKind)))
    allowed = frozenset({rp.StepAction.PASS})
    if kind is rp.StepKind.DECISION:
        allowed |= draw(st.frozensets(st.sampled_from(list(rp.StepAction))))
    return rp.Step(
        person=draw(entity_ids),
        kind=kind,
        days=draw(st.none() | st.integers(0, 365)),
        allowed=allowed,
    )


pipelines = st.builds(
    rp.Pipeline,
    pipeline_id=pipeline_ids,
    name=labels,
    steps=st.lists(steps(), min_size=1, max_size=4).map(tuple),
)

param_values = st.integers(-1000, 1000) | st.text(max_size=200)
param_maps = st.dictionaries(labels, param_values, max_size=3)


@st.composite
def step_outcomes(draw: st.DrawFn) -> rp.StepOutcome:
    action = draw(st.sampled_from(list(rp.StepAction)))
    amended = draw(st.dictionaries(labels, param_values, min_size=1, max_size=2))
    return rp.StepOutcome(
        step=draw(st.integers(0, 11)),
        person=draw(entity_ids),
        action=action,
        on=draw(in_world_dates),
        amended=amended if action is rp.StepAction.AMEND else {},
    )


orders = st.builds(
    rp.Order,
    order_id=order_ids,
    issuer=seat_ids,
    lever=labels,
    pipeline=pipeline_ids,
    params=param_maps,
    status=st.sampled_from(list(rp.OrderStatus)),
    step=st.integers(0, 12),
    filed=in_world_dates,
    outcomes=st.lists(step_outcomes(), max_size=3).map(tuple),
)


@st.composite
def document_views(draw: st.DrawFn) -> rd.DocumentView:
    issued = draw(in_world_dates)
    return rd.DocumentView(
        doc_id=draw(document_ids),
        kind=draw(st.sampled_from(list(rd.DocumentKind))),
        author=draw(entity_ids),
        issued=issued,
        arrives=issued + draw(st.timedeltas(min_value=timedelta(0), max_value=timedelta(60))),
        channel=draw(channels),
        classification=draw(st.sampled_from(list(rd.Classification))),
        title=draw(st.text(max_size=60)),
        body=tuple(
            draw(
                st.lists(
                    st.builds(
                        rd.BodyField,
                        label=labels,
                        value=st.none() | st.integers(-(10**6), 10**6) | labels,
                        unit=st.text(max_size=60),
                    ),
                    max_size=3,
                )
            )
        ),
        prose=draw(st.none() | st.text(max_size=400)),
        scope=draw(scopes()),
    )


truth_refs = st.builds(
    rd.TruthRef,
    quantity_id=quantity_ids,
    value=st.none() | st.integers(-(10**6), 10**6),
    canary=st.none() | ulid,
)


@st.composite
def documents_issued(draw: st.DrawFn) -> ev.DocumentIssued:
    view = draw(document_views())
    truth = rd.DocumentTruth(
        doc_id=view.doc_id,
        feed=draw(st.none() | feed_ids),
        truth_refs=tuple(draw(st.lists(truth_refs, max_size=3))),
        bias=draw(biases),
    )
    return ev.DocumentIssued(document=view, truth=truth)


quantities = st.builds(
    Quantity,
    quantity_id=quantity_ids,
    owner=st.none() | entity_ids,
    name=labels,
    value=st.none() | st.integers(-(10**9), 10**9),
    unit=labels,
    as_of=in_world_dates,
    scope=st.none() | scopes(),
)

realism_payloads = st.one_of(
    st.builds(ev.CampaignCreated, name=short_text, world=short_text, ruleset=st.just("realism")),
    st.builds(
        ev.CardsDeclared,
        cards=st.lists(seat_cards, max_size=2).map(tuple),
        feeds=st.lists(feeds, max_size=2).map(tuple),
        pipelines=st.lists(pipelines, max_size=2).map(tuple),
    ),
    documents_issued(),
    st.builds(ev.DocumentWritten, doc_id=document_ids, prose=st.text(min_size=1, max_size=400)),
    st.builds(ev.DocumentRead, seat_id=seat_ids, doc_id=document_ids),
    st.builds(ev.DocumentFlagged, seat_id=seat_ids, doc_id=document_ids, flagged=st.booleans()),
    st.builds(ev.OrderDrafted, order=orders),
    st.builds(ev.OrderStepTaken, order_id=order_ids, outcome=step_outcomes()),
    st.builds(ev.OrderSettled, order_id=order_ids, status=st.sampled_from(sorted(rp.SETTLED))),
    st.builds(ev.QuantitySet, quantity=quantities),
    st.builds(
        ev.CounselHeld,
        seat_id=seat_ids,
        person=entity_ids,
        channel=channels,
        text=st.text(max_size=400),
    ),
)

payloads = st.one_of(
    realism_payloads,
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
    st.builds(ev.ContestCreated, contest=contests()),
    st.builds(
        ev.ContestMoved,
        contest_id=contest_ids,
        change=st.integers(-200, 200),
        days=st.integers(0, 400),
        cause=st.sampled_from(["momentum", "order"]),
    ),
    st.builds(ev.ContestDecided, contest_id=contest_ids, winner=st.none() | entity_ids),
    st.builds(ev.ScheduledEventCreated, scheduled=any_scheduled_events),
    st.builds(ev.ScheduledEventSkipped, scheduled_id=scheduled_ids),
    st.builds(ev.DivergenceChanged, change=st.integers(-100, 100), reason=short_text),
    st.builds(ev.DateAdvanced, to=in_world_dates),
    st.builds(
        ev.NarrationWritten,
        seat_id=seat_ids,
        text=st.text(max_size=400),
        from_deltas=st.lists(delta_ids, max_size=4).map(tuple),
    ),
    st.builds(ev.StatementIssued, seat_id=seat_ids, intent_id=intent_ids, text=short_text),
    st.builds(
        ev.SessionZeroOpened, start=short_text, notes=st.lists(short_text, max_size=2).map(tuple)
    ),
    st.builds(
        ev.CharacterListed,
        character=st.builds(
            Character,
            entity_id=entity_ids,
            pitch=short_text,
            reach=short_text,
            composite=st.booleans(),
            means=st.frozensets(asset_ids, max_size=3),
            claimed_by=st.none() | seat_ids,
        ),
    ),
    st.builds(ev.CharacterClaimed, seat_id=seat_ids, entity_id=entity_ids),
    st.builds(ev.SessionZeroClosed),
    st.builds(
        ev.SeasonEnded,
        winners=st.lists(seat_ids, max_size=3).map(tuple),
        revealed=st.integers(0, 50),
    ),
    st.builds(ev.GoalSet, goal=goals()),
    st.builds(ev.GoalProgressed, goal_id=goal_ids, parts_met=st.integers(0, 8)),
    st.builds(ev.GoalMet, goal_id=goal_ids),
    st.builds(ev.GoalUndone, goal_id=goal_ids),
    st.builds(
        ev.GoalProposed,
        seat_id=seat_ids,
        route=st.sampled_from(["history", "could_have", "own"]),
        options=st.lists(
            conditions.map(lambda c: ev.GoalOption(text="goal", condition=c, kind=g.goal_kind(c))),
            max_size=3,
        ).map(tuple),
    ),
    st.builds(ev.GoalApproved, goal_id=goal_ids),
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
        ev.CostLogged,
        seat_id=st.none() | seat_ids,
        purpose=short_text,
        costs=st.lists(call_costs, max_size=3).map(tuple),
    ),
    st.builds(ev.Sealed, key_id=short_text),
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
    "ScheduledEvent": cast(st.SearchStrategy[object], any_scheduled_events),
    "Contest": cast(st.SearchStrategy[object], contests()),
    "Commitment": cast(st.SearchStrategy[object], commitments),
    "Seat": cast(st.SearchStrategy[object], seats),
    "Intent": cast(st.SearchStrategy[object], intents),
    "Goal": cast(st.SearchStrategy[object], goals()),
    "Event": cast(st.SearchStrategy[object], events),
    "SeatCard": cast(st.SearchStrategy[object], seat_cards),
    "Feed": cast(st.SearchStrategy[object], feeds),
    "Pipeline": cast(st.SearchStrategy[object], pipelines),
    "Order": cast(st.SearchStrategy[object], orders),
    "DocumentView": cast(st.SearchStrategy[object], document_views()),
    "Quantity": cast(st.SearchStrategy[object], quantities),
}
