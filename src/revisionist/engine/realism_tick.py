"""The realism tick (plan, Phase 2 unit 2f): the event-driven advance, end to end.

No model is called. Code walks the world from one wake to the next in four stages,
each landed in the log as one atomic batch ending in ``tick_stage_completed``:

    ADVANCE    the opening in-tray (first tick only), then ``date_advanced`` to the wake
    PIPELINES  day by day: orders move, history fires or skips, resolvers compute
    RESOLVE    goal changes, each private to the goal's owner
    FEEDS      any document still owed on the new today (the walk issues each feed
               on the day it falls due, so it waits in the in-tray until read)
    PROSE      one prose call per author for the pages issued since the last tick
               closed, from the pages and the author's voice only (Phase 4)

Resuming reads the tail of the log and continues from the first stage not marked
complete, so a resumed run reaches the hash an uninterrupted one would. PROSE runs
only when the caller passes a writer; a page whose prose is refused or whose call
fails keeps its body lines, and the tick still closes. Delivery comes later;
``TickResult.delivered`` is empty.
"""

import logging
from collections.abc import Sequence
from datetime import date, timedelta

from revisionist.core import events as ev
from revisionist.core import goals, history
from revisionist.core.events import CallCost, Event, Versions
from revisionist.core.fold import replay, state_hash
from revisionist.core.ids import (
    CampaignId,
    DocumentId,
    EntityId,
    EventId,
    OrderId,
    SeatId,
    derived_id,
)
from revisionist.core.ontology import Phase
from revisionist.core.realism.advance import Wake, next_wake
from revisionist.core.realism.card import ParamKind, ParamSpec, ParamValue
from revisionist.core.realism.document import DocumentView
from revisionist.core.realism.feeds import due_feeds, issue, seed_for_day
from revisionist.core.realism.pipeline import Order, OrderStatus, StepAction
from revisionist.core.realism.pipelines import human_step, run_pipelines
from revisionist.core.realism.resolvers import (
    ON_DAY,
    ON_ORDER,
    apply_changes,
    resolver_for,
)
from revisionist.core.resolution import TickStage
from revisionist.core.state import State
from revisionist.core.visibility import PUBLIC, Scope, private_to
from revisionist.engine import campaign as cmp
from revisionist.engine import session_zero
from revisionist.engine.emit import Emitter, tail
from revisionist.engine.ids import new_id
from revisionist.engine.llm import TransportError
from revisionist.engine.prose import ProseWriter, accepted, author_of, prose_prompt_version
from revisionist.engine.store import EventStore, load_state
from revisionist.engine.tick import NotInPlayError, TickResult

log = logging.getLogger(__name__)


class RealismTickResult(TickResult):
    wake: Wake | None = None
    """Where the advance stopped. None when a resumed tick passed ADVANCE in an earlier run."""
    written: int = 0
    """Pages given prose in this run."""
    prose_dropped: tuple[str, ...] = ()
    """``doc_id: reason`` for pages left without prose in this run."""


def _opening_date(events: Sequence[Event]) -> date | None:
    """The in-world date when the tick in progress opened: the last ``date_advanced``
    before its ``tick_opened``."""
    start, opened = 0, len(events)
    for i, e in enumerate(events):
        if e.kind == "tick_closed":
            start = i + 1
    for i in range(start, len(events)):
        if events[i].kind == "tick_opened":
            opened = i
            break
    found: date | None = None
    for e in events[:opened]:
        if isinstance(e.payload, ev.DateAdvanced):
            found = e.payload.to
    return found


