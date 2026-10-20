"""Layer 8: the tick scheduler.

The decision functions are pure and tested without a database. Everything
else runs against a scratch Postgres with a fake clock that the tests step;
nothing sleeps and no model is called (offline providers, or the scripted
``FakeClaude`` from the CLI tests).
"""

import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from typer.testing import CliRunner

from revisionist import cli
from revisionist.core.ids import CampaignId, IntentId
from revisionist.core.intent import Intent, Verb
from revisionist.core.resolution import TickStage
from revisionist.core.visibility import private_to
from revisionist.engine import campaign as cmp
from revisionist.engine import scheduler as sch
from revisionist.engine.llm import Reply, Request, TransportError
from revisionist.engine.pg_store import PostgresEventStore
from revisionist.engine.store import MemoryStore, load_state
from revisionist.engine.tick import file_intent
from tests.pg import PgDb
from tests.test_cli import FakeClaude

runner = CliRunner()

T0 = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
H = timedelta(hours=1)


# --- pure decisions -------------------------------------------------------------


def _row(*, paused: bool = False, advance_requested: bool = False) -> sch.Schedule:
    return sch.Schedule(
        CampaignId("c"), T0 + 12 * H, advance_requested=advance_requested, paused=paused
    )


@pytest.mark.parametrize(
    ("row", "now", "in_flight", "submitted", "expected"),
    [
        (_row(), T0, False, False, None),
        (_row(), T0 + 12 * H, False, False, sch.Why.TIMER),
        (_row(), T0 + 30 * H, False, False, sch.Why.TIMER),
        (_row(advance_requested=True), T0, False, False, sch.Why.ADVANCE),
        (_row(), T0, False, True, sch.Why.ALL_SUBMITTED),
        (_row(), T0, True, False, sch.Why.RESUME),
        (_row(paused=True), T0 + 30 * H, True, True, None),
        (_row(paused=True, advance_requested=True), T0, False, False, None),
    ],
)
def test_due(
    row: sch.Schedule, now: datetime, in_flight: bool, submitted: bool, expected: sch.Why | None
) -> None:
    assert sch.due(row, now, in_flight=in_flight, submitted=submitted) is expected


@pytest.mark.parametrize(
    ("previous", "now", "expected"),
    [
        (T0, T0, T0 + 12 * H),  # on time
        (T0, T0 - 5 * H, T0 + 12 * H),  # early (advance): the slot is spent
        (T0, T0 + 10 * H, T0 + 12 * H),  # late within the tick: no drift
        (T0, T0 + 12 * H, T0 + 24 * H),  # exactly one tick late
        (T0, T0 + 40 * H, T0 + 48 * H),  # missed ticks are skipped, not backfilled
    ],
)
def test_next_due_is_anchored(previous: datetime, now: datetime, expected: datetime) -> None:
    assert sch.next_due_after(previous, 12, now) == expected


def test_all_submitted_counts_only_new_intents_from_every_human() -> None:
    store = MemoryStore()
    world = cmp.load_world(cmp.world_path("salt-strait"))
    campaign_id = cmp.new_campaign(store, world)
    state = load_state(store, campaign_id)
    me = cmp.human_seat(state)
    tail = sch.since_last_close(list(store.read(campaign_id)))
    assert not sch.all_submitted(state, tail)
    wait = Intent(
        intent_id=IntentId("01ARZ3NDEKTSV4RRFFQ69G5W01"),
        actor=me,
        verb=Verb.WAIT,
        scope=private_to(me),
        filed_tick=0,
    )
    file_intent(store, campaign_id, wait, versions=cmp.versions())
    faction = cmp.faction_seats(state)[0]
    other = wait.model_copy(
        update={"intent_id": IntentId("01ARZ3NDEKTSV4RRFFQ69G5W02"), "actor": faction}
    )
    file_intent(store, campaign_id, other, versions=cmp.versions())
    state = load_state(store, campaign_id)
    events = list(store.read(campaign_id))
    assert sch.all_submitted(state, sch.since_last_close(events))
    assert not sch.in_flight(sch.since_last_close(events))
    no_humans = state.model_copy(update={"seats": {faction: state.seats[faction]}})
    assert not sch.all_submitted(no_humans, sch.since_last_close(events))


def test_all_submitted_waits_for_both_humans_in_a_duo() -> None:
    store = MemoryStore()
    campaign_id = cmp.new_campaign(store, cmp.load_world(cmp.world_path("salt-strait-duo")))
    first, second = cmp.human_seats(load_state(store, campaign_id))
    for n, seat in enumerate((first, second)):
        assert not sch.all_submitted(
            load_state(store, campaign_id), sch.since_last_close(list(store.read(campaign_id)))
        )
        wait = Intent(
            intent_id=IntentId(f"01ARZ3NDEKTSV4RRFFQ69G5W1{n}"),
            actor=seat,
            verb=Verb.WAIT,
            scope=private_to(seat),
            filed_tick=0,
        )
        file_intent(store, campaign_id, wait, versions=cmp.versions())
    events = list(store.read(campaign_id))
    assert sch.all_submitted(load_state(store, campaign_id), sch.since_last_close(events))


