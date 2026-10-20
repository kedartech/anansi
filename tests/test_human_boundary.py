"""Layer 9b: the human-boundary canary (spec §7).

The opacity boundary is the person, not the seat. Every narration, briefing
and reply here echoes its whole prompt, View JSON included, so nothing but
``project()`` and the delivery guards stands between a secret and the wrong
person. Then everything each person could read is scanned against the union of
what their seats may know. Never skipped: the Postgres half fails, not skips,
when ``REVISIONIST_REQUIRE_PG=1``.
"""

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from revisionist import cli
from revisionist.core import delta as d
from revisionist.core.canary import canaries_hidden_from_human, scan_humans, surfaces_from_log
from revisionist.core.fold import replay
from revisionist.core.ids import IntentId, SeatId
from revisionist.core.intent import Intent, Verb
from revisionist.core.state import View
from revisionist.core.visibility import private_to
from revisionist.engine.llm import Reply, Request
from revisionist.engine.providers import RecordingDeliverer, ScriptedResolver
from revisionist.engine.store import MemoryStore, load_state
from revisionist.engine.tick import Narration, Providers, file_intent, run_tick
from tests import fixtures as fx
from tests.pg import PgDb
from tests.test_cli import FakeClaude
from tests.test_discord_bot import GUILD, HOST, ORDER, Game
from tests.test_discord_duo import FRIEND, OSTREL, PUBLIC, VARDA

runner = CliRunner()

VARDA_OWN = "CANARY-VARDA-SKIM-55d0"
OSTREL_OWN = "CANARY-OSTREL-HARROW-4c1e"


class EchoClaude(FakeClaude):
    """Every prose reply is its own prompt, so it carries the reader's whole view."""

    def __init__(self, leak_to: str | None = None, leak: str = "") -> None:
        super().__init__()
        self.leak_to, self.leak = leak_to, leak

    def call(self, request: Request) -> Reply:
        out: dict[str, Any]
        match request.call_class:
            case "narrator":
                text = request.prompt
                if self.leak_to and self.leak_to in request.prompt.splitlines()[0]:
                    text += f"\nRumour: {self.leak}"
                out = {"text": text}
            case "brief":
                out = {"text": request.prompt, "dilemma": "?", "moves": ["a", "b", "c"]}
            case _:
                return super().call(request)
        return Reply(out, request.model, 100, 20, 0, 0.001)


class People:
    """What each person could read: their replies, their DMs, the public channel."""

    def __init__(self, game: Game) -> None:
        self.game = game
        self.replies: dict[str, list[tuple[str, str]]] = {HOST: [], FRIEND: []}

    def command(self, name: str, user: str, **options: str) -> str:
        reply = self.game.command(name, user, **options)
        self.replies[user].append((f"/{name}", reply))
        return reply

    def dm(self, user: str, text: str) -> None:
        for reply in self.game.dm(user, text):
            self.replies[user].append(("dm reply", reply))

    def seen(self) -> dict[str, list[tuple[str, str]]]:
        out = {user: list(texts) for user, texts in self.replies.items()}
        for m in self.game.discord.messages:
            for user in out:
                if m.channel_id in (f"dm-{user}", PUBLIC):
                    out[user].append((f"message {m.message_id}", m.content))
        return out


def _duo(pg_db: PgDb, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, claude: EchoClaude) -> Game:
    monkeypatch.setenv("REVISIONIST_DB", pg_db.app_dsn)
    monkeypatch.setenv("REVISIONIST_DISCORD_GUILD_ID", GUILD)
    monkeypatch.setenv("REVISIONIST_DISCORD_HOST_ID", HOST)
    monkeypatch.setenv("REVISIONIST_BRIEFINGS_DIR", str(tmp_path / "briefings"))
    monkeypatch.setattr(cli, "transport_factory", lambda: claude)
    game = Game(pg_db, claude, tmp_path / "briefings")
    monkeypatch.setattr(cli, "clock", game.clock)
    assert runner.invoke(cli.app, ["new", "salt-strait-duo"]).exit_code == 0
    assert (
        runner.invoke(cli.app, ["discord", "link", "--user", HOST, "--seat", VARDA]).exit_code == 0
    )
    assert runner.invoke(cli.app, ["discord", "channel", "public", PUBLIC]).exit_code == 0
    invite = runner.invoke(cli.app, ["discord", "invite", "--seat", OSTREL])
    assert game.command("join", FRIEND, code=invite.output.splitlines()[1].strip()).startswith(
        "You"
    )
    return game


def _holdings(game: Game) -> dict[str, frozenset[SeatId]]:
    store = game.store()
    state = load_state(store, game.campaign())
    store.close()
    seat = {s.name: s.seat_id for s in state.seats.values()}
    return {HOST: frozenset({seat[VARDA]}), FRIEND: frozenset({seat[OSTREL]})}


