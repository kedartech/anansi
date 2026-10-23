"""What a web seat sees, as JSON: one call, built from the seat's own ``View`` and
its own entries in the log. Pure apart from reading the store.

The rules the client must never break are kept here, where the data is made, so
the browser cannot break them by accident:

- Odds are only ever one of the five bands, and only the perceived band of a
  locked order (as ``/status`` shows it). Never a number.
- A false fact looks the same as a true one: ``truth`` never leaves the server.
- Whether a seat is human or AI never leaves the server: other seats are
  ``SeatFace``s, which carry neither.
- Every visibility tag goes out as the engine's word; the client adds the icon.

The whole snapshot is scanned for every canary the seat may not know before it
is returned, as ``Handlers._as_seat`` scans a Discord reply.
"""

import json
from typing import Any

from revisionist.core import events as ev
from revisionist.core.canary import canaries_hidden_from
from revisionist.core.dashboard import dashboard
from revisionist.core.ids import CampaignId, SeatId
from revisionist.core.ontology import AssetStatus, ClockStatus, EntityKind, Phase, SeatKind
from revisionist.core.project import project
from revisionist.core.referee import holds
from revisionist.core.state import State, View
from revisionist.engine import scheduler as sch
from revisionist.engine import session_zero
from revisionist.engine.pg_store import PostgresEventStore

BANDS = ("impossible", "long_shot", "contested", "likely", "assured")
"""The only odds a client is ever sent, as words (decision: odds are words)."""

_POWERS = frozenset({EntityKind.POLITY, EntityKind.FACTION, EntityKind.ORG})


class WithheldError(Exception):
    """The snapshot named something the seat cannot know. Nothing is sent."""


def _band(value: str) -> str:
    if value not in BANDS:  # a new band in the engine must be added here on purpose
        raise ValueError(f"unknown band {value!r}")
    return value


def _name(view: View, obj: str) -> str:
    for table in (view.entities, view.assets, view.clocks, view.meters):
        found = table.get(obj)  # type: ignore[call-overload]
        if found is not None:
            return str(found.name)
    face = view.seats.get(obj)  # type: ignore[call-overload]
    return face.name if face is not None else "someone unknown"


def _fact_object(view: View, obj: str) -> str:
    """A fact's object as words: a name, or the label itself. An ID the seat cannot
    name reads as someone unknown, never as the raw ID."""
    import re

    from revisionist.core.ids import ULID_PATTERN

    name = _name(view, obj)
    if name != "someone unknown" or re.fullmatch(ULID_PATTERN, obj):
        return name
    return obj


def campaign_info(store: PostgresEventStore, state: State) -> dict[str, Any]:
    return {
        "id": state.campaign_id,
        "name": sch.campaign_name(store, state.campaign_id),
        "phase": state.phase.value,
        "tick": state.tick,
        "date": state.in_world_date.isoformat() if state.in_world_date else None,
        "divergence": state.divergence,
        "start": _start(store, state.campaign_id),
        "tick_hours": state.settings.tick_hours,
        "world_days_per_tick": state.settings.world_days_per_tick,
    }


def _start(store: PostgresEventStore, campaign_id: CampaignId) -> str | None:
    for e in store.read(campaign_id):
        if isinstance(e.payload, ev.SessionZeroOpened):
            return e.payload.start
    return None


def schedule_info(store: PostgresEventStore, campaign_id: CampaignId, now: Any) -> dict[str, Any]:
    s = sch.register(store, now).get(campaign_id)
    return {
        "next_tick_at": s.next_due_at.isoformat(),
        "paused": s.paused,
    }


def roster(state: State) -> list[dict[str, Any]]:
    """The public roster, as ``roster_lines`` shows it, in parts."""
    out = []
    for ch in sorted(state.roster.values(), key=lambda c: state.entities[c.entity_id].name):
        out.append(
            {
                "name": state.entities[ch.entity_id].name,
                "pitch": ch.pitch,
                "reach": ch.reach,
                "composite": ch.composite,
                "taken": ch.claimed_by is not None,
                "aliases": list(ch.aliases),
                "sides": [
                    {
                        "contest": state.contests[s.contest_id].name,
                        "side": state.entities[s.side].name,
                    }
                    for s in ch.sides
                    if s.contest_id in state.contests
                ],
            }
        )
    return out


def _own_log(store: PostgresEventStore, campaign_id: CampaignId, seat: SeatId) -> dict[str, Any]:
    """The seat's own entries: narrations, goal offers, orders and their read odds,
    plus every public statement."""
    narrations: list[dict[str, Any]] = []
    offers: dict[str, ev.GoalProposed] = {}
    orders: dict[str, dict[str, Any]] = {}
    received: dict[str, tuple[int, str]] = {}
    statements: list[dict[str, Any]] = []
    for e in store.read(campaign_id):
        p = e.payload
        if isinstance(p, ev.NarrationWritten) and p.seat_id == seat:
            narrations.append({"tick": e.tick, "seq": e.seq, "text": p.text})
        elif isinstance(p, ev.GoalProposed) and p.seat_id == seat:
            offers[p.route] = p
        elif isinstance(p, ev.OrderReceived) and p.seat_id == seat:
            received[e.event_id] = (e.tick, p.text)
        elif isinstance(p, ev.IntentFiled) and p.intent.actor == seat:
            text = received.get(p.from_order or "", (e.tick, ""))[1]
            orders[p.intent.intent_id] = {
                "id": p.intent.intent_id,
                "tick": e.tick,
                "text": text,
                "verb": p.intent.verb.value,
                "visibility": p.intent.scope.visibility.value,
                "spin": p.intent.spin,
                "odds": None,
            }
        elif isinstance(p, ev.FeasibilityRated) and e.actor == seat and p.intent_id in orders:
            orders[p.intent_id]["odds"] = _band(p.perceived.value)
            orders[p.intent_id]["odds_tick"] = e.tick
        elif isinstance(p, ev.StatementIssued):
            statements.append({"tick": e.tick, "text": p.text})
    return {
        "narrations": narrations,
        "offers": offers,
        "orders": orders,
        "statements": statements,
    }


