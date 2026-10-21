"""Postgres event store and migrations (build plan Layer 8).

Same protocol and the same append-only rules as ``SqliteStore``. The database
enforces them too: the runtime connects as an app role holding only SELECT
and INSERT on ``events`` and ``snapshots``, and triggers refuse UPDATE,
DELETE and TRUNCATE for everyone. Migrations run separately, as an admin, and
grant to the app role by name.

``psycopg`` is the optional ``postgres`` extra; nothing imports this module
unless ``$REVISIONIST_DB`` is a ``postgresql://`` URL.
"""

from collections.abc import Iterator, Sequence
from pathlib import Path

import psycopg
from psycopg import errors, sql

from revisionist.core.events import Event
from revisionist.core.ids import CampaignId
from revisionist.core.state import State
from revisionist.engine.store import Sealer, StoreError, check_next, opened, state_body, state_from

MIGRATIONS = Path(__file__).parent / "migrations"

_MIGRATE_LOCK = 0x7276736E  # "rvsn": one migrator at a time


class PostgresEventStore:
    """One connection per store, autocommit outside explicit transactions."""

    def __init__(self, dsn: str, sealer: Sealer | None = None) -> None:
        self.conn = psycopg.connect(dsn, autocommit=True)
        self.sealer = sealer

    def close(self) -> None:
        self.conn.close()

    def append(self, event: Event) -> int:
        return self.append_all([event])

    def append_all(self, events: Sequence[Event]) -> int:
        if not events:
            raise StoreError("nothing to append")
        campaign_id = events[0].campaign_id
        # Seal first, outside the transaction: a seat key created here commits on
        # its own, so a refused append never leaves a key cached that is not stored.
        events = self.sealer.seal(events) if self.sealer else events
        try:
            with self.conn.transaction():
                expected = self.last_seq(campaign_id) + 1
                for event in events:
                    if event.campaign_id != campaign_id:
                        raise StoreError("append-only: a batch belongs to one campaign")
                    check_next(expected, event)
                    expected += 1
                with self.conn.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO events (campaign_id, seq, event_id, tick, kind, body)"
                        " VALUES (%s, %s, %s, %s, %s, %s)",
                        [
                            (e.campaign_id, e.seq, e.event_id, e.tick, e.kind, e.model_dump_json())
                            for e in events
                        ],
                    )
        except errors.UniqueViolation as exc:
            # A concurrent writer took this seq first, or an event_id repeats.
            raise StoreError(
                f"append-only: campaign {campaign_id}: {exc.diag.message_primary}"
            ) from exc
        return expected - 1

    def read(self, campaign_id: CampaignId, from_seq: int = 0) -> Iterator[Event]:
        rows = self.conn.execute(
            "SELECT body FROM events WHERE campaign_id = %s AND seq >= %s ORDER BY seq",
            (campaign_id, from_seq),
        ).fetchall()
        for (body,) in rows:
            yield opened(self.sealer, Event.model_validate_json(body))

    def last_seq(self, campaign_id: CampaignId) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(MAX(seq), -1) FROM events WHERE campaign_id = %s", (campaign_id,)
        ).fetchone()
        assert row is not None
        return int(row[0])

    def save_snapshot(self, state: State) -> None:
        self.conn.execute(
            "INSERT INTO snapshots (campaign_id, seq, body) VALUES (%s, %s, %s)"
            " ON CONFLICT DO NOTHING",
            (state.campaign_id, state.last_seq, state_body(self.sealer, state)),
        )

    def latest_snapshot(self, campaign_id: CampaignId) -> State | None:
        row = self.conn.execute(
            "SELECT seq, body FROM snapshots WHERE campaign_id = %s ORDER BY seq DESC LIMIT 1",
            (campaign_id,),
        ).fetchone()
        return state_from(self.sealer, campaign_id, int(row[0]), row[1]) if row else None

    def campaign_ids(self) -> list[CampaignId]:
        rows = self.conn.execute(
            "SELECT campaign_id FROM events WHERE seq = 0 ORDER BY campaign_id"
        ).fetchall()
        return [CampaignId(r[0]) for r in rows]


def migration_files() -> list[Path]:
    return sorted(MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.sql"))


def migrate(admin_dsn: str, app_role: str) -> list[str]:
    """Apply every migration not yet in ``schema_migrations``. Returns what ran.

    Each file runs in its own transaction with its version row, so a failure
    leaves the database at the last good version and a rerun picks up there.
    """
    applied: list[str] = []
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        role = sql.Identifier(app_role).as_string(conn)
        conn.execute("SELECT pg_advisory_lock(%s)", (_MIGRATE_LOCK,))
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " version TEXT PRIMARY KEY,"
                " applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
            for path in migration_files():
                if path.stem in done:
                    continue
                with conn.transaction():
                    # bytes, not str: the file is trusted SQL, and only the
                    # quoted role identifier is substituted into it.
                    conn.execute(path.read_text().replace("{app_role}", role).encode())
                    conn.execute(
                        "INSERT INTO schema_migrations (version) VALUES (%s)", (path.stem,)
                    )
                applied.append(path.stem)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (_MIGRATE_LOCK,))
    return applied


__all__ = ["MIGRATIONS", "PostgresEventStore", "migrate", "migration_files"]