def test_twelve_ticks_and_neither_person_reads_the_others_secrets(
    pg_db: PgDb, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    game = _duo(pg_db, monkeypatch, tmp_path, EchoClaude())
    people = People(game)
    for n in range(12):
        visibility = ("covert", "public", "private")[n % 3]
        people.command("order", HOST, text=ORDER, visibility=visibility, spin=f"Varda {n}.")
        if n % 2:
            people.dm(FRIEND, ORDER)
        else:
            people.command("order", FRIEND, text=ORDER, visibility="covert", spin=f"Ostrel {n}.")
        for user in (HOST, FRIEND):
            people.command("status", user)
            people.command("briefing", user)
            people.command("dispatch", user)
        assert "ticked" in people.command("advance", HOST), n

    holdings = _holdings(game)
    store = game.store()
    state = load_state(store, game.campaign())
    events = list(store.read(game.campaign()))
    store.close()
    seen = people.seen()
    assert scan_humans(state, holdings, seen) == []
    assert scan_humans(state, holdings, surfaces_from_log(events, holdings)) == []
    # the echo carried each person's own secret to them, so the scan had something to find
    assert any(VARDA_OWN in t for _, t in seen[HOST])
    assert any(OSTREL_OWN in t for _, t in seen[FRIEND])
    assert any(f"{OSTREL}: Ostrel 0." == t for _, t in seen[HOST])  # statements are public
    # what the host can read on the host: briefings directory and the bot's log
    host_hidden = canaries_hidden_from_human(state, holdings[HOST])
    on_disk = [p.read_text() for p in (tmp_path / "briefings").glob("**/*") if p.is_file()]
    for text in (*on_disk, caplog.text):
        assert not any(c in text for c in host_hidden)
    result = runner.invoke(cli.app, ["leaks", "--humans"])
    assert result.exit_code == 0 and "0 leaks across 2 people" in result.output


def test_the_boundary_check_can_fail(
    pg_db: PgDb, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Negative control: Ostrel's narrator names Varda's secret."""
    game = _duo(pg_db, monkeypatch, tmp_path, EchoClaude(leak_to=OSTREL, leak=VARDA_OWN))
    people = People(game)
    assert "Canary leak" in people.command("advance", HOST)
    assert VARDA_OWN not in "\n".join(t for _, t in people.seen()[FRIEND])  # the guard held
    holdings = _holdings(game)
    store = game.store()
    state = load_state(store, game.campaign())
    log_leaks = scan_humans(
        state, holdings, surfaces_from_log(store.read(game.campaign()), holdings)
    )
    store.close()
    assert [(x.human, x.canary) for x in log_leaks] == [(FRIEND, VARDA_OWN)]
    result = runner.invoke(cli.app, ["leaks", "--humans"])
    assert result.exit_code == cli.EXIT_LEAK and f"human={FRIEND}" in result.output


def test_the_cli_will_not_play_another_persons_seat(
    pg_db: PgDb, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _duo(pg_db, monkeypatch, tmp_path, EchoClaude())
    for command in (["status"], ["briefing"], ["order", ORDER]):
        result = runner.invoke(cli.app, [*command, "--seat", OSTREL])
        assert result.exit_code == cli.EXIT_REFUSED and "someone else" in result.output
    assert runner.invoke(cli.app, ["status", "--seat", VARDA]).exit_code == 0


# --- the same check with no database ---------------------------------------------


class EchoNarrator:
    def __init__(self, leak: tuple[SeatId, str] | None = None) -> None:
        self.leak = leak

    def narrate(self, view: View, deltas: Sequence[d.Delta]) -> Narration:
        text = view.model_dump_json()
        if self.leak and view.viewer == self.leak[0]:
            text += self.leak[1]
        return Narration(text=text)


def _memory_run(narrator: EchoNarrator) -> tuple[MemoryStore, dict[str, frozenset[SeatId]]]:
    store = MemoryStore()
    store.append_all(fx.creation_events())
    for n in range(5):
        for k, seat in enumerate((fx.NORTH, fx.SOUTH)):
            intent = Intent(
                intent_id=IntentId(f"01ARZ3NDEKTSV4RRFFQ69H{10 * n + k:04d}"),
                actor=seat,
                verb=Verb.WAIT,
                scope=private_to(seat),
                filed_tick=n,
            )
            assert file_intent(store, fx.CAMPAIGN, intent) is None
        providers = Providers(ScriptedResolver(), narrator, RecordingDeliverer())
        run_tick(store, fx.CAMPAIGN, providers)
    return store, {"a": frozenset({fx.NORTH}), "b": frozenset({fx.SOUTH})}


def test_the_human_boundary_holds_without_a_database() -> None:
    store, holdings = _memory_run(EchoNarrator())
    state = replay(store.read(fx.CAMPAIGN))
    assert scan_humans(state, holdings, surfaces_from_log(store.read(fx.CAMPAIGN), holdings)) == []
    hidden_from_b = canaries_hidden_from_human(state, holdings["b"])
    assert hidden_from_b  # the fixture has a secret South must not learn
    leak = next(iter(hidden_from_b))
    store, holdings = _memory_run(EchoNarrator(leak=(fx.SOUTH, leak)))
    state = replay(store.read(fx.CAMPAIGN))
    found = scan_humans(state, holdings, surfaces_from_log(store.read(fx.CAMPAIGN), holdings))
    assert {(x.human, x.canary) for x in found} == {("b", leak)}
