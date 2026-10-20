"""Layer 9b: encryption at rest, two-tier, over every store.

A sealed campaign's events and snapshots are ciphertext at rest and plaintext
to every reader. Destroying a seat key erases that seat's own words and
nothing the fold needs.
"""

import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import psycopg
import pytest

from revisionist.core import delta as d
from revisionist.core import events as ev
from revisionist.core.fold import FoldError, replay, state_hash
from revisionist.core.ids import CampaignId, DeltaId, EventId, FactId
from revisionist.core.sealing import needs_admin_read, seal_holder
from revisionist.core.visibility import PUBLIC, private_to
from revisionist.engine.pg_store import PostgresEventStore
from revisionist.engine.seal import KeyringSealer, MemoryKeyring
from revisionist.engine.store import EventStore, MemoryStore, SqliteStore, StoreError, load_state
from tests import fixtures as fx
from tests.pg import PgDb

ORDER_TEXT = "Move the secret army north at dawn."
BRIEFING_TEXT = "Regent, the plot holds."


@dataclass
class Sealed:
    store: EventStore
    keyring: MemoryKeyring
    rows: Callable[[], list[str]]
    """Every kind and body as stored, events and snapshots."""


def _events() -> list[ev.Event]:
    events = fx.creation_events()
    n = len(events)
    return [
        *events,
        fx.envelope(
            n, ev.OrderReceived(seat_id=fx.NORTH, text=ORDER_TEXT), scope=private_to(fx.NORTH)
        ),
        fx.envelope(
            n + 1,
            ev.NarrationWritten(seat_id=fx.NORTH, text=BRIEFING_TEXT),
            scope=private_to(fx.NORTH),
        ),
        fx.envelope(n + 2, ev.NarrationWritten(seat_id=fx.HERALD, text="Calm seas."), scope=PUBLIC),
    ]


@pytest.fixture(params=["memory", "sqlite", "postgres"])
def sealed(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Sealed]:
    keyring = MemoryKeyring()
    keyring.create_campaign_key(fx.CAMPAIGN)
    sealer = KeyringSealer(keyring)
    if request.param == "memory":
        m = MemoryStore(sealer)
        yield Sealed(
            m,
            keyring,
            lambda: (
                [e.model_dump_json() for log in m._events.values() for e in log]
                + [body for _, body in m._snapshots.values()]
            ),
        )
    elif request.param == "sqlite":
        path = tmp_path / "c.db"
        s = SqliteStore(path, sealer)

        def sqlite_rows() -> list[str]:
            conn = sqlite3.connect(path)
            rows = conn.execute(
                "SELECT kind || body FROM events UNION ALL SELECT body FROM snapshots"
            )
            out = [r[0] for r in rows]
            conn.close()
            return out

        yield Sealed(s, keyring, sqlite_rows)
        s.close()
    else:
        db: PgDb = request.getfixturevalue("pg_db")
        p = PostgresEventStore(db.app_dsn, sealer)

        def pg_rows() -> list[str]:
            with psycopg.connect(db.app_dsn) as conn:
                rows = conn.execute(
                    "SELECT kind || body FROM events UNION ALL SELECT body FROM snapshots"
                )
                return [r[0] for r in rows]

        yield Sealed(p, keyring, pg_rows)
        p.close()


def test_readers_see_plaintext_and_rest_holds_ciphertext(sealed: Sealed) -> None:
    events = _events()
    sealed.store.append_all(events)
    sealed.store.save_snapshot(replay(events))
    assert list(sealed.store.read(fx.CAMPAIGN)) == events
    assert load_state(sealed.store, fx.CAMPAIGN) == replay(events)
    at_rest = "\n".join(sealed.rows())
    for secret in (ORDER_TEXT, BRIEFING_TEXT, fx.CANARY, "Calm seas.", "order_received"):
        assert secret not in at_rest
    assert at_rest.count('"kind":"sealed"') >= len(events)


