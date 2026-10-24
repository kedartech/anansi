"""The web client's backend (web client MVP): accounts, web-held seats and the JSON API.

A real server on a free port, driven over HTTP with urllib. The host side runs on
the Discord fakes (``/establish``, ``/invite``, ``/begin``), so one game is held
from both clients at once. No model is called (``Table``, ``FakeClaude``).
"""

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import psycopg
import pytest
from typer.testing import CliRunner

from revisionist import cli
from revisionist.core.ids import SeatId
from revisionist.engine import host_ops
from revisionist.engine.discord.handlers import ALREADY_SEATED, BAD_CODE, PICK_FIRST
from revisionist.engine.discord.tables import LinkError, SeatLinks
from revisionist.engine.store import load_state
from revisionist.engine.web import accounts as acc
from revisionist.engine.web import snapshot
from revisionist.engine.web.handlers import JOINED_ZERO, NOT_LINKED
from revisionist.engine.web.server import COOKIE, App, make_handler
from tests.pg import PgDb
from tests.test_discord_bot import GUILD, HOST, Game
from tests.test_discord_duo import FRIEND
from tests.test_session_zero import FLORENCE
from tests.test_session_zero_steps import PERSONA, Table

runner = CliRunner()
PASSWORD = "a long enough passphrase"


class Client:
    """One browser: a cookie jar and same-origin JSON calls."""

    def __init__(self, base: str) -> None:
        self.base = base
        self.cookie = ""
        self.last_set_cookie = ""

    def call(
        self,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data)
        if data is not None:
            req.add_header("Content-Type", "application/json")
            req.add_header("Origin", self.base)
        if self.cookie:
            req.add_header("Cookie", f"{COOKIE}={self.cookie}")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                status, raw, hdrs = resp.status, resp.read(), resp.headers
        except urllib.error.HTTPError as exc:
            status, raw, hdrs = exc.code, exc.read(), exc.headers
        set_cookie = hdrs.get("Set-Cookie") or ""
        if set_cookie:
            self.last_set_cookie = set_cookie
            self.cookie = set_cookie.split(";", 1)[0].split("=", 1)[1]
        kind = hdrs.get("Content-Type", "")
        return status, json.loads(raw) if kind.startswith("application/json") else raw

    def seat(self, action: str, **body: Any) -> str:
        status, out = self.call(f"/api/seat/{action}", body)
        assert status == 200, out
        return str(out["reply"])

    def me(self) -> dict[str, Any]:
        status, out = self.call("/api/me")
        assert status == 200, out
        return dict(out)


class Web:
    def __init__(self, pg_db: PgDb, tmp_path: Path, table: Any) -> None:
        self.game = Game(pg_db, table, tmp_path / "briefings")
        static = tmp_path / "static"
        static.mkdir()
        (static / "index.html").write_text("<!doctype html><title>Revisionist</title>")
        (static / "app.js").write_text("export {};")
        (tmp_path / "secret.txt").write_text("not for you")
        self.app = App(
            pg_db.app_dsn,
            static,
            transport_factory=lambda: self.game.transport,
            clock=self.game.clock,
            briefings=tmp_path / "briefings",
            offline_prose=True,
        )
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.db = pg_db

    def client(self) -> Client:
        return Client(self.base)

    def signed_up(self, handle: str) -> tuple[Client, list[str]]:
        c = self.client()
        status, out = c.call("/api/signup", {"handle": handle, "password": PASSWORD})
        assert status == 201, out
        return c, list(out["codes"])

    def invite(self) -> str:
        reply = self.game.command("invite", HOST)
        assert "a new player" in reply or "invite" in reply.lower(), reply
        return reply.splitlines()[1].strip()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def web(pg_db: PgDb, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Web]:
    monkeypatch.setenv("REVISIONIST_DB", pg_db.app_dsn)
    monkeypatch.setenv("REVISIONIST_DISCORD_GUILD_ID", GUILD)
    w = Web(pg_db, tmp_path, Table())
    monkeypatch.setattr(cli, "clock", w.game.clock)
    yield w
    w.close()


