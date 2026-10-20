"""Layers 9a-9b: briefings by Discord DM over REST, and the routing deliverer.

The REST API is ``FakeDiscord``; the real HTTP client is blocked for every
test (``conftest.no_discord_network``).
"""

import logging
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from hypothesis import given
from hypothesis import strategies as st
from psycopg import errors
from typer.testing import CliRunner

from revisionist import cli
from revisionist.core import events as ev
from revisionist.core.ids import CampaignId, SeatId
from revisionist.engine.discord import delivery
from revisionist.engine.discord.rest import (
    HELD_LABEL,
    PART_LIMIT,
    DiscordDeliverer,
    DiscordError,
    DiscordRest,
    MemoryOutbox,
    nonce_for,
    split_message,
)
from revisionist.engine.discord.tables import PgOutbox, SeatLinks
from revisionist.engine.pg_store import PostgresEventStore
from revisionist.engine.store import load_state
from tests.discord_fakes import FakeDiscord
from tests.pg import PgDb
from tests.test_cli import FakeClaude

TOKEN = "MTIz.fake-bot-token.SECRET-do-not-log"
SEAT = SeatId("01ARZ3NDEKTSV4RRFFQ69G5W0S")
USER = "111111111111111111"
KEY = "01ARZ3NDEKTSV4RRFFQ69G5WKY"
CANARY = "CANARY-OSTREL-HARROW-4c1e"

runner = CliRunner()


def _deliverer(fake: FakeDiscord, outbox: MemoryOutbox | None = None) -> DiscordDeliverer:
    rest = DiscordRest(TOKEN, http=fake, sleep=lambda s: None)
    return DiscordDeliverer(rest, outbox or MemoryOutbox(), {SEAT: USER})


# --- splitting ------------------------------------------------------------------


def test_a_long_briefing_splits_on_paragraphs_and_rejoins() -> None:
    paragraphs = [f"Paragraph {n}: " + "the tide turns. " * 40 for n in range(12)]
    text = "\n\n".join(paragraphs)
    parts = split_message(text)
    assert len(parts) > 1
    assert all(len(p) <= PART_LIMIT for p in parts)
    assert "".join(parts) == text
    assert all(p.startswith("Paragraph") for p in parts)  # no paragraph was cut


def test_one_huge_line_is_cut_hard() -> None:
    text = "x" * 5000
    parts = split_message(text)
    assert [len(p) for p in parts] == [1900, 1900, 1200]
    assert "".join(parts) == text


@given(st.text(alphabet=st.sampled_from("ab \n"), max_size=6000))
def test_split_is_bounded_and_lossless(text: str) -> None:
    parts = split_message(text)
    assert all(0 < len(p) <= PART_LIMIT for p in parts)
    assert "".join(parts) == text


def test_nonce_fits_discord() -> None:
    assert len(nonce_for(KEY, 0)) == 25
    assert nonce_for(KEY, 0) == nonce_for(KEY, 0) != nonce_for(KEY, 1)


# --- the outbox -----------------------------------------------------------------


def test_delivering_the_same_key_twice_sends_once() -> None:
    fake = FakeDiscord()
    deliverer = _deliverer(fake)
    assert deliverer.deliver(SEAT, "The harbour is calm.", KEY) == "discord"
    assert deliverer.deliver(SEAT, "The harbour is calm.", KEY) == "discord"
    assert len(fake.posts()) == 1 and len(fake.messages) == 1
    assert fake.messages[0].channel_id == f"dm-{USER}"


def test_a_long_briefing_goes_out_in_parts() -> None:
    text = "\n\n".join("Para " + "word " * 300 for _ in range(4))
    fake = FakeDiscord()
    _deliverer(fake).deliver(SEAT, text, KEY)
    assert len(fake.messages) == len(split_message(text)) > 1
    assert all(len(m.content) <= PART_LIMIT for m in fake.messages)
    assert "".join(m.content for m in fake.messages) == text


class CrashingOutbox(MemoryOutbox):
    """Dies once after Discord accepted a part, before the row lands."""

    crash_on: int | None = None

    def record(self, idempotency_key: str, part: int, channel_id: str, message_id: str) -> None:
        if part == self.crash_on:
            self.crash_on = None
            raise RuntimeError("killed between send and insert")
        super().record(idempotency_key, part, channel_id, message_id)


def test_a_crash_between_send_and_insert_resends_with_the_same_nonce() -> None:
    text = "\n\n".join("Para " + "word " * 300 for _ in range(4))
    fake = FakeDiscord()
    outbox = CrashingOutbox()
    outbox.crash_on = 1
    deliverer = _deliverer(fake, outbox)
    with pytest.raises(RuntimeError):
        deliverer.deliver(SEAT, text, KEY)
    assert outbox.sent(KEY) == {0}
    deliverer.deliver(SEAT, text, KEY)
    nonces = [p["nonce"] for p in fake.posts()]
    parts = len(split_message(text))
    assert nonces.count(nonce_for(KEY, 1)) == 2  # part 1 was resent with the same nonce
    assert nonces.count(nonce_for(KEY, 0)) == 1  # part 0 was recorded, so not resent
    assert len(fake.messages) == parts  # and Discord kept one copy of each
    assert outbox.sent(KEY) == set(range(parts))


