"""The host's read and admin operations, free of any transport.

The CLI and Discord both call these. They return lines of text or plain data; a
refusal raises ``HostOpError`` with a message that is safe to show the host.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from revisionist.core.canary import HumanLeak, scan_humans, surfaces_from_log
from revisionist.core.fold import replay_to, state_hash
from revisionist.core.ids import CampaignId, SeatId
from revisionist.core.ontology import Controller
from revisionist.core.report import (
    cost_report,
    leak_report,
    render_cost,
    render_leaks,
    render_why,
    why,
)
from revisionist.core.sealing import needs_admin_read
from revisionist.core.state import State
from revisionist.engine import campaign as cmp
from revisionist.engine import scheduler as sch
from revisionist.engine import season, session_zero
from revisionist.engine.discord.tables import Invites, SeatLinks
from revisionist.engine.maintenance import verify_campaign
from revisionist.engine.store import EventStore, Sealer, load_state

if TYPE_CHECKING:
    from psycopg import Connection

    from revisionist.engine.pg_store import PostgresEventStore


class HostOpError(Exception):
    """A refusal with a one-line message the host may see."""


def goal_flag_lines(store: EventStore, campaign_id: CampaignId) -> list[str]:
    state = load_state(store, campaign_id)
    rows = session_zero.flagged(store, campaign_id)
    return [
        f"{state.seats[s].name}: power {b.power}, difficulty {b.difficulty}, {b.flag}"
        for s, b in rows
    ] or ["no goals wait for approval"]


def approve_goal(store: EventStore, campaign_id: CampaignId, seat_ref: str) -> str:
    state = load_state(store, campaign_id)
    try:
        seat = state.seats[cmp.resolve_seat(state, seat_ref)]
    except LookupError as exc:
        raise HostOpError(f"No seat called {seat_ref!r} here.") from exc
    if seat.controller is not Controller.HUMAN:
        raise HostOpError(f"{seat.name} is not a human seat")
    return session_zero.approve(store, campaign_id, seat.seat_id)


def season_report_lines(store: EventStore, campaign_id: CampaignId) -> list[str]:
    return season.report(store, campaign_id)


def cost_text(store: EventStore, campaign_id: CampaignId) -> str:
    return render_cost(cost_report(store.read(campaign_id)))


def holdings(
    conn: "Connection[Any]", state: State, host_id: str | None
) -> dict[str, frozenset[SeatId]]:
    """Who holds which seats: Discord users from their links, and the host, who
    can read every human seat nobody else holds through the CLI."""
    links = SeatLinks(conn).users(state.campaign_id)
    host = host_id or "host"
    held: dict[str, set[SeatId]] = {host: set()}
    for seat, user in links.items():
        held.setdefault(user, set()).add(seat)
    held[host] |= {s for s in cmp.human_seats(state) if s not in links}
    return {human: frozenset(seats) for human, seats in held.items()}


def leak_lines(
    store: EventStore,
    campaign_id: CampaignId,
    holdings: Mapping[str, frozenset[SeatId]] | None = None,
) -> tuple[list[str], bool]:
    state = load_state(store, campaign_id)
    found = leak_report(state, store.read(campaign_id))
    lines = render_leaks(found).splitlines()
    if holdings is None:
        return lines, bool(found)
    seen = surfaces_from_log(store.read(campaign_id), holdings)
    human_leaks: list[HumanLeak] = scan_humans(state, holdings, seen)
    lines += [
        f"LEAK human={leak.human} fact={leak.fact_id} in {leak.surface}" for leak in human_leaks
    ]
    if not human_leaks:
        lines.append(f"0 leaks across {len(holdings)} people")
    return lines, bool(found or human_leaks)


def why_lines(
    store: EventStore,
    campaign_id: CampaignId,
    target: str,
    reason: str | None = None,
    actor: str | None = None,
) -> list[str]:
    """Trace an intent. On a sealed campaign the private records stay hidden unless
    a non-blank ``reason`` is given, and reading them logs an admin read first.

    ``actor`` is not recorded: ``log_admin_read`` names the host from the OS user.
    """
    events = list(store.read(campaign_id))
    lines = why(events, target)
    sealer: Sealer | None = getattr(store, "sealer", None)
    if sealer is None or not sealer.sealed(campaign_id):
        return render_why(lines).splitlines()
    by_seq = {e.seq: e for e in events}
    private = [w for w in lines if needs_admin_read(by_seq[w.seq])]
    if not private:
        return render_why(lines).splitlines()
    if reason is None or not reason.strip():
        public = [w for w in lines if w not in private]
        shown = render_why(public).splitlines() if public else ["nothing public found"]
        return [
            *shown,
            f"{len(private)} private records hidden; give a reason to read them "
            "(logged and announced)",
        ]
    cmp.log_admin_read(
        store, campaign_id, [by_seq[w.seq].event_id for w in private], reason.strip()
    )
    return render_why(lines).splitlines()


def replay_line(store: EventStore, campaign_id: CampaignId, tick: int) -> str:
    state = replay_to(list(store.read(campaign_id)), tick)
    return (
        f"tick {state.tick}  seq {state.last_seq}  hash {state_hash(state)}  "
        f"assets {len(state.assets)}  facts {len(state.facts)}  pending {len(state.pending)}"
    )


def verify_lines(store: EventStore, campaign_ids: Sequence[CampaignId]) -> tuple[list[str], bool]:
    lines: list[str] = []
    ok = True
    for campaign_id in campaign_ids:
        v = verify_campaign(store, campaign_id)
        if v.ok:
            sealed = f", sealed, {v.erased} erased" if v.sealed else ""
            lines.append(
                f"ok {campaign_id}: {v.events} events, tick {v.tick}, hash {v.state_hash}{sealed}"
            )
        else:
            lines.append(f"FAIL {campaign_id}: {v.problem}")
            ok = False
    return lines, ok


def schedule_lines(
    store: "PostgresEventStore", campaign_ids: Sequence[CampaignId] | None = None
) -> list[str]:
    """Each campaign's next tick. A campaign without a schedule row raises LookupError;
    ``sch.register`` gives one."""
    table = sch.ScheduleTable(store.conn)
    ids = store.campaign_ids() if campaign_ids is None else campaign_ids
    lines: list[str] = []
    for campaign_id in ids:
        row = table.get(campaign_id)
        state = "paused: " + row.paused_reason if row.paused else "active"
        if row.advance_requested:
            state += ", advance requested"
        lines.append(
            f"{row.campaign_id}  next {row.next_due_at.astimezone(UTC):%Y-%m-%d %H:%MZ}"
            f"  {state}  failures {row.consecutive_failures}  last {row.last_status or '-'}"
        )
    return lines


def seat_lines(conn: "Connection[Any]", campaign_id: CampaignId | None = None) -> list[str]:
    links = SeatLinks(conn).all()
    if campaign_id is not None:
        links = [link for link in links if link.campaign_id == campaign_id]
    if not links:
        return ["no seats linked"]
    return [
        f"{link.campaign_id}  {link.seat_id}  user {link.discord_user_id}"
        f"  guild {link.guild_id}  since {link.linked_at.astimezone(UTC):%Y-%m-%d %H:%MZ}"
        for link in links
    ]


def invite_lines(conn: "Connection[Any]", campaign_id: CampaignId, now: datetime) -> list[str]:
    invites = Invites(conn).all(campaign_id)
    if not invites:
        return ["no invites"]
    lines: list[str] = []
    for i in invites:
        if i.used_at is not None:
            state = f"used by {i.used_by} {i.used_at.astimezone(UTC):%Y-%m-%d %H:%MZ}"
        elif i.expires_at <= now:
            state = "expired"
        else:
            state = f"open until {i.expires_at.astimezone(UTC):%Y-%m-%d %H:%MZ}"
        lines.append(f"{i.seat_id}  issued {i.issued_at.astimezone(UTC):%Y-%m-%d %H:%MZ}  {state}")
    return lines