def _florence(web: Web) -> None:
    assert "Session Zero is open" in web.game.establish(HOST, FLORENCE)


# --- accounts -------------------------------------------------------------------


def test_passwords_are_scrypt_and_codes_are_hashed(web: Web) -> None:
    c, codes = web.signed_up("lorenzo")
    assert len(codes) == acc.CODES and len(set(codes)) == acc.CODES
    with psycopg.connect(web.db.app_dsn) as conn:
        (stored,) = conn.execute("SELECT password_hash FROM web_accounts").fetchone()  # type: ignore[misc]
        hashes = [r[0] for r in conn.execute("SELECT code_hash FROM web_recovery_codes")]
        tokens = [r[0] for r in conn.execute("SELECT token_hash FROM web_sessions")]
    assert stored.startswith("scrypt$") and PASSWORD not in stored
    assert not set(codes) & set(hashes)
    assert c.cookie and c.cookie not in tokens


def test_the_session_cookie_is_httponly_and_strict(web: Web) -> None:
    c, _ = web.signed_up("lorenzo")
    assert "HttpOnly" in c.last_set_cookie and "SameSite=Strict" in c.last_set_cookie


def test_signup_rules(web: Web) -> None:
    c = web.client()
    assert c.call("/api/signup", {"handle": "x", "password": PASSWORD})[0] == 400
    assert c.call("/api/signup", {"handle": "lorenzo", "password": "short"})[0] == 400
    web.signed_up("Lorenzo")
    status, out = c.call("/api/signup", {"handle": "lorenzo", "password": PASSWORD})
    assert status == 400 and out["error"] == "That handle is taken."


def test_sign_in_out_and_a_wrong_password(web: Web) -> None:
    web.signed_up("lorenzo")
    c = web.client()
    assert c.call("/api/signin", {"handle": "LORENZO", "password": "wrong password!!"})[0] == 401
    assert c.me() == {"account": None}
    assert c.call("/api/signin", {"handle": "LORENZO", "password": PASSWORD})[0] == 200
    assert c.me()["account"]["handle"] == "lorenzo"
    assert c.call("/api/signout", {})[0] == 200
    assert c.me() == {"account": None}


def test_sign_in_is_throttled_per_handle(web: Web) -> None:
    web.signed_up("lorenzo")
    c = web.client()
    for _ in range(10):
        c.call("/api/signin", {"handle": "lorenzo", "password": "wrong password!!"})
    status, _ = c.call("/api/signin", {"handle": "lorenzo", "password": PASSWORD})
    assert status == 429


def test_a_recovery_code_works_once_and_ends_every_session(web: Web) -> None:
    old, codes = web.signed_up("lorenzo")
    c = web.client()
    new = "another long passphrase"
    status, out = c.call(
        "/api/recover", {"handle": "lorenzo", "code": codes[0].lower(), "password": new}
    )
    assert status == 200 and len(out["codes"]) == acc.CODES
    assert old.me() == {"account": None}  # the old browser is signed out
    assert c.me()["account"]["codes_left"] == acc.CODES
    again = web.client().call(
        "/api/recover", {"handle": "lorenzo", "code": codes[0], "password": new}
    )
    assert again[0] == 401
    assert web.client().call("/api/signin", {"handle": "lorenzo", "password": new})[0] == 200


# --- the request guard --------------------------------------------------------------


def test_writes_need_json_from_this_origin(web: Web) -> None:
    c, _ = web.signed_up("lorenzo")
    status, _ = c.call("/api/seat/status", {}, headers={"Origin": "http://evil.example"})
    assert status == 403
    req = urllib.request.Request(web.base + "/api/signout", data=b"x=1")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(req, timeout=10)
    assert caught.value.code == 415


def test_seat_calls_need_a_session(web: Web) -> None:
    status, out = web.client().call("/api/seat/status", {})
    assert status == 401 and out["error"] == "Sign in first."