def _unresolved(events: Sequence[Event]) -> list[OrderId]:
    """Orders enacted since the last PIPELINES stage closed: a human's final step,
    filed between ticks, whose resolver has not run.

    ``take_step`` now resolves the order in the step's own batch and marks that
    batch with the issuer as actor, so only a settlement without an actor (a log
    written before that) is left for the tick to resolve."""
    after = 0
    for i, e in enumerate(events):
        p = e.payload
        if isinstance(p, ev.TickStageCompleted) and p.stage is TickStage.PIPELINES:
            after = i + 1
    return [
        e.payload.order_id
        for e in events[after:]
        if isinstance(e.payload, ev.OrderSettled)
        and e.payload.status is OrderStatus.ENACTED
        and e.actor is None
    ]


def run_realism_tick(
    store: EventStore,
    campaign_id: CampaignId,
    *,
    versions: Versions | None = None,
    stop_after: TickStage | None = None,
    prose: ProseWriter | None = None,
) -> RealismTickResult:
    """Run (or resume) one realism tick. Safe to call again after a crash at any point.

    ``prose`` writes the pages; with none, the tick has no PROSE stage."""
    versions = versions or cmp.versions()
    state = load_state(store, campaign_id)
    if state.phase is not Phase.PLAY:
        raise NotInPlayError(state.phase)
    events = list(store.read(campaign_id))
    log_tail = tail(events)
    em = Emitter(store, state, versions)
    done = set(log_tail.completed)
    result = RealismTickResult(
        tick=state.tick, resumed_from=log_tail.completed[-1] if log_tail.completed else None
    )
    today = state.in_world_date
    if state.ruleset != "realism" or today is None:
        raise ValueError("run_realism_tick needs a realism campaign with an in-world date")
    before = _opening_date(events) if log_tail.opened else today
    assert before is not None

    def stopped(stage: TickStage) -> bool:
        return stop_after is stage

    def finish(stage: TickStage) -> RealismTickResult:
        return result.model_copy(update={"stopped_after": stage})

    if not log_tail.opened:
        em.commit([em.event(ev.TickOpened(intents_frozen=()))])

    if TickStage.ADVANCE not in done:
        batch: list[Event] = []
        if not any(d.truth.feed is not None for d in em.state.documents.values()):
            batch += _feed_documents(em, today)  # the opening in-tray, dated today
        local = replay(batch, initial=em.state) if batch else em.state
        wake = next_wake(local, today)
        batch.append(em.event(ev.DateAdvanced(to=wake.to)))
        batch.append(em.event(ev.TickStageCompleted(stage=TickStage.ADVANCE)))
        em.commit(batch)
        done.add(TickStage.ADVANCE)
        result = result.model_copy(update={"wake": wake})
        if stopped(TickStage.ADVANCE):
            return finish(TickStage.ADVANCE)

    new_today = em.state.in_world_date
    assert new_today is not None

    if TickStage.PIPELINES not in done:
        batch = _walk(em, events, before, new_today)
        batch.append(em.event(ev.TickStageCompleted(stage=TickStage.PIPELINES)))
        em.commit(batch)
        done.add(TickStage.PIPELINES)
        if stopped(TickStage.PIPELINES):
            return finish(TickStage.PIPELINES)

    if TickStage.RESOLVE not in done:
        batch = []
        for change in goals.check(em.state):
            owner = em.state.goals[change.goal_id].seat_id
            batch.append(em.event(change, scope=private_to(owner)))
        batch.append(em.event(ev.TickStageCompleted(stage=TickStage.RESOLVE)))
        em.commit(batch)
        done.add(TickStage.RESOLVE)
        if stopped(TickStage.RESOLVE):
            return finish(TickStage.RESOLVE)

    if TickStage.FEEDS not in done:
        batch = _feed_documents(em, new_today)
        batch.append(em.event(ev.TickStageCompleted(stage=TickStage.FEEDS)))
        em.commit(batch)
        done.add(TickStage.FEEDS)
        if stopped(TickStage.FEEDS):
            return finish(TickStage.FEEDS)

    costs = list(log_tail.costs)
    if prose is not None and TickStage.PROSE not in done:
        batch, spent, dropped = _prose(em, prose)
        costs += spent
        batch.append(em.event(ev.TickStageCompleted(stage=TickStage.PROSE, costs=tuple(spent))))
        em.commit(batch)
        done.add(TickStage.PROSE)
        result = result.model_copy(
            update={"written": len(batch) - 1, "prose_dropped": tuple(dropped)}
        )
        if stopped(TickStage.PROSE):
            return finish(TickStage.PROSE)

    digest = state_hash(em.state)
    em.commit([em.event(ev.TickClosed(state_hash=digest, costs=tuple(costs)))])
    em.store.save_snapshot(em.state)
    return result.model_copy(update={"state_hash": digest})