def test_cli_imports_without_psycopg() -> None:
    code = "import sys; sys.modules['psycopg'] = None; import revisionist.cli"
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0


# --- against Postgres -----------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def game(
    pg_db: PgDb, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[tuple[PgDb, Clock, CampaignId]]:
    """A salt-strait campaign on 12-hour ticks in Postgres, at T0 on a fake clock."""
    world = tmp_path / "strait-12h.yaml"
    world.write_text(
        (cmp.WORLDS_DIR / "salt-strait.yaml")
        .read_text()
        .replace("tick_hours: 24", "tick_hours: 12")
    )
    monkeypatch.setenv("REVISIONIST_DB", pg_db.app_dsn)
    monkeypatch.setenv("REVISIONIST_BRIEFINGS_DIR", str(tmp_path / "briefings"))
    monkeypatch.setattr(cli, "transport_factory", FakeClaude)
    clock = Clock()
    monkeypatch.setattr(cli, "clock", clock)
    assert runner.invoke(cli.app, ["new", str(world)]).exit_code == 0
    assert runner.invoke(cli.app, ["scheduler", "status"]).exit_code == 0  # registers at T0
    store = PostgresEventStore(pg_db.app_dsn)
    campaign_id = store.campaign_ids()[0]
    store.close()
    yield pg_db, clock, campaign_id


def _run(*args: str) -> tuple[int, str]:
    result = runner.invoke(cli.app, list(args))
    return result.exit_code, result.output


def _kinds(db: PgDb, campaign_id: CampaignId, kind: str) -> list[int]:
    store = PostgresEventStore(db.app_dsn)
    ticks = [e.tick for e in store.read(campaign_id) if e.kind == kind]
    store.close()
    return ticks


def _row_of(db: PgDb, campaign_id: CampaignId) -> sch.Schedule:
    store = PostgresEventStore(db.app_dsn)
    row = sch.ScheduleTable(store.conn).get(campaign_id)
    store.close()
    return row


def test_three_days_of_twelve_hour_ticks(game: tuple[PgDb, Clock, CampaignId]) -> None:
    """The accelerated stand-in for the Layer 8 soak: 72 hours, polled every 10 minutes."""
    db, clock, campaign_id = game
    crash_at, contend_at = T0 + 36 * H, T0 + 48 * H
    while clock.now <= T0 + 72 * H:
        if clock.now == crash_at:
            # the tick due now dies after NARRATE; the next poll must resume it
            store = PostgresEventStore(db.app_dsn)
            outcomes = sch.run(
                store,
                clock,
                offline=True,
                transport_factory=FakeClaude,
                briefings=cmp.briefings_dir(),
                warn=print,
                stop_after=TickStage.NARRATE,
            )
            store.close()
            assert [o.action for o in outcomes] == [sch.Action.STOPPED]
        elif clock.now == contend_at:
            # another run holds the campaign: this one must skip, not tick twice
            with psycopg.connect(db.app_dsn, autocommit=True) as other:
                other.execute(
                    "SELECT pg_advisory_lock(%s, hashtext(%s))", (0x7276736E, campaign_id)
                )
                code, out = _run("scheduler", "run", "--offline")
                assert code == 0 and "locked" in out
        else:
            code, out = _run("scheduler", "run", "--offline")
            assert code == 0, out
        clock.now += timedelta(minutes=10)

    assert _kinds(db, campaign_id, "tick_closed") == [0, 1, 2, 3, 4, 5]
    assert _kinds(db, campaign_id, "tick_opened") == [0, 1, 2, 3, 4, 5]
    row = _row_of(db, campaign_id)
    assert row.next_due_at == T0 + 84 * H  # the contended tick ran late; no drift
    assert row.consecutive_failures == 0 and not row.paused
    assert row.last_status == "tick 5 resolved (timer)"
    code, out = _run("db", "verify")
    assert code == 0 and "tick 6" in out


def test_ticks_fire_on_advance_and_on_all_submitted(game: tuple[PgDb, Clock, CampaignId]) -> None:
    db, clock, campaign_id = game
    assert _run("scheduler", "run", "--offline") == (0, "")
    assert "advance requested" in _run("scheduler", "advance")[1]
    assert "advance requested" in _run("scheduler", "status")[1]
    assert "ticked: tick 0 resolved (advance)" in _run("scheduler", "run", "--offline")[1]
    assert _row_of(db, campaign_id).next_due_at == T0 + 24 * H  # the 12h slot was spent

    clock.now += H
    assert _run("order", "Declare the strait open.")[0] == 0
    assert "tick 1 resolved (all submitted)" in _run("scheduler", "run", "--offline")[1]
    clock.now += H
    assert _run("scheduler", "run", "--offline") == (0, "")  # nothing new since the close
    assert _kinds(db, campaign_id, "tick_closed") == [0, 1]


