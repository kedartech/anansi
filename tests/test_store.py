"""Layer 2 and Layer 8: stores are append-only and snapshot+tail equals full replay.

The contract tests run over every store. Postgres runs against a scratch
database as the least-privilege app role (``tests/pg.py``).
"""

import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg import errors, sql

from revisionist.core import events as ev
from revisionist.core.fold import replay, state_hash
from revisionist.core.ids import EventId
from revisionist.engine import pg_store
from revisionist.engine.pg_store import PostgresEventStore, migrate
from revisionist.engine.store import EventStore, MemoryStore, SqliteStore, StoreError, load_state
from tests import fixtures as fx
from tests.pg import Cluster, PgDb, url


@pytest.fixture(params=["memory", "sqlite", "postgres"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[EventStore]:
    if request.param == "memory":
        yield MemoryStore()
    elif request.param == "sqlite":
        s = SqliteStore(tmp_path / "campaign.db")
        yield s
        s.close()
    else:
        db: PgDb = request.getfixturevalue("pg_db")
        p = PostgresEventStore(db.app_dsn)
        yield p
        p.close()


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


def test_mixed_campaign_batch_is_refused(store: EventStore) -> None:
    events = fx.creation_events()
    other = events[1].model_copy(
        update={"campaign_id": fx.CampaignId("01ARZ3NDEKTSV4RRFFQ69G5FA1")}
    )
    with pytest.raises(StoreError, match="append-only"):
        store.append_all([events[0], other])
    assert store.last_seq(fx.CAMPAIGN) == -1
    assert store.campaign_ids() == []


def test_campaign_ids_oldest_first(store: EventStore) -> None:
    assert store.campaign_ids() == []
    first = fx.creation_events()
    store.append_all(first)
    later = fx.CampaignId("7ZZZZZZZZZZZZZZZZZZZZZZZZZ")
    renamed = [
        e.model_copy(update={"campaign_id": later, "event_id": EventId("Z" + e.event_id[1:])})
        for e in first[:1]
    ]
    store.append_all(renamed)
    assert store.campaign_ids() == [fx.CAMPAIGN, later]


def test_awkward_text_round_trips_exactly(store: EventStore) -> None:
    events = fx.creation_events()
    store.append_all(events)
    text = 'nul\x00 tab\t emoji \U0001f600 quote" backslash\\ é'
    narration = fx.envelope(len(events), ev.NarrationWritten(seat_id=fx.NORTH, text=text))
    store.append(narration)
    assert list(store.read(fx.CAMPAIGN, len(events))) == [narration]


def test_snapshot_is_insert_if_absent_and_latest_wins(store: EventStore) -> None:
    events = fx.creation_events()
    store.append_all(events)
    early, late = replay(events[:3]), replay(events)
    store.save_snapshot(late)
    store.save_snapshot(early)
    assert store.latest_snapshot(fx.CAMPAIGN) == late
    store.save_snapshot(late.model_copy(update={"tick": 99}))
    assert store.latest_snapshot(fx.CAMPAIGN) == late


# --- Postgres only ----------------------------------------------------------


def test_postgres_app_role_cannot_update_or_delete(pg_db: PgDb) -> None:
    s = PostgresEventStore(pg_db.app_dsn)
    events = fx.creation_events()
    s.append_all(events)
    s.save_snapshot(replay(events))
    s.close()
    with psycopg.connect(pg_db.app_dsn, autocommit=True) as conn:
        for statement in (
            "UPDATE events SET tick = 9",
            "DELETE FROM events",
            "TRUNCATE events",
            "UPDATE snapshots SET seq = 9",
            "DELETE FROM snapshots",
        ):
            with pytest.raises(errors.InsufficientPrivilege):
                conn.execute(statement.encode())


def test_postgres_triggers_refuse_even_the_owner(pg_db: PgDb) -> None:
    s = PostgresEventStore(pg_db.app_dsn)
    s.append(fx.creation_events()[0])
    s.close()
    with psycopg.connect(pg_db.admin_dsn, autocommit=True) as conn:
        for statement in ("UPDATE events SET tick = 9", "DELETE FROM events", "TRUNCATE events"):
            with pytest.raises(errors.RaiseException, match="append-only"):
                conn.execute(statement.encode())
        row = conn.execute("SELECT count(*) FROM events").fetchone()
        assert row is not None and row[0] == 1


def test_postgres_repeated_event_id_is_refused(pg_db: PgDb) -> None:
    s = PostgresEventStore(pg_db.app_dsn)
    events = fx.creation_events()
    s.append_all(events[:2])
    clash = events[2].model_copy(update={"event_id": events[0].event_id})
    with pytest.raises(StoreError, match="append-only"):
        s.append_all([clash, events[3].model_copy(update={"seq": 3})])
    assert s.last_seq(fx.CAMPAIGN) == 1
    s.close()


def test_postgres_concurrent_writers_one_wins(pg_db: PgDb) -> None:
    events = fx.creation_events()
    winner = PostgresEventStore(pg_db.app_dsn)
    winner.append_all(events[:2])
    loser = PostgresEventStore(pg_db.app_dsn)
    rival = fx.envelope(2, ev.NarrationWritten(seat_id=fx.NORTH, text="loser"))
    outcome: list[BaseException | int] = []

    # The winner holds seq 2 in an open transaction; the loser reads seq 1 as the
    # last one, inserts seq 2 too, and blocks on the winner's row until it commits.
    with psycopg.connect(pg_db.app_dsn) as tx:
        tx.execute(
            "INSERT INTO events (campaign_id, seq, event_id, tick, kind, body)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (fx.CAMPAIGN, 2, events[2].event_id, 0, events[2].kind, events[2].model_dump_json()),
        )

        def race() -> None:
            try:
                outcome.append(loser.append(rival))
            except BaseException as exc:
                outcome.append(exc)

        thread = threading.Thread(target=race)
        thread.start()
        thread.join(timeout=0.5)
        assert thread.is_alive(), "the loser should be blocked on the winner's row"
        tx.commit()
    thread.join(timeout=10)
    assert len(outcome) == 1 and isinstance(outcome[0], StoreError)
    assert "append-only" in str(outcome[0])
    assert [e.event_id for e in winner.read(fx.CAMPAIGN)] == [e.event_id for e in events[:3]]
    winner.close()
    loser.close()


def test_postgres_migrations_are_idempotent(pg_db: PgDb) -> None:
    assert migrate(pg_db.admin_dsn, pg_db.app_role) == []
    with psycopg.connect(pg_db.admin_dsn) as conn:
        versions = [r[0] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY 1")]
    assert versions == ["0001_init", "0002_schedule", "0003_discord", "0004_rename"]


def _append_only_functions(dsn: str) -> list[str]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute("SELECT proname FROM pg_proc WHERE proname LIKE '%append_only'")
        return sorted(r[0] for r in rows)


def test_a_database_from_before_the_rename_converges(
    pg_cluster: Cluster, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0001 still creates the old trigger function; 0004 renames it in place."""
    name = f"{pg_cluster.template}_upgrade"
    dsn = url(pg_cluster.admin, dbname=name)
    with psycopg.connect(pg_cluster.admin, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        before = [p for p in pg_store.migration_files() if p.stem < "0004"]
        monkeypatch.setattr(pg_store, "migration_files", lambda: before)
        migrate(dsn, pg_cluster.role)
        assert _append_only_functions(dsn) == ["wyrd_append_only"]
        monkeypatch.undo()
        assert migrate(dsn, pg_cluster.role) == ["0004_rename"]
        assert _append_only_functions(dsn) == ["revisionist_append_only"]
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("INSERT INTO events VALUES ('c', 0, 'e', 0, 'k', '{}')")
            with pytest.raises(errors.RaiseException, match="append-only"):
                conn.execute("DELETE FROM events")
    finally:
        with psycopg.connect(pg_cluster.admin, autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


def test_postgres_app_role_may_update_only_the_schedule(pg_db: PgDb) -> None:
    with psycopg.connect(pg_db.app_dsn, autocommit=True) as conn:
        conn.execute("INSERT INTO schedule (campaign_id, next_due_at) VALUES ('c', now())")
        conn.execute("UPDATE schedule SET paused = TRUE WHERE campaign_id = 'c'")
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("DELETE FROM schedule")
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM schema_migrations")