def _prose(em: Emitter, writer: ProseWriter) -> tuple[list[Event], list[CallCost], list[str]]:
    """``document_written`` for each page issued since the last tick closed that has no
    prose yet, one writer call per author. A failed call leaves its pages bare."""
    log_now = list(em.store.read(em.state.campaign_id))
    start = max((i + 1 for i, e in enumerate(log_now) if e.kind == "tick_closed"), default=0)
    issued = [
        e.payload.document.doc_id
        for e in log_now[start:]
        if isinstance(e.payload, ev.DocumentIssued)
    ]
    by_author: dict[EntityId, list[DocumentView]] = {}
    for doc_id in issued:
        page = em.state.documents[doc_id].view
        if page.prose is None:
            by_author.setdefault(page.author, []).append(page)
    versions = em.versions.model_copy(
        update={"prompt": ",".join(x for x in (em.versions.prompt, prose_prompt_version()) if x)}
    )
    batch: list[Event] = []
    costs: list[CallCost] = []
    dropped: list[str] = []
    for author_id in sorted(by_author):
        pages = sorted(by_author[author_id], key=lambda v: (v.issued, v.doc_id))
        try:
            written = writer.write(author_of(em.state, author_id, pages), pages)
        except TransportError as exc:
            log.warning("prose call for %s failed: %s", author_id, exc)
            dropped += [f"{p.doc_id}: call failed" for p in pages]
            continue
        if written.cost is not None:
            costs.append(written.cost)
        keep, refused = accepted(pages, written)
        dropped += [f"{r.doc_id}: {r.reason}" for r in refused]
        for page in pages:
            if page.doc_id in keep:
                e = em.event(
                    ev.DocumentWritten(doc_id=page.doc_id, prose=keep[page.doc_id]),
                    scope=page.scope,
                )
                batch.append(e.model_copy(update={"versions": versions}))
    return batch, costs, dropped


def _feed_documents(em: Emitter, today: date) -> list[Event]:
    """A document from every feed due on or before ``today``, dated ``today``.

    A feed that fell due during a collapsed stretch issues once, today, not once
    per skipped period."""
    seed = seed_for_day(em.state.campaign_id, today)
    out: list[Event] = []
    for feed in sorted(due_feeds(em.state, today), key=lambda f: f.feed_id):
        doc = issue(em.state, feed, today, seed)
        if doc is not None:
            out.append(em.event(doc, scope=doc.document.scope))
    return out


