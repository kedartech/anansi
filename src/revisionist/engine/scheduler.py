"""The tick scheduler (build plan Layer 8; README §9).

A poll, not a daemon: a systemd timer runs ``revisionist scheduler run`` every few
minutes, and each run ticks every campaign that is due, at most once each.
The cadence is each campaign's ``Settings.tick_hours``, not the timer's.

A campaign is due when its timer has passed, the host asked to advance, or
every human seat has filed an intent since the last tick closed. A tick left
open by a crash is always resumed first. A paused campaign is never due.
Missed ticks are skipped, not backfilled, and the next due time is anchored
to the previous one so the schedule never drifts.

Bookkeeping lives in the ``schedule`` table (migration 0002). It is
operational state, not game state, so it never enters the event log. The
scheduler is Postgres-only: it needs the advisory lock that keeps two
overlapping runs from ticking one campaign twice. SQLite campaigns tick by
hand with ``revisionist tick``.

The decision functions (``due``, ``next_due_after``, ``all_submitted``) are
pure and take ``now`` as an argument; only ``run`` reads the clock.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from revisionist.core import events as ev
from revisionist.core.events import Event
from revisionist.core.ids import CampaignId, SeatId
from revisionist.core.ontology import Controller, Phase
from revisionist.core.report import render_leaks
from revisionist.core.resolution import TickStage
from revisionist.core.state import State
from revisionist.engine.llm import Transport
from revisionist.engine.runner import DelivererFactory, play_tick
from revisionist.engine.store import EventStore, load_state

if TYPE_CHECKING:  # psycopg is the optional ``postgres`` extra; the CLI imports this module
    from psycopg import Connection

    from revisionist.engine.pg_store import PostgresEventStore

MAX_FAILURES = 3
_LOCK_CLASS = 0x7276736E  # "rvsn": first key of the per-campaign advisory lock


def utc_now() -> datetime:
    return datetime.now(UTC)


# --- pure decisions -------------------------------------------------------------


@dataclass(frozen=True)
class Schedule:
    campaign_id: CampaignId
    next_due_at: datetime
    advance_requested: bool = False
    paused: bool = False
    paused_reason: str = ""
    consecutive_failures: int = 0
    last_started_at: datetime | None = None
    last_finished_at: datetime | None = None
    last_status: str = ""


class Why(StrEnum):
    RESUME = "resume"
    ADVANCE = "advance"
    TIMER = "timer"
    ALL_SUBMITTED = "all submitted"


def due(row: Schedule, now: datetime, *, in_flight: bool, submitted: bool) -> Why | None:
    if row.paused:
        return None
    if in_flight:
        return Why.RESUME
    if row.advance_requested:
        return Why.ADVANCE
    if now >= row.next_due_at:
        return Why.TIMER
    if submitted:
        return Why.ALL_SUBMITTED
    return None


def next_due_after(previous: datetime, tick_hours: int, now: datetime) -> datetime:
    """``previous`` plus whole ticks until it is in the future: no drift, no backfill."""
    step = timedelta(hours=tick_hours)
    nxt = previous + step
    if nxt <= now:
        nxt += step * ((now - nxt) // step + 1)
    return nxt


def since_last_close(events: Sequence[Event]) -> Sequence[Event]:
    last = max((i for i, e in enumerate(events) if e.kind == "tick_closed"), default=-1)
    return events[last + 1 :]


def in_flight(tail: Sequence[Event]) -> bool:
    """A tick was opened since the last one closed."""
    return any(e.kind == "tick_opened" for e in tail)


def all_submitted(state: State, tail: Sequence[Event]) -> bool:
    """Every human seat filed an intent since the last tick closed that is still pending.

    Intents held open by a running operation, or carried by a quiet tick, were
    filed before that close, so they never make a campaign due on their own.
    """
    humans = {s.seat_id for s in state.seats.values() if s.controller is Controller.HUMAN}
    filed: set[SeatId] = set()
    for e in tail:
        if isinstance(e.payload, ev.IntentFiled) and e.payload.intent.intent_id in state.pending:
            filed.add(e.payload.intent.actor)
    return bool(humans) and humans <= filed


# --- the schedule table ---------------------------------------------------------

_COLUMNS = (
    "campaign_id, next_due_at, advance_requested, paused, paused_reason,"
    " consecutive_failures, last_started_at, last_finished_at, last_status"
)


class ScheduleTable:
    def __init__(self, conn: "Connection[Any]") -> None:
        self.conn = conn

    def ensure(self, campaign_id: CampaignId, next_due_at: datetime) -> None:
        self.conn.execute(
            "INSERT INTO schedule (campaign_id, next_due_at) VALUES (%s, %s)"
            " ON CONFLICT DO NOTHING",
            (campaign_id, next_due_at),
        )

    def get(self, campaign_id: CampaignId) -> Schedule:
        row = self.conn.execute(
            f"SELECT {_COLUMNS} FROM schedule WHERE campaign_id = %s",
            (campaign_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"campaign {campaign_id} is not scheduled")
        return Schedule(CampaignId(row[0]), *row[1:])

    def record(
        self,
        campaign_id: CampaignId,
        status: str,
        *,
        started: datetime | None = None,
        finished: datetime | None = None,
        failures: int | None = None,
        next_due_at: datetime | None = None,
        clear_advance: bool = False,
        pause: str | None = None,
    ) -> None:
        """The scheduler's write. It can set ``paused`` but never clears it, so a
        host's pause or advance landing mid-tick is not overwritten."""
        self.conn.execute(
            "UPDATE schedule SET"
            " next_due_at = COALESCE(%(next)s::timestamptz, next_due_at),"
            " advance_requested = advance_requested AND NOT %(clear)s,"
            " consecutive_failures = COALESCE(%(failures)s::integer, consecutive_failures),"
            " last_started_at = COALESCE(%(started)s::timestamptz, last_started_at),"
            " last_finished_at = COALESCE(%(finished)s::timestamptz, last_finished_at),"
            " last_status = %(status)s,"
            " paused = paused OR %(pause)s::text IS NOT NULL,"
            " paused_reason = COALESCE(%(pause)s::text, paused_reason)"
            " WHERE campaign_id = %(id)s",
            {
                "id": campaign_id,
                "status": status[:500],
                "started": started,
                "finished": finished,
                "failures": failures,
                "next": next_due_at,
                "clear": clear_advance,
                "pause": pause,
            },
        )

    def advance(self, campaign_id: CampaignId) -> None:
        self.conn.execute(
            "UPDATE schedule SET advance_requested = TRUE WHERE campaign_id = %s", (campaign_id,)
        )

    def pause(self, campaign_id: CampaignId, reason: str) -> None:
        self.conn.execute(
            "UPDATE schedule SET paused = TRUE, paused_reason = %s WHERE campaign_id = %s",
            (reason[:500], campaign_id),
        )

    def resume(self, campaign_id: CampaignId) -> None:
        self.conn.execute(
            "UPDATE schedule SET paused = FALSE, paused_reason = '', consecutive_failures = 0"
            " WHERE campaign_id = %s",
            (campaign_id,),
        )

    def try_lock(self, campaign_id: CampaignId) -> bool:
        row = self.conn.execute(
            "SELECT pg_try_advisory_lock(%s, hashtext(%s))", (_LOCK_CLASS, campaign_id)
        ).fetchone()
        return bool(row and row[0])

    def unlock(self, campaign_id: CampaignId) -> None:
        self.conn.execute("SELECT pg_advisory_unlock(%s, hashtext(%s))", (_LOCK_CLASS, campaign_id))


