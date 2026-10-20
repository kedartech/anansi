"""Event stores (build plan Layer 2).

One protocol, three implementations: ``MemoryStore`` for tests,
``SqliteStore`` for the solo slice (decision 3) and ``PostgresEventStore``
(``engine/pg_store.py``, Layer 8) for the home server.

Append-only is enforced by the store, not by convention: a sequence number
must be exactly the next one, and the SQLite schema carries triggers that
abort any UPDATE or DELETE on the events table.

A store may carry a ``Sealer`` (Layer 9b, ``engine/seal.py``): events and
snapshots are sealed on the way in and unsealed on the way out, so nothing
above the store ever sees ciphertext. Without one, a sealed record is refused
with a ``StoreError`` rather than handed on.
"""

import sqlite3
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Protocol

from revisionist.core import events as ev
from revisionist.core.events import Event
from revisionist.core.fold import replay
from revisionist.core.ids import CampaignId, SeatId
from revisionist.core.state import State


class StoreError(RuntimeError):
    pass


class EventStore(Protocol):
    def append(self, event: Event) -> int:
        """Append one event. Returns its ``seq``. Raises ``StoreError`` on a gap or repeat."""
        ...

    def append_all(self, events: Sequence[Event]) -> int:
        """Append atomically: all land or none do. Returns the last ``seq``."""
        ...

    def read(self, campaign_id: CampaignId, from_seq: int = 0) -> Iterator[Event]: ...

    def last_seq(self, campaign_id: CampaignId) -> int:
        """-1 for an unknown campaign."""
        ...

    def save_snapshot(self, state: State) -> None: ...

    def latest_snapshot(self, campaign_id: CampaignId) -> State | None: ...

    def campaign_ids(self) -> list[CampaignId]:
        """Every campaign in the store, oldest first by first event."""
        ...


class Sealer(Protocol):
    def sealed(self, campaign_id: CampaignId) -> bool: ...

    def begin(self, campaign_id: CampaignId) -> None:
        """Seal this new campaign from its first event."""
        ...

    def seal_text(self, campaign_id: CampaignId, seat_id: SeatId, text: str) -> str: ...

    def seal(self, events: Sequence[Event]) -> list[Event]: ...

    def unseal(self, event: Event) -> Event: ...

    def seal_state(self, state: State) -> str: ...

    def unseal_state(self, campaign_id: CampaignId, seq: int, body: str) -> State: ...


NO_KEY = "this campaign is sealed: set REVISIONIST_MASTER_KEY_FILE to read it"


def opened(sealer: Sealer | None, event: Event) -> Event:
    """What a reader gets: the event unsealed, or a ``StoreError`` without the key."""
    if sealer is not None:
        return sealer.unseal(event)
    if isinstance(event.payload, ev.Sealed):
        raise StoreError(NO_KEY)
    return event


def state_body(sealer: Sealer | None, state: State) -> str:
    return sealer.seal_state(state) if sealer is not None else state.model_dump_json()


def state_from(sealer: Sealer | None, campaign_id: CampaignId, seq: int, body: str) -> State:
    if sealer is not None:
        return sealer.unseal_state(campaign_id, seq, body)
    if body.startswith('{"sealed":'):
        raise StoreError(NO_KEY)
    return State.model_validate_json(body)


def load_state(store: EventStore, campaign_id: CampaignId) -> State:
    """Nearest snapshot plus the tail of the log."""
    snapshot = store.latest_snapshot(campaign_id)
    from_seq = snapshot.last_seq + 1 if snapshot is not None else 0
    events = list(store.read(campaign_id, from_seq))
    if snapshot is None and not events:
        raise StoreError(f"no events for campaign {campaign_id!r}")
    if not events:
        assert snapshot is not None
        return snapshot
    return replay(events, initial=snapshot)


def check_next(expected: int, event: Event) -> None:
    if event.seq != expected:
        raise StoreError(
            f"append-only: campaign {event.campaign_id} expects seq {expected}, got {event.seq}"
        )