def test_destroying_a_seat_key_erases_its_words_and_nothing_else(sealed: Sealed) -> None:
    events = _events()
    sealed.store.append_all(events)
    before = state_hash(load_state(sealed.store, fx.CAMPAIGN))
    seat_key = sealed.keyring.seats[(fx.CAMPAIGN, fx.NORTH)]
    sealed.keyring.destroy(seat_key)
    read = list(sealed.store.read(fx.CAMPAIGN))
    erased = [e for e in read if isinstance(e.payload, ev.Sealed)]
    assert len(erased) == 2 and {e.payload.holder for e in erased} == {"seat"}  # type: ignore[union-attr]
    assert ORDER_TEXT not in "\n".join(e.model_dump_json() for e in read)
    assert state_hash(replay(read)) == before  # the fold never needed them
    assert any(
        isinstance(e.payload, ev.NarrationWritten) and e.payload.seat_id == fx.HERALD for e in read
    )


def test_a_lost_campaign_key_stops_the_fold(sealed: Sealed) -> None:
    sealed.store.append_all(_events())
    sealed.keyring.destroy(sealed.keyring.campaigns[fx.CAMPAIGN])
    with pytest.raises(FoldError, match="still sealed"):
        replay(sealed.store.read(fx.CAMPAIGN))


def test_without_the_sealer_a_sealed_campaign_is_refused(sealed: Sealed) -> None:
    sealed.store.append_all(_events())
    sealed.store.sealer = None  # type: ignore[attr-defined]
    with pytest.raises(StoreError, match="sealed"):
        list(sealed.store.read(fx.CAMPAIGN))


def test_a_campaign_without_a_key_stays_plaintext(sealed: Sealed) -> None:
    other = CampaignId("01ARZ3NDEKTSV4RRFFQ69G5FA9")
    events = [
        e.model_copy(update={"campaign_id": other, "event_id": EventId("P" + e.event_id[1:])})
        for e in fx.creation_events()
    ]
    sealed.store.append_all(events)
    assert list(sealed.store.read(other)) == events
    assert any(fx.CANARY in row for row in sealed.rows())


def test_tampering_is_detected() -> None:
    keyring = MemoryKeyring()
    keyring.create_campaign_key(fx.CAMPAIGN)
    store = MemoryStore(KeyringSealer(keyring))
    store.append_all(_events())
    stub = store._events[fx.CAMPAIGN][0]
    assert stub.payload_enc is not None
    flipped = bytes([stub.payload_enc[-1] ^ 1])
    store._events[fx.CAMPAIGN][0] = stub.model_copy(
        update={"payload_enc": stub.payload_enc[:-1] + flipped}
    )
    with pytest.raises(StoreError, match="authenticate"):
        list(store.read(fx.CAMPAIGN))
    moved = stub.model_copy(update={"seq": 1})  # a valid ciphertext under another envelope
    store._events[fx.CAMPAIGN][1] = moved
    store._events[fx.CAMPAIGN][0] = stub
    with pytest.raises(StoreError, match="authenticate"):
        list(store.read(fx.CAMPAIGN))


def test_a_plaintext_event_in_a_sealed_campaign_is_refused() -> None:
    keyring = MemoryKeyring()
    store = MemoryStore()
    store.append_all(_events())  # written before the key existed, or behind the sealer's back
    keyring.create_campaign_key(fx.CAMPAIGN)
    store.sealer = KeyringSealer(keyring)
    with pytest.raises(StoreError, match="plaintext"):
        list(store.read(fx.CAMPAIGN))


# --- which key, and what counts as an admin read (pure) --------------------------


def test_seat_words_take_the_seat_key_and_the_rest_the_campaign_key() -> None:
    events = _events()
    assert seal_holder(events[-3]) == fx.NORTH  # what the player typed
    assert seal_holder(events[-2]) == fx.NORTH  # their private briefing
    assert seal_holder(events[-1]) is None  # the press is public
    assert all(seal_holder(e) is None for e in fx.creation_events())


def test_admin_reads_look_inside_payloads() -> None:
    covert = d.FactAssert(
        delta_id=DeltaId("01ARZ3NDEKTSV4RRFFQ69G5DX1"),
        reason="r",
        fact_id=FactId("01ARZ3NDEKTSV4RRFFQ69G5FX1"),
        subject=fx.E_NORTH,
        predicate="hides",
        obj=fx.E_SOUTH,
        scope=private_to(fx.NORTH, covert=True),
    )
    applied = fx.envelope(0, ev.DeltaApplied(intent_id=None, delta=covert))
    assert applied.scope == PUBLIC and needs_admin_read(applied)
    public = fx.envelope(1, ev.NarrationWritten(seat_id=fx.HERALD, text="news"))
    assert not needs_admin_read(public)
    assert needs_admin_read(_events()[-3])