def test_static_files_stay_inside_the_client_folder(web: Web) -> None:
    c = web.client()
    status, body = c.call("/app.js")
    assert status == 200 and body == b"export {};"
    for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/..%2fsecret.txt"):
        status, body = c.call(path)
        assert b"not for you" not in (body if isinstance(body, bytes) else b"")
    status, body = c.call("/play/war-room")  # a client route gets the shell
    assert status == 200 and b"Revisionist" in body


def test_every_response_carries_the_csp(web: Web) -> None:
    with urllib.request.urlopen(web.base + "/", timeout=10) as resp:
        csp = resp.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp


# --- web seats --------------------------------------------------------------------


def test_web_session_zero_to_play_beside_a_discord_player(web: Web) -> None:
    """One game, two clients: a web player and a Discord player, to the first tick."""
    _florence(web)
    lorenzo, _ = web.signed_up("lorenzo")
    assert lorenzo.me()["game"] is None
    assert lorenzo.seat("status") == NOT_LINKED
    assert lorenzo.seat("join", code=web.invite()) == JOINED_ZERO
    assert lorenzo.seat("join", code=web.invite()) == ALREADY_SEATED
    assert web.game.command("join", FRIEND, code=web.invite()).startswith("You are in")

    me = lorenzo.me()["game"]
    assert me["phase"] == "session_zero" and me["view"]["seat"] is None
    assert any(c["name"] == "Lorenzo de' Medici" for c in me["view"]["roster"])
    assert lorenzo.seat("order", text="Raise the city.") == PICK_FIRST

    assert lorenzo.seat("claim", name="Lorenzo de' Medici") == "You play Lorenzo de' Medici."
    web.game.command("character", FRIEND, action="claim", text="Giuliano de' Medici")
    offered = lorenzo.seat("goal", route="history")
    assert "**H2** Hold the Gonfalonier" in offered
    offers = lorenzo.me()["game"]["view"]["session_zero"]["offers"]
    assert any(line.startswith("H2. Hold the Gonfalonier") for line in offers["history"])
    assert "Your goal is fixed" in lorenzo.seat("confirm", label="H2")
    assert "suggest" in lorenzo.seat("history").lower()
    assert "now serves" in lorenzo.seat("agent", name="Ser Agnolo", role="chancellor")
    steps = lorenzo.me()["game"]["view"]["session_zero"]
    assert steps == {"character": True, "goal": True, "agent": True, "brief": True, "offers": {}}

    web.game.command("goal", FRIEND, action="history")
    web.game.command("goal", FRIEND, action="confirm", text="2")
    web.game.command("history", FRIEND)
    web.game.handlers.agent(FRIEND, PERSONA)
    assert web.game.command("begin", HOST).startswith("Session Zero is closed. 2 player(s)")

    game = lorenzo.me()["game"]
    assert game["phase"] == "play"
    assert game["view"]["seat"]["name"] == "Lorenzo de' Medici"
    assert game["view"]["seat"]["goal"]["text"].startswith("Hold the Gonfalonier")
    assert "Lorenzo" in lorenzo.seat("status")
    assert lorenzo.seat("dispatch").startswith("Tick 0:\nAs recorded:")  # the brief, as on Discord
    assert game["view"]["brief"].startswith("As recorded:")
    assert game["view"]["dispatches"] == []  # tick 0 is the brief, not a dispatch


def test_a_seat_is_held_from_one_client_only(web: Web) -> None:
    _florence(web)
    store = web.game.store()
    try:
        c = web.game.campaign()
        seat = SeatId("01ARZ3NDEKTSV4RRFFQ69PZ001")
        with store.conn.transaction():
            store.conn.execute(
                "INSERT INTO web_accounts (account_id, handle, password_hash)"
                " VALUES ('A1', 'someone', 'x')"
            )
        acc.WebSeats(store.conn).link(c, seat, "A1")
        with pytest.raises(LinkError):
            SeatLinks(store.conn).link(c, seat, FRIEND, GUILD)
        other = SeatId("01ARZ3NDEKTSV4RRFFQ69PZ002")
        SeatLinks(store.conn).link(c, other, FRIEND, GUILD)
        with pytest.raises(LinkError):
            acc.WebSeats(store.conn).link(c, other, "A1")
    finally:
        store.close()


