"""Host operations, called without the CLI: the lines the host reads and the admin moves.

The Postgres cases use the ``pg_db`` fixture; the rest run on a memory store.
"""

import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from revisionist.core.ids import CampaignId
from revisionist.core.ontology import Controller
from revisionist.engine import campaign as cmp
from revisionist.engine import host_ops, season, session_zero
from revisionist.engine import scheduler as sch
from revisionist.engine.discord.tables import Invites, SeatLinks
from revisionist.engine.pg_store import PostgresEventStore
from revisionist.engine.store import MemoryStore, load_state
from tests.pg import PgDb


@pytest.fixture
def memory() -> tuple[MemoryStore, CampaignId]:
    store = MemoryStore()
    campaign_id = cmp.new_campaign(store, cmp.load_world(cmp.world_path("salt-strait")))
    return store, campaign_id


@pytest.fixture
def pg(pg_db: PgDb) -> Iterator[tuple[PostgresEventStore, CampaignId]]:
    store = PostgresEventStore(pg_db.app_dsn)
    campaign_id = cmp.new_campaign(store, cmp.load_world(cmp.world_path("salt-strait")))
    yield store, campaign_id
    store.close()


# --- goals and the season -------------------------------------------------------------


def test_flagged_goals_show_scores_and_approval_waits_for_session_zero(
    memory: tuple[MemoryStore, CampaignId],
) -> None:
    store, campaign_id = memory
    flagged = [seat for seat, _ in session_zero.flagged(store, campaign_id)]
    assert flagged
    lines = host_ops.goal_flag_lines(store, campaign_id)
    assert len(lines) == len(flagged)
    assert all(re.fullmatch(r".+: power \d+, difficulty \d+, \w+", line) for line in lines)
    with pytest.raises(session_zero.StepRefusedError):
        host_ops.approve_goal(store, campaign_id, flagged[0])


def test_approving_an_unknown_seat_is_refused(memory: tuple[MemoryStore, CampaignId]) -> None:
    store, campaign_id = memory
    with pytest.raises(host_ops.HostOpError, match=re.escape("No seat called 'nobody' here.")):
        host_ops.approve_goal(store, campaign_id, "nobody")


def test_approving_an_ai_seat_is_refused(memory: tuple[MemoryStore, CampaignId]) -> None:
    store, campaign_id = memory
    ai = next(
        s for s in load_state(store, campaign_id).seats.values() if s.controller is Controller.AI
    )
    with pytest.raises(host_ops.HostOpError, match="is not a human seat"):
        host_ops.approve_goal(store, campaign_id, ai.name)


def test_the_season_report_waits_for_the_end(memory: tuple[MemoryStore, CampaignId]) -> None:
    store, campaign_id = memory
    with pytest.raises(season.SeasonError):
        host_ops.season_report_lines(store, campaign_id)


# --- cost, leaks, why, replay, verify ---------------------------------------------


def test_cost_starts_with_its_header_row(memory: tuple[MemoryStore, CampaignId]) -> None:
    store, campaign_id = memory
    header = host_ops.cost_text(store, campaign_id).splitlines()[0]
    assert header.split() == ["tick", "in-tick", "outside", "calls"]


def test_a_fresh_campaign_leaks_nothing(memory: tuple[MemoryStore, CampaignId]) -> None:
    store, campaign_id = memory
    assert host_ops.leak_lines(store, campaign_id) == (["0 leaks"], False)


def test_leaks_per_human_add_a_count_line(memory: tuple[MemoryStore, CampaignId]) -> None:
    store, campaign_id = memory
    holdings = {"host": frozenset(cmp.human_seats(load_state(store, campaign_id)))}
    lines, leaked = host_ops.leak_lines(store, campaign_id, holdings)
    assert lines == ["0 leaks", "0 leaks across 1 people"] and not leaked


def test_why_on_an_unknown_target_finds_nothing(memory: tuple[MemoryStore, CampaignId]) -> None:
    store, campaign_id = memory
    assert host_ops.why_lines(store, campaign_id, "01NOSUCHEVENT") == ["nothing found"]