# --- rate limits, errors, closed DMs --------------------------------------------


def test_a_rate_limit_is_waited_out() -> None:
    fake = FakeDiscord(rate_limits=2)
    waits: list[float] = []
    rest = DiscordRest(TOKEN, http=fake, sleep=waits.append)
    assert rest.open_dm(USER) == f"dm-{USER}"
    assert waits == [0.25, 0.25]


def test_three_rate_limits_give_up() -> None:
    fake = FakeDiscord(rate_limits=5)
    rest = DiscordRest(TOKEN, http=fake, sleep=lambda s: None)
    with pytest.raises(DiscordError, match="HTTP 429"):
        rest.open_dm(USER)
    assert len(fake.requests) == 3


def test_closed_dms_hold_the_briefing_and_send_it_nowhere() -> None:
    fake = FakeDiscord(closed_dms={USER})
    outbox = MemoryOutbox()
    label = _deliverer(fake, outbox).deliver(SEAT, "The harbour is calm.", KEY)
    assert label == HELD_LABEL
    assert fake.messages == [] and outbox.rows == {}
    assert [url for _, url, _, _ in fake.requests if url.endswith("/messages")] == [
        f"https://discord.com/api/v10/channels/dm-{USER}/messages"
    ]  # the one refused attempt, at the player's own DM


def test_other_errors_propagate() -> None:
    fake = FakeDiscord(fail_with=(403, 50001))
    with pytest.raises(DiscordError, match="50001"):
        _deliverer(fake).deliver(SEAT, "The harbour is calm.", KEY)
    assert fake.messages == []


# --- token hygiene --------------------------------------------------------------


class Unreachable:
    def request(self, *args: object) -> tuple[int, bytes]:
        raise OSError(f"connection refused while sending Authorization: Bot {TOKEN}")