def register(store: "PostgresEventStore", now: datetime) -> ScheduleTable:
    """Give every campaign a schedule row; a new one is first due one tick from now."""
    table = ScheduleTable(store.conn)
    for campaign_id in store.campaign_ids():
        hours = load_state(store, campaign_id).settings.tick_hours
        table.ensure(campaign_id, now + timedelta(hours=hours))
    return table


# --- one scheduler run ----------------------------------------------------------


class Action(StrEnum):
    WAITING = "waiting"
    PAUSED = "paused"
    LOCKED = "locked"
    TICKED = "ticked"
    FAILED = "failed"
    STOPPED = "stopped"
    LEAK = "leak"


@dataclass(frozen=True)
class Outcome:
    campaign_id: CampaignId
    action: Action
    detail: str = ""
    newly_paused: bool = False


def run(
    store: "PostgresEventStore",
    clock: Callable[[], datetime],
    *,
    offline: bool,
    transport_factory: Callable[[], Transport],
    briefings: Path,
    warn: Callable[[str], None],
    campaign: CampaignId | None = None,
    stop_after: TickStage | None = None,
    deliverer_factory: DelivererFactory | None = None,
) -> list[Outcome]:
    """Tick every due campaign once. ``stop_after`` simulates a crash (tests only)."""
    table = register(store, clock())
    outcomes = []
    for campaign_id in [campaign] if campaign else store.campaign_ids():
        if not table.try_lock(campaign_id):
            outcomes.append(Outcome(campaign_id, Action.LOCKED, "another run holds it"))
            continue
        try:
            outcomes.append(
                _one(
                    store,
                    table,
                    campaign_id,
                    clock,
                    offline=offline,
                    transport_factory=transport_factory,
                    briefings=briefings,
                    warn=warn,
                    stop_after=stop_after,
                    deliverer_factory=deliverer_factory,
                )
            )
        finally:
            table.unlock(campaign_id)
    return outcomes