def seat_snapshot(store: PostgresEventStore, state: State, seat: SeatId) -> dict[str, Any]:
    """Everything one seat's screens show. ``seat`` need not exist yet: in Session
    Zero it appears when the player claims a character."""
    log = _own_log(store, state.campaign_id, seat)
    out: dict[str, Any] = {"roster": roster(state) if state.phase is Phase.SESSION_ZERO else []}
    if seat not in state.seats:
        out["seat"] = None
        return out
    view = project(state, seat)
    me = view.me
    board = dashboard(view)
    has_goal = board.goal is not None
    out["seat"] = {
        "id": seat,
        "name": me.name,
        "kind": me.kind.value,
        "slots": me.slots,
        "status": me.status.value,
        "agent": (
            {"name": me.persona.name, "role": me.persona.role} if me.persona is not None else None
        ),
        "goal": (
            {
                "text": board.goal.text,
                "kind": board.goal.kind.value,
                "parts_met": board.goal.parts_met,
                "parts": board.goal.parts,
                "holding": board.goal.holding,
                "met_tick": board.goal.met_tick,
            }
            if board.goal is not None
            else None
        ),
    }
    out["session_zero"] = {
        "character": True,
        "goal": has_goal,
        "agent": me.agent is not None,
        "brief": has_goal and any(n["tick"] == 0 for n in log["narrations"]),
        "offers": {
            route: session_zero.offer_lines(state, offer) for route, offer in log["offers"].items()
        }
        if not has_goal
        else {},
    }
    out["holdings"] = [
        {
            "name": a.name,
            "kind": a.kind.value,
            "tier": a.tier,
            "where": _name(view, a.location) if a.location else None,
            "degraded": a.status is AssetStatus.DEGRADED,
            "visibility": a.scope.visibility.value,
        }
        for a in sorted(view.assets.values(), key=lambda a: (a.kind.value, a.name))
        if holds(view, me.embodies, a.asset_id) and a.status is not AssetStatus.LOST
    ]
    out["standing"] = [
        {"name": m.name, "value": m.value, "lo": m.lo, "hi": m.hi}
        for m in sorted(view.meters.values(), key=lambda m: m.name)
        if m.owner == me.embodies
    ]
    goal_clock = next((g.clock_id for g in view.goals.values() if g.seat_id == seat), None)
    out["clocks"] = [
        {
            "name": c.name,
            "kind": c.kind.value,
            "filled": c.segments_filled,
            "total": c.segments_total,
            "visibility": c.scope.visibility.value,
        }
        for c in sorted(view.clocks.values(), key=lambda c: c.name)
        if c.status is ClockStatus.OPEN and c.clock_id != goal_clock
    ]
    played = {s.embodies: s.name for s in view.seats.values() if s.kind is SeatKind.PLAYER}
    out["powers"] = [
        {"name": e.name, "played_by": played.get(e.entity_id)}
        for e in sorted(view.entities.values(), key=lambda e: e.name)
        if e.kind in _POWERS
    ]
    out["relationships"] = [
        {
            "with": _name(view, r.b if r.a == me.embodies else r.a),
            "disposition": r.disposition,
            "visibility": r.scope.visibility.value,
        }
        for r in view.relationships.values()
        if me.embodies in (r.a, r.b)
    ]
    out["facts"] = [
        # No ``truth``: a false fact must look exactly like a true one.
        {
            "subject": _name(view, f.subject),
            "predicate": f.predicate,
            "object": _fact_object(view, str(f.obj)),
            "visibility": f.scope.visibility.value,
            "tick": f.asserted_tick,
        }
        for f in sorted(view.facts.values(), key=lambda f: (f.asserted_tick, f.predicate))
    ]
    out["contests"] = [
        {
            "name": c.name,
            "kind": c.kind.value,
            "prize": c.prize,
            "sides": [_name(view, s) for s in c.sides],
            "track": c.track,
            "countdown_days": c.countdown_days,
            "decided": c.decided,
            "winner": _name(view, c.winner) if c.winner else None,
            "visibility": c.scope.visibility.value,
        }
        for c in sorted(view.contests.values(), key=lambda c: c.name)
    ]
    out["rivals"] = [{"seat": name, "pieces": list(pieces)} for name, pieces in board.rivals]
    pending = {i.intent_id for i in view.own_pending}
    orders = list(log["orders"].values())
    for o in orders:
        o["pending"] = o["id"] in pending
    out["orders"] = sorted(orders, key=lambda o: o["tick"], reverse=True)
    out["dispatches"] = [n for n in log["narrations"] if n["tick"] > 0][::-1]
    out["brief"] = next((n["text"] for n in log["narrations"] if n["tick"] == 0), None)
    out["statements"] = log["statements"][::-1][:50]
    return out


def checked(state: State, seat: SeatId, payload: dict[str, Any]) -> dict[str, Any]:
    """``payload`` if it names no canary hidden from ``seat``; else ``WithheldError``."""
    text = json.dumps(payload, ensure_ascii=False)
    if any(c in text for c in canaries_hidden_from(state, seat)):
        raise WithheldError(seat)
    return payload


__all__ = [
    "BANDS",
    "WithheldError",
    "campaign_info",
    "checked",
    "roster",
    "schedule_info",
    "seat_snapshot",
]