@pytest.mark.parametrize(
    "http", [FakeDiscord(fail_with=(500, 0)), FakeDiscord(fail_with=(401, 0)), Unreachable()]
)
def test_the_token_never_appears_in_errors_reprs_or_logs(
    http: object, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    rest = DiscordRest(TOKEN, http=http, sleep=lambda s: None)  # type: ignore[arg-type]
    deliverer = DiscordDeliverer(rest, MemoryOutbox(), {SEAT: USER})
    with pytest.raises(DiscordError) as caught:
        deliverer.deliver(SEAT, "The harbour is calm.", KEY)
    exc = caught.value
    seen = [str(exc), repr(exc), repr(rest), repr(deliverer), caplog.text]
    seen += [str(exc.__cause__), str(exc.__context__)]
    assert all(TOKEN not in s for s in seen)
    if isinstance(http, FakeDiscord):
        assert http.requests[0][2]["Authorization"] == f"Bot {TOKEN}"  # it was used, though


# --- routing and the operational tables (Postgres) -----------------------------


@pytest.fixture
def linked(
    pg_db: PgDb, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[tuple[PgDb, FakeDiscord]]:
    """A Postgres campaign with the human seat bound to ``USER`` and a bot token set."""
    monkeypatch.setenv("REVISIONIST_DB", pg_db.app_dsn)
    monkeypatch.setenv("REVISIONIST_BRIEFINGS_DIR", str(tmp_path / "briefings"))
    monkeypatch.setenv("REVISIONIST_DISCORD_TOKEN", TOKEN)
    monkeypatch.setenv("REVISIONIST_DISCORD_GUILD_ID", "222")
    monkeypatch.setattr(cli, "transport_factory", FakeClaude)
    fake = FakeDiscord()
    monkeypatch.setattr(
        delivery, "DiscordRest", lambda token: DiscordRest(token, http=fake, sleep=lambda s: None)
    )
    assert runner.invoke(cli.app, ["new", "salt-strait"]).exit_code == 0
    result = runner.invoke(cli.app, ["discord", "link", "--user", USER])
    assert result.exit_code == 0, result.output
    yield pg_db, fake


def _deliveries(db: PgDb) -> dict[str, str]:
    store = PostgresEventStore(db.app_dsn)
    campaign_id = store.campaign_ids()[0]
    state = load_state(store, campaign_id)
    out = {
        state.seats[e.payload.seat_id].name: e.payload.channel
        for e in store.read(campaign_id)
        if isinstance(e.payload, ev.BriefingDelivered)
    }
    store.close()
    return out


def test_a_tick_dms_the_bound_seat_and_files_the_rest(
    linked: tuple[PgDb, FakeDiscord], tmp_path: Path
) -> None:
    db, fake = linked
    result = runner.invoke(cli.app, ["tick", "--offline"])
    assert result.exit_code == 0, result.output
    channels = _deliveries(db)
    assert channels.pop("First Minister of Varda") == "discord"
    assert channels and set(channels.values()) == {"file"}  # the outlet
    assert [m.channel_id for m in fake.messages] == [f"dm-{USER}"]
    files = {p.name for p in (tmp_path / "briefings").glob("*.md")}
    assert "t000-first-minister-of-varda.md" not in files and files
    with psycopg.connect(db.app_dsn) as conn:
        assert conn.execute("SELECT count(*) FROM discord_deliveries").fetchone() == (1,)


def test_the_scheduler_delivers_by_dm_too(linked: tuple[PgDb, FakeDiscord]) -> None:
    db, fake = linked
    assert runner.invoke(cli.app, ["scheduler", "advance"]).exit_code == 0
    result = runner.invoke(cli.app, ["scheduler", "run", "--offline"])
    assert result.exit_code == 0 and "ticked" in result.output, result.output
    assert _deliveries(db)["First Minister of Varda"] == "discord"
    assert len(fake.messages) == 1


def test_no_token_means_files_as_before(
    linked: tuple[PgDb, FakeDiscord], monkeypatch: pytest.MonkeyPatch
) -> None:
    db, fake = linked
    monkeypatch.delenv("REVISIONIST_DISCORD_TOKEN")
    assert runner.invoke(cli.app, ["tick", "--offline"]).exit_code == 0
    assert set(_deliveries(db).values()) == {"file"}
    assert fake.requests == []


def test_a_closed_dm_holds_the_briefing_and_the_tick_closes(
    linked: tuple[PgDb, FakeDiscord], tmp_path: Path
) -> None:
    db, fake = linked
    fake.closed_dms.add(USER)
    result = runner.invoke(cli.app, ["tick", "--offline"])
    assert result.exit_code == 0, result.output
    assert _deliveries(db)["First Minister of Varda"] == HELD_LABEL
    assert fake.messages == []
    files = {p.name for p in (tmp_path / "briefings").glob("*.md")}
    assert "t000-first-minister-of-varda.md" not in files  # held, not written for the host


def test_a_dm_failure_other_than_closed_leaves_the_tick_resumable(
    linked: tuple[PgDb, FakeDiscord],
) -> None:
    db, fake = linked
    fake.fail_with = (500, 0)
    result = runner.invoke(cli.app, ["tick", "--offline"])
    assert result.exit_code != 0 and isinstance(result.exception, DiscordError)
    assert TOKEN not in str(result.exception)
    assert _deliveries(db).get("First Minister of Varda") is None
    fake.fail_with = None
    assert runner.invoke(cli.app, ["tick", "--offline"]).exit_code == 0  # resumes DELIVER
    assert _deliveries(db)["First Minister of Varda"] == "discord"
    assert len(fake.messages) == 1


def test_a_leaking_narration_is_withheld_from_discord(
    linked: tuple[PgDb, FakeDiscord], monkeypatch: pytest.MonkeyPatch
) -> None:
    db, fake = linked
    leaky = FakeClaude(narration=f"Rumour: {CANARY}")
    monkeypatch.setattr(cli, "transport_factory", lambda: leaky)
    result = runner.invoke(cli.app, ["tick"])
    assert result.exit_code == cli.EXIT_LEAK
    assert _deliveries(db)["First Minister of Varda"] == delivery.WITHHELD_LABEL
    assert CANARY not in fake.text() and fake.posts() == []


def test_pg_outbox_is_idempotent(pg_db: PgDb) -> None:
    with psycopg.connect(pg_db.app_dsn, autocommit=True) as conn:
        outbox = PgOutbox(conn)
        outbox.record(KEY, 0, "c", "m1")
        outbox.record(KEY, 0, "c", "m2")
        outbox.record(KEY, 2, "c", "m3")
        assert outbox.sent(KEY) == {0, 2} and outbox.sent("other") == set()


def test_app_role_grants_on_the_discord_tables(pg_db: PgDb) -> None:
    with psycopg.connect(pg_db.app_dsn, autocommit=True) as conn:
        links = SeatLinks(conn)
        links.link(CampaignId("c"), SEAT, USER, "g")
        conn.execute("UPDATE discord_seats SET guild_id = 'h'")
        assert links.unlink(CampaignId("c"), SEAT)
        PgOutbox(conn).record(KEY, 0, "c", "m")
        conn.execute(
            "INSERT INTO discord_bugs (campaign_id, seat_id, seq, state_hash, notes)"
            " VALUES ('c', 's', 1, 'h', 'n')"
        )
        for statement in (
            "UPDATE discord_deliveries SET message_id = 'x'",
            "DELETE FROM discord_deliveries",
            "UPDATE discord_bugs SET notes = 'x'",
            "DELETE FROM discord_bugs",
        ):
            with pytest.raises(errors.InsufficientPrivilege):
                conn.execute(statement)