def _tail(store: EventStore, campaign_id: CampaignId) -> Sequence[Event]:
    snapshot = store.latest_snapshot(campaign_id)
    return since_last_close(list(store.read(campaign_id, snapshot.last_seq if snapshot else 0)))


def _one(
    store: "PostgresEventStore",
    table: ScheduleTable,
    campaign_id: CampaignId,
    clock: Callable[[], datetime],
    *,
    offline: bool,
    transport_factory: Callable[[], Transport],
    briefings: Path,
    warn: Callable[[str], None],
    stop_after: TickStage | None,
    deliverer_factory: DelivererFactory | None,
) -> Outcome:
    now = clock()
    row = table.get(campaign_id)  # re-read under the lock
    state = load_state(store, campaign_id)
    if state.phase is not Phase.PLAY:
        return Outcome(campaign_id, Action.WAITING, state.phase.value.replace("_", " "))
    tail = _tail(store, campaign_id)
    why = due(row, now, in_flight=in_flight(tail), submitted=all_submitted(state, tail))
    if why is None:
        action = Action.PAUSED if row.paused else Action.WAITING
        return Outcome(campaign_id, action, row.paused_reason if row.paused else "")
    table.record(campaign_id, f"running ({why})", started=now)
    try:
        played = play_tick(
            store,
            campaign_id,
            offline=offline,
            transport_factory=transport_factory,
            briefings=briefings,
            warn=warn,
            stop_after=stop_after,
            deliverer_factory=deliverer_factory,
        )
    except Exception as exc:  # the tick stays resumable; count it and maybe stop trying
        failures = row.consecutive_failures + 1
        status = f"failed ({why}): {type(exc).__name__}: {exc}"
        stop = failures >= MAX_FAILURES
        table.record(
            campaign_id,
            status,
            finished=clock(),
            failures=failures,
            pause=f"{failures} consecutive failures" if stop else None,
        )
        return Outcome(campaign_id, Action.FAILED, status, newly_paused=stop)
    result = played.result
    if result.stopped_after is not None:
        table.record(campaign_id, f"stopped after {result.stopped_after}")
        return Outcome(campaign_id, Action.STOPPED, str(result.stopped_after))
    word = "quiet" if result.quiet else "resolved"
    status = f"tick {result.tick} {word} ({why})"
    leak = f"canary leak at tick {result.tick}" if played.leaks else None
    table.record(
        campaign_id,
        leak or status,
        finished=clock(),
        failures=0,
        next_due_at=next_due_after(row.next_due_at, state.settings.tick_hours, now),
        clear_advance=True,
        pause=leak,
    )
    if played.leaks:
        return Outcome(campaign_id, Action.LEAK, render_leaks(played.leaks), newly_paused=True)
    return Outcome(campaign_id, Action.TICKED, status)


__all__ = [
    "MAX_FAILURES",
    "Action",
    "Outcome",
    "Schedule",
    "ScheduleTable",
    "Why",
    "all_submitted",
    "due",
    "in_flight",
    "next_due_after",
    "register",
    "run",
    "since_last_close",
    "utc_now",
]