def _walk(em: Emitter, events: Sequence[Event], before: date, until: date) -> list[Event]:
    """Every day from ``before`` + 1 to ``until``, earliest first. Each day: orders
    move, history fires or skips what fell due (opinion reads fired incidents), the
    orders enacted that day are resolved, then what moves with time, then the feeds
    that fall due issue, dated that day. Each step sees
    the state after the ones before it."""
    state = em.state
    out: list[Event] = []

    def add(payloads: Sequence[ev.Payload]) -> None:
        nonlocal state
        batch = [
            em.event(p, scope=p.document.scope) if isinstance(p, ev.DocumentIssued) else em.event(p)
            for p in payloads
        ]
        state = replay(batch, initial=state)
        out.extend(batch)

    pending = _unresolved(events)
    for oid in pending:
        order = state.orders[oid]
        add(apply_changes(state, ON_ORDER[resolver_for(state, order)](state, order)))
    day = before
    while day < until:
        day += timedelta(days=1)
        steps = run_pipelines(state, day, seed_for_day(state.campaign_id, day))
        add(steps)
        add(history.settle(state, day))
        for step in steps:
            if isinstance(step, ev.OrderSettled) and step.status is OrderStatus.ENACTED:
                order = state.orders[step.order_id]
                add(apply_changes(state, ON_ORDER[resolver_for(state, order)](state, order)))
        for on_day in ON_DAY.values():
            add(apply_changes(state, on_day(state, day)))
        seed = seed_for_day(state.campaign_id, day)
        docs: list[Event] = []
        for feed in sorted(due_feeds(state, day), key=lambda f: f.feed_id):
            doc = issue(state, feed, day, seed)
            if doc is not None:
                docs.append(em.event(doc, scope=doc.document.scope))
        state = replay(docs, initial=state) if docs else state
        out.extend(docs)
    return out


# --- what a human does between ticks ---------------------------------------------


def wake_line(result: RealismTickResult, today: date | None) -> str:
    """The line the CLI and the web print after a realism tick."""
    woke = f"{result.wake.reason.label}, " if result.wake is not None else ""
    return f"tick {result.tick}: woke on {woke}now {today}"


def seat_player(store: EventStore, campaign_id: CampaignId, entity: EntityId) -> str:
    """Seat the human as ``entity``, then close Session Zero. Returns the person's
    name. Raises ``StepRefusedError``."""
    name = load_state(store, campaign_id).entities[entity].name
    seat = SeatId(derived_id(campaign_id, "seat", entity))
    session_zero.claim(store, campaign_id, seat, name)
    closed = load_state(store, campaign_id)
    store.append_all(
        [
            Event(
                event_id=EventId(new_id()),
                campaign_id=campaign_id,
                seq=closed.last_seq + 1,
                tick=closed.tick,
                scope=PUBLIC,
                payload=ev.SessionZeroClosed(),
                versions=cmp.versions(),
            )
        ]
    )
    return name


def _fits(spec: ParamSpec, raw: str) -> ParamValue:
    if spec.kind is ParamKind.INT:
        try:
            value = int(raw)
        except ValueError:
            raise ValueError(f"{spec.name}: {raw!r} is not a whole number") from None
        if spec.lo is None or spec.hi is None or not spec.lo <= value <= spec.hi:
            raise ValueError(f"{spec.name}: {raw} is outside {spec.lo}..{spec.hi}")
        return value
    if spec.kind is ParamKind.ENUM and raw not in spec.choices:
        raise ValueError(f"{spec.name}: {raw!r} is not one of {', '.join(spec.choices)}")
    return raw


def file_order(
    store: EventStore, campaign_id: CampaignId, seat: SeatId, lever: str, given: dict[str, str]
) -> Order:
    """Draft the seat's order for ``lever`` dated today. Every parameter must be given
    and fit its spec. Raises ``ValueError``."""
    state = load_state(store, campaign_id)
    card = state.cards[state.seats[seat].embodies]
    found = next((x for x in card.levers if x.name == lever), None)
    if found is None:
        names = ", ".join(x.name for x in card.levers)
        raise ValueError(f"no lever {lever!r} on your card (levers: {names})")
    wanted = {p.name for p in found.params}
    if set(given) != wanted:
        raise ValueError(f"{lever} takes exactly: {', '.join(sorted(wanted)) or 'no parameters'}")
    params = {p.name: _fits(p, given[p.name]) for p in found.params}
    assert state.in_world_date is not None
    order = Order(
        order_id=OrderId(new_id()),
        issuer=seat,
        lever=lever,
        pipeline=found.pipeline,
        params=params,
        filed=state.in_world_date,
    )
    _append(store, state, ev.OrderDrafted(order=order), seat)
    return order