def test_replay_line_gives_the_tick_seq_and_hash(memory: tuple[MemoryStore, CampaignId]) -> None:
    store, campaign_id = memory
    line = host_ops.replay_line(store, campaign_id, 0)
    assert re.fullmatch(r"tick 0  seq \d+  hash \S+  assets \d+  facts \d+  pending \d+", line)


def test_verify_says_ok_for_a_sound_campaign(memory: tuple[MemoryStore, CampaignId]) -> None:
    store, campaign_id = memory
    lines, ok = host_ops.verify_lines(store, [campaign_id])
    assert ok and re.fullmatch(rf"ok {campaign_id}: \d+ events, tick 0, hash \S+", lines[0])


# --- Postgres: seats, invites, schedule, holdings -----------------------------------

HUMAN_USER = "1234567890"
HOST_USER = "9999999999"
GUILD = "5555555555"
NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)


def test_no_seats_are_linked_yet(pg: tuple[PostgresEventStore, CampaignId]) -> None:
    store, _ = pg
    assert host_ops.seat_lines(store.conn) == ["no seats linked"]


def test_a_linked_seat_is_listed_for_its_campaign(
    pg: tuple[PostgresEventStore, CampaignId],
) -> None:
    store, campaign_id = pg
    seat = cmp.human_seats(load_state(store, campaign_id))[0]
    SeatLinks(store.conn).link(campaign_id, seat, HUMAN_USER, GUILD)
    [line] = host_ops.seat_lines(store.conn, campaign_id)
    assert re.fullmatch(
        rf"{campaign_id}  {seat}  user {HUMAN_USER}  guild {GUILD}"
        r"  since \d{4}-\d\d-\d\d \d\d:\d\dZ",
        line,
    )
    assert host_ops.seat_lines(store.conn, CampaignId("01NOSUCHCAMPAIGN")) == ["no seats linked"]


def test_no_invites_yet(pg: tuple[PostgresEventStore, CampaignId]) -> None:
    store, campaign_id = pg
    assert host_ops.invite_lines(store.conn, campaign_id, NOW) == ["no invites"]


def test_an_invite_says_when_it_is_open_and_when_it_expires(
    pg: tuple[PostgresEventStore, CampaignId],
) -> None:
    store, campaign_id = pg
    seat = cmp.human_seats(load_state(store, campaign_id))[0]
    Invites(store.conn).issue(campaign_id, seat, NOW, 72)
    assert host_ops.invite_lines(store.conn, campaign_id, NOW) == [
        f"{seat}  issued 2026-10-01 12:00Z  open until 2026-10-04 12:00Z"
    ]
    assert host_ops.invite_lines(store.conn, campaign_id, NOW + timedelta(days=4)) == [
        f"{seat}  issued 2026-10-01 12:00Z  expired"
    ]


def test_a_schedule_line_says_when_the_next_tick_is_due(
    pg: tuple[PostgresEventStore, CampaignId],
) -> None:
    store, campaign_id = pg
    sch.register(store, NOW)
    assert host_ops.schedule_lines(store, [campaign_id]) == [
        f"{campaign_id}  next 2026-10-02 12:00Z  active  failures 0  last -"
    ]
    sch.ScheduleTable(store.conn).pause(campaign_id, "host away")
    assert host_ops.schedule_lines(store, [campaign_id]) == [
        f"{campaign_id}  next 2026-10-02 12:00Z  paused: host away  failures 0  last -"
    ]


def test_holdings_give_the_host_every_human_seat_nobody_links(
    pg: tuple[PostgresEventStore, CampaignId],
) -> None:
    store, campaign_id = pg
    state = load_state(store, campaign_id)
    humans = frozenset(cmp.human_seats(state))
    assert host_ops.holdings(store.conn, state, None) == {"host": humans}
    seat = cmp.human_seats(state)[0]
    SeatLinks(store.conn).link(campaign_id, seat, HUMAN_USER, GUILD)
    assert host_ops.holdings(store.conn, state, HOST_USER) == {
        HOST_USER: humans - {seat},
        HUMAN_USER: frozenset({seat}),
    }
