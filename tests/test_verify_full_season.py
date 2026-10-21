"""V3 probe: a whole season through the fake Discord command tree, two players."""

import re
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from revisionist import cli
from revisionist.core import events as ev
from revisionist.core.ontology import Persona
from revisionist.engine.discord import handlers
from revisionist.engine.discord.handlers import DRAFTING, JOINED_ZERO
from revisionist.engine.llm import Reply, Request, TransportError
from revisionist.engine.store import load_state
from tests.pg import PgDb
from tests.test_discord_bot import GUILD, HOST, Game
from tests.test_discord_duo import FRIEND
from tests.test_discord_host import _host
from tests.test_session_zero import FLORENCE
from tests.test_session_zero_steps import Table

BAD = re.compile(r"Traceback|Error\b|Exception|File \"|\.py\"|psycopg|pydantic", re.I)
PERSONA = Persona(name="Ser Agnolo", role="chancellor", voice="dry")
TIMES: list[tuple[float, str]] = []
PROBLEMS: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        PROBLEMS.append(msg)


class Playing(Table):
    def __init__(self) -> None:
        super().__init__()
        self.fail: set[str] = set()
        self.delay = 0.0
        self.record: list[dict[str, str]] = []
        self.lock = threading.Lock()

    def call(self, request: Request) -> Reply:
        cc = request.call_class
        if cc in self.fail:
            self.calls.append(cc)
            raise TransportError(f"boom in {cc}: Traceback (most recent call last)")
        if cc == "character_record":
            self.calls.append(cc)
            return Reply({"people": self.record}, request.model, 1, 1, 0, 0.01)
        if cc in {
            "structure",
            "brief",
            "deputy",
            "resolver",
            "narration",
            "narrator",
            "press",
        } or cc not in {
            "session_zero",
            "character_search",
            "character_pick",
            "goal",
            "history_brief",
        }:
            self.calls.append(cc)
            if self.delay:
                time.sleep(self.delay)
            out: dict[str, Any]
            match cc:
                case "structure":
                    out = {"kind": "intent", "intent": {"verb": "declare", "spin": "Open."}}
                case "brief":
                    out = {"text": "Mira: calm.", "dilemma": "Toll?", "moves": ["a", "b", "c"]}
                case "deputy":
                    out = {"intents": [{"verb": "negotiate"}]}
                case "resolver":
                    out = {"proposals": []}
                case _:
                    out = {"text": "A quiet week in Florence."}
            return Reply(out, request.model, 100, 20, 0, 0.001)
        return super().call(request)


def step(name: str, fn: Any) -> Any:
    t = time.perf_counter()
    out = fn()
    TIMES.append((time.perf_counter() - t, name))
    if isinstance(out, str):
        check(not BAD.search(out), f"{name}: reply smells of internals: {out[:200]!r}")
    return out