def take_step(
    store: EventStore,
    campaign_id: CampaignId,
    order_id: OrderId,
    action: StepAction,
    given: dict[str, str],
) -> Order:
    """The human acts at the step they hold, dated today. Raises ``ValueError``."""
    state = load_state(store, campaign_id)
    order = state.orders.get(order_id)
    if order is None:
        raise ValueError(f"no order {order_id}")
    assert state.in_world_date is not None
    levers = state.cards[state.seats[order.issuer].embodies].levers
    specs = {p.name: p for lever in levers if lever.name == order.lever for p in lever.params}
    amended: dict[str, ParamValue] = {}
    for name, raw in given.items():
        if name not in specs:
            raise ValueError(f"order has no parameter {name}")
        amended[name] = _fits(specs[name], raw)
    steps = human_step(state, order_id, action, state.in_world_date, amended)
    batch: list[Event] = []
    seq = state.last_seq

    def add(payloads: Sequence[ev.Payload]) -> State:
        nonlocal seq
        added: list[Event] = []
        for payload in payloads:
            seq += 1
            added.append(
                Event(
                    event_id=EventId(new_id()),
                    campaign_id=campaign_id,
                    seq=seq,
                    tick=state.tick,
                    actor=order.issuer,
                    scope=(
                        payload.document.scope if isinstance(payload, ev.DocumentIssued) else PUBLIC
                    ),
                    payload=payload,
                    versions=cmp.versions(),
                )
            )
        batch.extend(added)
        # fold first so a bad step never reaches the store
        return replay(batch, initial=state)

    after = add(steps)
    if any(isinstance(p, ev.OrderSettled) and p.status is OrderStatus.ENACTED for p in steps):
        # The votes are counted at once, in the same batch as the final step.
        enacted = after.orders[order_id]
        add(apply_changes(after, ON_ORDER[resolver_for(after, enacted)](after, enacted)))
    store.append_all(batch)
    return load_state(store, campaign_id).orders[order_id]


def mark_read(store: EventStore, campaign_id: CampaignId, seat: SeatId, doc_id: DocumentId) -> bool:
    """Log that ``seat`` opened ``doc_id``, once. False if it was already marked."""
    for e in store.read(campaign_id):
        if (
            isinstance(e.payload, ev.DocumentRead)
            and e.payload.seat_id == seat
            and e.payload.doc_id == doc_id
        ):
            return False
    state = load_state(store, campaign_id)
    _append(store, state, ev.DocumentRead(seat_id=seat, doc_id=doc_id), seat, private_to(seat))
    return True


def set_flag(
    store: EventStore, campaign_id: CampaignId, seat: SeatId, doc_id: DocumentId, flagged: bool
) -> bool:
    """Flag ``doc_id`` for ``seat``, or take the flag off. False if it already stood so."""
    standing = False
    for e in store.read(campaign_id):
        if (
            isinstance(e.payload, ev.DocumentFlagged)
            and e.payload.seat_id == seat
            and e.payload.doc_id == doc_id
        ):
            standing = e.payload.flagged
    if standing == flagged:
        return False
    state = load_state(store, campaign_id)
    event = ev.DocumentFlagged(seat_id=seat, doc_id=doc_id, flagged=flagged)
    _append(store, state, event, seat, private_to(seat))
    return True


def _append(
    store: EventStore,
    state: State,
    payload: ev.Payload,
    actor: SeatId,
    scope: Scope = PUBLIC,
) -> None:
    event = Event(
        event_id=EventId(new_id()),
        campaign_id=state.campaign_id,
        seq=state.last_seq + 1,
        tick=state.tick,
        actor=actor,
        scope=scope,
        payload=payload,
        versions=cmp.versions(),
    )
    replay([event], initial=state)
    store.append_all([event])


__all__ = [
    "RealismTickResult",
    "file_order",
    "mark_read",
    "run_realism_tick",
    "seat_player",
    "set_flag",
    "take_step",
    "wake_line",
]
