"""Layer 2: stores are append-only and snapshot+tail equals full replay."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from anansi.core import events as ev
from anansi.core.fold import replay, state_hash
from anansi.engine.store import EventStore, MemoryStore, SqliteStore, StoreError, load_state
from tests import fixtures as fx


@pytest.fixture(params=["memory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[EventStore]:
    if request.param == "memory":
        yield MemoryStore()
    else:
        s = SqliteStore(tmp_path / "campaign.db")
        yield s
        s.close()


def test_append_read_round_trip(store: EventStore) -> None:
    events = fx.creation_events()
    for e in events:
        store.append(e)
    assert list(store.read(fx.CAMPAIGN)) == events
    assert store.last_seq(fx.CAMPAIGN) == len(events) - 1
    assert store.last_seq(fx.CampaignId("01ARZ3NDEKTSV4RRFFQ69G5FA1")) == -1


def test_gap_and_repeat_are_refused(store: EventStore) -> None:
    events = fx.creation_events()
    store.append(events[0])
    with pytest.raises(StoreError, match="append-only"):
        store.append(events[2])
    with pytest.raises(StoreError, match="append-only"):
        store.append(events[0])
    assert store.last_seq(fx.CAMPAIGN) == 0


def test_batch_is_atomic(store: EventStore) -> None:
    events = fx.creation_events()
    bad = [events[0], events[1], events[3]]
    with pytest.raises(StoreError):
        store.append_all(bad)
    assert store.last_seq(fx.CAMPAIGN) == -1
    assert store.append_all(events) == len(events) - 1


def test_snapshot_plus_tail_equals_full_replay(store: EventStore) -> None:
    events = fx.creation_events()
    half = len(events) // 2
    store.append_all(events[:half])
    store.save_snapshot(replay(events[:half]))
    store.append_all(events[half:])
    loaded = load_state(store, fx.CAMPAIGN)
    full = replay(events)
    assert loaded == full
    assert state_hash(loaded) == state_hash(full)


def test_load_state_without_events_fails(store: EventStore) -> None:
    with pytest.raises(StoreError, match="no events"):
        load_state(store, fx.CAMPAIGN)


def test_sqlite_refuses_update_and_delete_at_the_database(tmp_path: Path) -> None:
    path = tmp_path / "c.db"
    s = SqliteStore(path)
    s.append(fx.creation_events()[0])
    s.close()
    conn = sqlite3.connect(path)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE events SET tick = 9")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM events")
    conn.close()


def test_sqlite_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "c.db"
    events = fx.creation_events()
    s = SqliteStore(path)
    s.append_all(events)
    s.close()
    s2 = SqliteStore(path)
    assert [e.event_id for e in s2.read(fx.CAMPAIGN)] == [e.event_id for e in events]
    narration = fx.envelope(len(events), ev.NarrationWritten(seat_id=fx.NORTH, text="quiet"))
    assert s2.append(narration) == len(events)
    s2.close()