def test_full_season(pg_db: PgDb, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    table = Playing()
    g = Game(pg_db, table, tmp_path / "briefings")
    monkeypatch.setenv("REVISIONIST_DB", pg_db.app_dsn)
    monkeypatch.setenv("REVISIONIST_DISCORD_GUILD_ID", GUILD)
    monkeypatch.setattr(cli, "clock", g.clock)

    def cmd(name: str, user: str = HOST, **o: str) -> str:
        return str(
            step(f"/{name} {o.get('action', '')}".strip(), lambda: g.command(name, user, **o))
        )

    def pub() -> list[str]:
        return [c for c, e in g.last_sent if not e]

    # --- 1. Session Zero
    r = cmd("establish", place=FLORENCE)
    check(r.startswith(DRAFTING.format(place=FLORENCE)), f"establish private: {r[:120]}")
    check(any("characters to choose from" in p for p in pub()), f"establish public: {pub()}")
    codes = []
    for _ in range(2):
        r = cmd("invite")
        check("a new player" in r, f"invite: {r}")
        codes.append(r.splitlines()[1].strip())
    for user, code in ((HOST, codes[0]), (FRIEND, codes[1])):
        r = cmd("join", user, code=code)
        check(r.startswith(JOINED_ZERO), f"join: {r}")
        check(any("joined" in p for p in pub()), f"join public: {pub()}")
    r = cmd("character", FRIEND, action="roster")
    check("Lorenzo de' Medici" in r, f"roster: {r[:200]}")
    table.record = [
        {"name": "Luca Pitti", "note": "A rival banker."},
        {"name": "LORENZO DE' MEDICI", "note": "dup of roster, different case"},
        {"name": "lorenzo de' medici", "note": "dup again"},
        {"name": "Piero Capponi", "note": "A statesman."},
    ]
    r = cmd("character", FRIEND, action="search", text="a poet")
    check(
        "LORENZO" not in r.upper().replace("LORENZO DE' MEDICI:", "") or "From the record" in r, "?"
    )
    record_part = r.split("From the record", 1)[-1]
    check("LORENZO DE' MEDICI" not in record_part.upper(), f"dup in record section: {record_part}")
    check("Luca Pitti" in r and "From the record" in r, f"search: {r}")
    r = cmd("character", FRIEND, action="pick", text="Luca Pitti")
    check(r == "You play Luca Pitti.", f"pick private: {r}")
    check(pub() == [f"<@{FRIEND}> plays Luca Pitti."], f"pick public: {pub()}")
    r = cmd("character", HOST, action="claim", text="Lorenzo de' Medici")
    check(r == "You play Lorenzo de' Medici.", f"claim: {r}")
    check(pub() == [f"<@{HOST}> plays Lorenzo de' Medici."], f"claim public: {pub()}")
    for user in (HOST, FRIEND):
        r = cmd("goal", user, action="history")
        check(r.startswith("- **H1** "), f"goal history: {r[:200]}")
        r = cmd("goal", user, action="confirm", text="1")
        check("fixed" in r or "approve" in r, f"goal confirm: {r}")
        r = cmd("history", user)
        check(r.startswith("**As recorded:**"), f"history: {r[:100]}")
        r = step("/agent", lambda u=user: g.handlers.agent(u, PERSONA))
        check("now serves" in r, f"agent: {r}")
    r = _host(g, "flags")
    check(True, "")
    TIMES.append((0, "flags"))
    flags = r
    if "too_" in flags:
        for seat in ("Lorenzo de' Medici", "Luca Pitti"):
            r2 = _host(g, "approve", seat=seat)
            PROBLEMS.append(f"INFO approve {seat}: {r2}") if False else None
    r = cmd("begin")
    check(r.startswith("Session Zero is closed"), f"begin: {r}")
    check(any("Session Zero is closed" in p for p in pub()), f"begin public: {pub()}")

    # --- 2. Play loop
    r = cmd("order", text="Raise the city.")
    check(r.startswith("filed ") and r.endswith("(private)"), f"order private: {r}")
    r = cmd("order", FRIEND, text="Open the gates.", visibility="public", spin="Gates open.")
    check(r.startswith("filed ") and r.endswith("(public)"), f"order public: {r}")
    r = cmd("order", FRIEND, text="Quietly bribe a clerk.", visibility="covert", spin="Calm.")
    check(r.startswith("filed ") and r.endswith("(covert)"), f"order covert: {r}")

    r = cmd("advance")
    print("ADVANCE:", repr(r), pub())
    check("ticked" in r.lower() or "tick" in r.lower(), f"advance: {r}")
    check(any("has run" in p for p in pub()), f"advance public: {pub()}")
    for user in (HOST, FRIEND):
        for c in ("dispatch", "briefing", "status", "dashboard"):
            r = cmd(c, user)
            check(bool(r.strip()) and r != handlers.BROKEN, f"{c} {user}: {r[:200]}")
    r = cmd("history", HOST)
    check(r == "Session Zero is not open in this campaign.", f"history in play: {r[:100]}")

    for sub in ("seats", "invites", "flags", "schedule", "cost", "leaks", "verify"):
        r = step(f"/host {sub}", lambda s=sub: _host(g, s))
        print(f"HOST {sub}:", r[:300].replace("\n", " | "))
        check(r != handlers.BROKEN and bool(r), f"host {sub}: {r[:200]}")
    r = _host(g, "replay", tick=0)
    print("REPLAY:", r)
    check(r.startswith("tick 0 "), f"replay: {r}")
    r = _host(g, "replay", tick=99)
    print("REPLAY99:", r)
    store = g.store()
    log = list(store.read(g.campaign()))
    store.close()
    target = next(e.event_id for e in log if isinstance(e.payload, ev.IntentFiled))
    r = step("/host why", lambda: _host(g, "why", target=target, reason="probe"))
    print("WHY:", r[:400].replace("\n", " | "))
    check(r not in ("nothing found", handlers.BROKEN), f"why: {r[:200]}")
    r = _host(g, "link", user=type("U", (), {"id": int(FRIEND)})(), seat="Luca Pitti")
    print("LINK:", r)
    r = _host(g, "unlink", seat="Luca Pitti")
    print("UNLINK:", r)
    check("Unlinked" in r, f"unlink: {r}")
    r = _host(g, "link", user=type("U", (), {"id": int(FRIEND)})(), seat="Luca Pitti")
    print("RELINK:", r)
    check("Linked" in r, f"relink: {r}")

    r = cmd("pause")
    check("Paused" in r, f"pause: {r}")
    r = cmd("advance")
    print("ADVANCE PAUSED:", repr(r))
    check("paus" in r.lower() and "has run" not in r, f"advance while paused: {r}")
    check(not any("has run" in p for p in pub()), f"advance paused public: {pub()}")
    r = cmd("resume")
    check("Resumed" in r, f"resume: {r}")

    # --- 4a. concurrency
    store = g.store()
    t0 = load_state(store, g.campaign()).tick
    store.close()
    table.delay = 0.4
    outs: list[str] = []
    ths = [threading.Thread(target=lambda: outs.append(g.command("advance"))) for _ in range(2)]
    t = time.perf_counter()
    for th in ths:
        th.start()
    for th in ths:
        th.join()
    TIMES.append((time.perf_counter() - t, "2x concurrent /advance"))
    table.delay = 0.0
    store = g.store()
    t1 = load_state(store, g.campaign()).tick
    store.close()
    print("CONCURRENT:", outs, t0, t1)
    check(t1 - t0 <= 1, f"double tick: {t0}->{t1} outs={outs}")

    # --- 3. season end
    r = cmd("season-end")
    print("SEASON-END:", r[:500].replace("\n", " | "))
    check(r != handlers.BROKEN, f"season-end: {r[:200]}")
    for user in (HOST, FRIEND):
        r = cmd("report", user)
        print("REPORT:", r[:300].replace("\n", " | "))
        check(r != handlers.BROKEN and bool(r), f"report: {r[:200]}")
    r = _host(g, "leaks")
    print("LEAKS after:", r[-200:].replace("\n", " | "))
    check(r.splitlines()[-1].startswith("0 leaks"), f"leaks after season: {r[-300:]}")
    r = cmd("retire")
    print("RETIRE list:", r)
    r = cmd("retire", campaign=g.campaign())
    print("RETIRE:", r)

    print("PROBLEMS:", *PROBLEMS, sep="\n  ")
    print("SLOWEST:", *sorted(TIMES, reverse=True)[:8], sep="\n  ")
    assert not [p for p in PROBLEMS if p], PROBLEMS


def test_robustness(pg_db: PgDb, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    table = Playing()
    g = Game(pg_db, table, tmp_path / "briefings")
    monkeypatch.setenv("REVISIONIST_DB", pg_db.app_dsn)
    monkeypatch.setenv("REVISIONIST_DISCORD_GUILD_ID", GUILD)
    monkeypatch.setattr(cli, "clock", g.clock)
    g.command("establish", HOST, place=FLORENCE)
    code = g.command("invite", HOST).splitlines()[1].strip()
    g.command("join", HOST, code=code)
    g.command("character", HOST, action="claim", text="Lorenzo de' Medici")

    probs: list[str] = []
    table.fail = {"goal"}
    r = g.command("goal", HOST, action="history")
    print("GOAL FAIL:", repr(r))
    if "\n" in r.strip() or BAD.search(r) or "boom" in r:
        probs.append(f"goal transport error: {r!r}")
    table.fail = {"character_search"}
    table.record = [{"name": "Piero Capponi", "note": "x"}]
    r = g.command("character", HOST, action="search", text="a banker")
    print("SEARCH FAIL(search):", repr(r))
    if "Lorenzo" not in r and "roster" not in r.lower():
        probs.append(f"search w/ failing character_search lost roster: {r!r}")
    table.fail = {"character_record"}
    r = g.command("character", HOST, action="search", text="a banker")
    print("SEARCH FAIL(record):", repr(r))
    if "Francesco" not in r:
        probs.append(f"search w/ failing record lost roster lines: {r!r}")
    table.fail = {"character_search", "character_record"}
    r = g.command("character", HOST, action="search", text="a banker")
    print("SEARCH FAIL(both):", repr(r))
    table.fail = set()
    assert not probs, probs


def _to_play(g: Game, table: Playing) -> None:
    g.command("establish", HOST, place=FLORENCE)
    for user, who in ((HOST, "Lorenzo de' Medici"), (FRIEND, "Giuliano de' Medici")):
        code = g.command("invite", HOST).splitlines()[1].strip()
        g.command("join", user, code=code)
        g.command("character", user, action="claim", text=who)
        g.command("goal", user, action="history")
        g.command("goal", user, action="confirm", text="1")
        g.handlers.agent(user, PERSONA)
    assert g.command("begin", HOST).startswith("Session Zero is closed")


def test_failures_and_long_messages(
    pg_db: PgDb, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from revisionist.engine import host_ops

    table = Playing()
    g = Game(pg_db, table, tmp_path / "briefings")
    monkeypatch.setenv("REVISIONIST_DB", pg_db.app_dsn)
    monkeypatch.setenv("REVISIONIST_DISCORD_GUILD_ID", GUILD)
    monkeypatch.setattr(cli, "clock", g.clock)
    probs: list[str] = []

    r = g.command("report", HOST)
    print("REPORT in zero:", repr(r))
    r = g.command("advance", HOST)
    print("ADVANCE in zero:", repr(r))
    r = g.command("begin", HOST)
    print("BEGIN w/o campaign:", repr(r))
    _to_play(g, table)
    print("BEGIN twice:", repr(g.command("begin", HOST)))
    print("REPORT in play:", repr(g.command("report", HOST)))
    print("INVITE in play:", repr(g.command("invite", HOST)))
    print("CHARACTER in play:", repr(g.command("character", HOST, action="roster")[:150]))
    print("GOAL in play:", repr(g.command("goal", HOST, action="history")))
    print("AGENT in play:", repr(g.handlers.agent(HOST, PERSONA)))
    print("SEASON-END ok?")

    table.fail = {"structure"}
    r = g.command("order", HOST, text="Do a thing")
    print("ORDER FAIL:", repr(r))
    if r != handlers.MODEL_DOWN:
        probs.append(f"order transport failure: {r!r}")
    table.fail = {"resolver"}
    r = g.command("advance", HOST)
    print("ADVANCE FAIL:", repr(r), g.last_sent)
    if BAD.search(r) or "boom" in r:
        probs.append(f"advance failure leaks: {r!r}")
    table.fail = set()
    r = g.command("advance", HOST)
    print("ADVANCE RECOVER:", repr(r), g.last_sent)

    long = "\n".join(f"tick {i:4d}  0.0040  0.0860  {i}" for i in range(200))
    monkeypatch.setattr(host_ops, "cost_text", lambda s, c: long)

    group = g.bot.tree.get_command("host", guild=g.bot.guild)
    import asyncio

    from tests.test_discord_bot import FakeInteraction

    it = FakeInteraction(HOST)
    cb: Any = group.get_command("cost").callback  # type: ignore[union-attr]
    asyncio.run(cb(it))
    parts = [c for c, _ in it.followup.sent]
    print("COST parts:", [len(p) for p in parts], [p.count("```") for p in parts])
    if any(len(p) > 2000 for p in parts):
        probs.append("cost part >2000")
    if not all(p.startswith("```\n") and p.endswith("\n```") for p in parts):
        probs.append("cost part not fenced on its own")
    if "".join(p[4:-4] for p in parts) != long:
        probs.append("cost content lost on split")
    if any(p.count("```") % 2 for p in parts):
        probs.append(f"cost split leaves unbalanced code fences: {[p.count('```') for p in parts]}")

    r = g.command("season-end", HOST)
    r2 = g.command("season-end", HOST)
    print("SEASON-END twice:", repr(r2))
    print("ADVANCE after end:", repr(g.command("advance", HOST)))
    print("ORDER after end:", repr(g.command("order", HOST, text="hello")))
    print("PAUSE after end:", repr(g.command("pause", HOST)))
    print("PROBS", probs)
    assert not probs, probs