def test_a_spent_code_is_spent_on_both_clients(web: Web) -> None:
    _florence(web)
    code = web.invite()
    lorenzo, _ = web.signed_up("lorenzo")
    assert lorenzo.seat("join", code=code) == JOINED_ZERO
    assert web.game.command("join", FRIEND, code=code) == BAD_CODE


def test_the_leak_scan_counts_web_players_as_people(web: Web) -> None:
    _florence(web)
    lorenzo, _ = web.signed_up("lorenzo")
    lorenzo.seat("join", code=web.invite())
    lorenzo.seat("claim", name="Lorenzo de' Medici")
    store = web.game.store()
    try:
        c = web.game.campaign()
        state = load_state(store, c)
        held = host_ops.holdings(store.conn, state, HOST)
        web_people = {k: v for k, v in held.items() if k.startswith(acc.WEB_PREFIX)}
        [seats] = web_people.values()
        [lorenzo_seat] = [s for s in state.seats.values() if s.name == "Lorenzo de' Medici"]
        assert seats == frozenset({lorenzo_seat.seat_id})
        assert lorenzo_seat.seat_id not in held[HOST]  # the host cannot read it either
    finally:
        store.close()


def test_retire_releases_web_seats(web: Web) -> None:
    _florence(web)
    lorenzo, _ = web.signed_up("lorenzo")
    lorenzo.seat("join", code=web.invite())
    c = web.game.campaign()
    assert web.game.command("retire", HOST, campaign=c).startswith("Retired")
    with psycopg.connect(web.db.app_dsn) as conn:
        assert conn.execute("SELECT count(*) FROM web_seats").fetchone() == (0,)
    assert lorenzo.me()["game"] is None


def test_the_cli_will_not_play_a_web_held_seat(web: Web) -> None:
    _florence(web)
    lorenzo, _ = web.signed_up("lorenzo")
    lorenzo.seat("join", code=web.invite())
    lorenzo.seat("claim", name="Lorenzo de' Medici")
    result = runner.invoke(cli.app, ["status", "--seat", "Lorenzo de' Medici"])
    assert result.exit_code == 2 and "someone else" in result.output


# --- the snapshot's rules -------------------------------------------------------------


def test_the_snapshot_never_says_truth_or_who_runs_a_seat(web: Web) -> None:
    _florence(web)
    lorenzo, _ = web.signed_up("lorenzo")
    lorenzo.seat("join", code=web.invite())
    lorenzo.seat("claim", name="Lorenzo de' Medici")
    raw = json.dumps(lorenzo.me())
    for word in ('"truth"', '"controller"', '"human"', '"canary"', '"deputy"', '"persona"'):
        assert word not in raw, word


def test_odds_are_only_ever_a_band() -> None:
    assert snapshot.BANDS == ("impossible", "long_shot", "contested", "likely", "assured")
    with pytest.raises(ValueError):
        snapshot._band("0.62")


def test_a_snapshot_naming_a_hidden_canary_is_withheld(
    web: Web, monkeypatch: pytest.MonkeyPatch
) -> None:
    _florence(web)
    lorenzo, _ = web.signed_up("lorenzo")
    lorenzo.seat("join", code=web.invite())
    lorenzo.seat("claim", name="Lorenzo de' Medici")
    real = snapshot.seat_snapshot

    def leaky(store: Any, state: Any, seat: Any) -> dict[str, Any]:
        out = real(store, state, seat)
        out["leak"] = "CANARY-PLANTED"
        return out

    monkeypatch.setattr(snapshot, "seat_snapshot", leaky)
    monkeypatch.setattr(
        snapshot, "canaries_hidden_from", lambda state, seat: {"CANARY-PLANTED": "F1"}
    )
    out = lorenzo.me()
    assert "CANARY-PLANTED" not in json.dumps(out)
    assert out["error"].startswith("That reply was withheld")