def test_pause_and_resume(game: tuple[PgDb, Clock, CampaignId]) -> None:
    _, clock, campaign_id = game
    assert "paused" in _run("scheduler", "pause", "--reason", "holiday")[1]
    clock.now += 13 * H
    code, out = _run("scheduler", "run", "--offline")
    assert code == 0 and "paused: holiday" in out
    assert "paused: holiday" in _run("scheduler", "status", "--campaign", campaign_id)[1]
    assert "resumed" in _run("scheduler", "resume")[1]
    assert "tick 0 resolved (timer)" in _run("scheduler", "run", "--offline")[1]


class BrokenNarrator(FakeClaude):
    def __init__(self) -> None:
        super().__init__()
        self.broken = True

    def call(self, request: Request) -> Reply:
        if self.broken and request.call_class == "narrator":
            raise TransportError("claude cli exit 1: overloaded")
        return super().call(request)


def test_three_failures_pause_the_campaign_and_the_tick_resumes(
    game: tuple[PgDb, Clock, CampaignId], monkeypatch: pytest.MonkeyPatch
) -> None:
    db, clock, campaign_id = game
    claude = BrokenNarrator()
    monkeypatch.setattr(cli, "transport_factory", lambda: claude)
    clock.now += 12 * H
    for attempt in (1, 2):
        code, out = _run("scheduler", "run")
        assert code == 0 and "failed (" in out and "overloaded" in out, attempt
        assert _row_of(db, campaign_id).consecutive_failures == attempt
        clock.now += timedelta(minutes=10)
    code, out = _run("scheduler", "run")
    assert code == cli.EXIT_ERROR
    row = _row_of(db, campaign_id)
    assert row.paused and row.paused_reason == "3 consecutive failures"
    assert "failed (resume): TransportError" in row.last_status

    clock.now += timedelta(minutes=10)
    assert _run("scheduler", "run")[0] == 0  # paused: nothing happens
    assert claude.calls.count("deputy") == 3  # deputies ran once, before the freeze

    claude.broken = False
    _run("scheduler", "resume")
    assert "tick 0 resolved (resume)" in _run("scheduler", "run")[1]
    assert _kinds(db, campaign_id, "tick_opened") == [0]
    assert _kinds(db, campaign_id, "tick_closed") == [0]
    assert _row_of(db, campaign_id).consecutive_failures == 0


def test_a_canary_leak_pauses_at_once(
    game: tuple[PgDb, Clock, CampaignId], monkeypatch: pytest.MonkeyPatch
) -> None:
    db, clock, campaign_id = game
    leaky = FakeClaude(narration="Rumour: CANARY-OSTREL-HARROW-4c1e")
    monkeypatch.setattr(cli, "transport_factory", lambda: leaky)
    clock.now += 12 * H
    code, out = _run("scheduler", "run")
    assert code == cli.EXIT_LEAK and "LEAK" in out
    row = _row_of(db, campaign_id)
    assert row.paused and row.paused_reason == "canary leak at tick 0"
    clock.now += 12 * H
    assert _run("scheduler", "run")[0] == 0
    assert _kinds(db, campaign_id, "tick_closed") == [0]


def test_the_scheduler_needs_postgres(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("REVISIONIST_DB", str(tmp_path / "solo.db"))
    code, out = _run("scheduler", "run")
    assert code == cli.EXIT_ERROR and "revisionist tick" in out


def test_unknown_campaign_is_an_error(game: tuple[PgDb, Clock, CampaignId]) -> None:
    code, out = _run("scheduler", "status", "--campaign", "NOPE")
    assert code == cli.EXIT_ERROR and "not scheduled" in out


def test_a_pause_issued_mid_tick_survives_the_tick(
    game: tuple[PgDb, Clock, CampaignId], monkeypatch: pytest.MonkeyPatch
) -> None:
    db, clock, campaign_id = game

    class HostPausesDuringNarration(FakeClaude):
        def call(self, request: Request) -> Reply:
            if request.call_class == "narrator":
                with psycopg.connect(db.app_dsn, autocommit=True) as host:
                    sch.ScheduleTable(host).pause(campaign_id, "mid-tick")
            return super().call(request)

    claude = HostPausesDuringNarration()
    monkeypatch.setattr(cli, "transport_factory", lambda: claude)
    clock.now += 12 * H
    assert "tick 0 resolved (timer)" in _run("scheduler", "run")[1]
    row = _row_of(db, campaign_id)
    assert row.paused and row.paused_reason == "mid-tick"
    assert row.next_due_at == T0 + 24 * H