class MemoryStore:
    def __init__(self, sealer: Sealer | None = None) -> None:
        self.sealer = sealer
        self._events: dict[CampaignId, list[Event]] = {}
        self._snapshots: dict[CampaignId, tuple[int, str]] = {}

    def append(self, event: Event) -> int:
        return self.append_all([event])

    def append_all(self, events: Sequence[Event]) -> int:
        if not events:
            raise StoreError("nothing to append")
        campaign_id = events[0].campaign_id
        log = self._events.setdefault(campaign_id, [])
        expected = len(log)
        for event in events:
            if event.campaign_id != campaign_id:
                raise StoreError("append-only: a batch belongs to one campaign")
            check_next(expected, event)
            expected += 1
        log.extend(self.sealer.seal(events) if self.sealer else events)
        return expected - 1

    def read(self, campaign_id: CampaignId, from_seq: int = 0) -> Iterator[Event]:
        for event in self._events.get(campaign_id, [])[from_seq:]:
            yield opened(self.sealer, event)

    def last_seq(self, campaign_id: CampaignId) -> int:
        return len(self._events.get(campaign_id, [])) - 1

    def save_snapshot(self, state: State) -> None:
        current = self._snapshots.get(state.campaign_id)
        if current is None or state.last_seq > current[0]:
            self._snapshots[state.campaign_id] = (state.last_seq, state_body(self.sealer, state))

    def latest_snapshot(self, campaign_id: CampaignId) -> State | None:
        found = self._snapshots.get(campaign_id)
        return state_from(self.sealer, campaign_id, *found) if found else None

    def campaign_ids(self) -> list[CampaignId]:
        return sorted(
            (c for c, log in self._events.items() if log), key=lambda c: self._events[c][0].event_id
        )


_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    campaign_id TEXT    NOT NULL,
    seq         INTEGER NOT NULL,
    event_id    TEXT    NOT NULL UNIQUE,
    tick        INTEGER NOT NULL,
    kind        TEXT    NOT NULL,
    body        TEXT    NOT NULL,
    PRIMARY KEY (campaign_id, seq)
) WITHOUT ROWID;
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TABLE IF NOT EXISTS snapshots (
    campaign_id TEXT    NOT NULL,
    seq         INTEGER NOT NULL,
    body        TEXT    NOT NULL,
    PRIMARY KEY (campaign_id, seq)
) WITHOUT ROWID;
"""


class SqliteStore:
    """Single-file store for the solo slice."""

    def __init__(self, path: Path | str, sealer: Sealer | None = None) -> None:
        self.sealer = sealer
        self._conn = sqlite3.connect(str(path), isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def append(self, event: Event) -> int:
        return self.append_all([event])

    def append_all(self, events: Sequence[Event]) -> int:
        if not events:
            raise StoreError("nothing to append")
        campaign_id = events[0].campaign_id
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            expected = self.last_seq(campaign_id) + 1
            for event in self.sealer.seal(events) if self.sealer else events:
                if event.campaign_id != campaign_id:
                    raise StoreError("append-only: a batch belongs to one campaign")
                check_next(expected, event)
                self._conn.execute(
                    "INSERT INTO events (campaign_id, seq, event_id, tick, kind, body)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        event.campaign_id,
                        event.seq,
                        event.event_id,
                        event.tick,
                        event.kind,
                        event.model_dump_json(),
                    ),
                )
                expected += 1
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        self._conn.execute("COMMIT")
        return expected - 1

    def read(self, campaign_id: CampaignId, from_seq: int = 0) -> Iterator[Event]:
        rows = self._conn.execute(
            "SELECT body FROM events WHERE campaign_id = ? AND seq >= ? ORDER BY seq",
            (campaign_id, from_seq),
        )
        for (body,) in rows:
            yield opened(self.sealer, Event.model_validate_json(body))

    def last_seq(self, campaign_id: CampaignId) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(seq), -1) FROM events WHERE campaign_id = ?", (campaign_id,)
        ).fetchone()
        return int(row[0])

    def save_snapshot(self, state: State) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO snapshots (campaign_id, seq, body) VALUES (?, ?, ?)",
            (state.campaign_id, state.last_seq, state_body(self.sealer, state)),
        )

    def campaign_ids(self) -> list[CampaignId]:
        rows = self._conn.execute(
            "SELECT campaign_id FROM events WHERE seq = 0 ORDER BY event_id"
        ).fetchall()
        return [CampaignId(r[0]) for r in rows]

    def latest_snapshot(self, campaign_id: CampaignId) -> State | None:
        row = self._conn.execute(
            "SELECT seq, body FROM snapshots WHERE campaign_id = ? ORDER BY seq DESC LIMIT 1",
            (campaign_id,),
        ).fetchone()
        return state_from(self.sealer, campaign_id, int(row[0]), row[1]) if row else None
