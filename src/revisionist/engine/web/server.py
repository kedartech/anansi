"""The web client's HTTP server: the static client in ``web/`` and a JSON API.

Stdlib ``http.server``, one thread per request; every request opens its own store
connection, as the Discord bot's calls do. Bind it to a private address (the
tailnet): it speaks plain HTTP.

Every seat command goes through ``WebHandlers``, the Discord bot's own command
code, so the web and Discord replies cannot drift. Reads go through
``snapshot.seat_snapshot``, which is built from the seat's own view and scanned
for hidden canaries before it is sent.
"""

import json
import logging
import mimetypes
import threading
import time
from collections.abc import Callable
from datetime import date, datetime
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from revisionist.core.ids import DocumentId, OrderId, SeatId
from revisionist.core.ontology import Persona, Phase
from revisionist.core.project import project
from revisionist.core.realism.pipeline import OrderStatus, StepAction
from revisionist.core.state import State
from revisionist.engine import campaign as cmp
from revisionist.engine import realism_tick
from revisionist.engine import scheduler as sch
from revisionist.engine.discord import handlers as dh
from revisionist.engine.llm import Transport
from revisionist.engine.pg_store import PostgresEventStore
from revisionist.engine.prose import ClaudeProseWriter, ProseWriter, TemplateProseWriter
from revisionist.engine.store import load_state
from revisionist.engine.tick import NotInPlayError
from revisionist.engine.web import realism_snapshot, snapshot
from revisionist.engine.web.accounts import (
    SESSION_DAYS,
    Account,
    AccountError,
    Accounts,
)
from revisionist.engine.web.handlers import WebHandlers

log = logging.getLogger("revisionist.web")

COOKIE = "rv_session"
BODY_MAX = 64 * 1024
SIGNIN_TRIES = 10
"""Failed sign-ins (or recoveries) per handle per 15 minutes."""

CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
    "form-action 'self'"
)


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class App:
    """What every request needs: the database, the model transport, a clock."""

    def __init__(
        self,
        dsn: str,
        static: Path,
        *,
        transport_factory: Callable[[], Transport],
        clock: Callable[[], datetime],
        briefings: Path,
        secure_cookie: bool = False,
        offline_prose: bool = False,
    ) -> None:
        self.dsn = dsn
        self.static = static.resolve()
        self.clock = clock
        self.secure_cookie = secure_cookie
        self.offline_prose = offline_prose
        self.transport_factory = transport_factory
        self.handlers = WebHandlers(
            dsn, transport_factory=transport_factory, clock=clock, briefings=briefings
        )
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def open(self) -> PostgresEventStore:
        store = cmp.open_store(self.dsn)
        assert isinstance(store, PostgresEventStore)
        return store

    # --- sign-in throttle ------------------------------------------------------

    def throttled(self, handle: str) -> bool:
        key = handle.strip().casefold()
        now = time.monotonic()
        with self._lock:
            recent = [t for t in self._failures.get(key, []) if now - t < 900]
            self._failures[key] = recent
            return len(recent) >= SIGNIN_TRIES

    def failed(self, handle: str) -> None:
        with self._lock:
            self._failures.setdefault(handle.strip().casefold(), []).append(time.monotonic())

    # --- reads -----------------------------------------------------------------

    def state_for(self, account: Account) -> dict[str, Any]:
        """The signed-in account's home: its game, its seat and what the seat sees."""
        store = self.open()
        try:
            now = self.clock()
            out: dict[str, Any] = {
                "account": {
                    "handle": account.handle,
                    "codes_left": Accounts(store.conn).codes_left(account.account_id),
                },
                "game": None,
                "live": [],
            }
            live = sch.live_campaigns(store, now)
            out["live"] = [{"id": c, "name": sch.campaign_name(store, c)} for c in live]
            link = self.handlers._game_of(store, account.account_id, "report")
            if isinstance(link, str):
                return out
            state = load_state(store, link.campaign_id)
            game = snapshot.campaign_info(store, state)
            game |= snapshot.schedule_info(store, link.campaign_id, now)
            game["live"] = link.campaign_id in live
            seat_view = snapshot.seat_snapshot(store, state, link.seat_id)
            game["ruleset"] = state.ruleset
            if (
                state.ruleset == "realism"
                and state.phase is Phase.PLAY
                and link.seat_id in state.seats
            ):
                wake_at = None if game["paused"] else game["next_tick_at"]
                log_now = list(store.read(link.campaign_id))
                seat_view["realism"] = realism_snapshot.seat_realism(
                    state,
                    link.seat_id,
                    realism_snapshot.read_ids(log_now, link.seat_id),
                    realism_snapshot.flagged_ids(log_now, link.seat_id),
                    wake_at,
                )
            out["game"] = game | {"view": seat_view}
            return snapshot.checked(state, link.seat_id, out)
        finally:
            store.close()

    # --- realism actions -------------------------------------------------------

    def realism(self, account: Account, action: str, body: dict[str, Any]) -> dict[str, Any]:
        """One ``/api/realism/<action>``. A refusal is a reply, not an error."""
        store = self.open()
        try:
            link = self.handlers._game_of(store, account.account_id, "status")
            if isinstance(link, str):
                raise ApiError(HTTPStatus.BAD_REQUEST, link)
            state = load_state(store, link.campaign_id)
            if state.ruleset != "realism":
                raise ApiError(HTTPStatus.BAD_REQUEST, "This game does not use the realism rules.")
            if state.phase is not Phase.PLAY or link.seat_id not in state.seats:
                raise ApiError(HTTPStatus.BAD_REQUEST, "The game is not in play yet.")
            seat, cid = link.seat_id, link.campaign_id
            try:
                if action == "read":
                    doc_id = DocumentId(_text(body, "doc_id", 64))
                    try:
                        page = realism_snapshot.document(project(state, seat), doc_id)
                    except KeyError:
                        raise ApiError(HTTPStatus.NOT_FOUND, "No such document.") from None
                    realism_tick.mark_read(store, cid, seat, doc_id)
                    flagged = doc_id in realism_snapshot.flagged_ids(store.read(cid), seat)
                    page["item"]["flagged"] = flagged
                    return snapshot.checked(state, seat, dict(page))
                if action == "flag":
                    doc_id = DocumentId(_text(body, "doc_id", 64))
                    if doc_id not in project(state, seat).documents:
                        raise ApiError(HTTPStatus.NOT_FOUND, "No such document.")
                    flag = body.get("flagged")
                    if not isinstance(flag, bool):
                        raise ValueError("flagged is true or false.")
                    realism_tick.set_flag(store, cid, seat, doc_id, flag)
                    return snapshot.checked(state, seat, {"flagged": flag})
                if action == "lever":
                    order = realism_tick.file_order(
                        store, cid, seat, _text(body, "lever", 120), _params(body)
                    )
                    first = state.pipelines[order.pipeline].steps[0]
                    who = state.entities[first.person].name
                    reply = f"Sent to {who}: {_lever_label(state, seat, order.lever)}."
                elif action == "step":
                    try:
                        taken = StepAction(_text(body, "action", 20))
                    except ValueError:
                        names = ", ".join(a.value for a in StepAction)
                        raise ValueError(f"The action is one of {names}.") from None
                    order = realism_tick.take_step(
                        store, cid, OrderId(_text(body, "order_id", 64)), taken, _params(body)
                    )
                    reply = (
                        f"{_lever_label(state, seat, order.lever)}: {_DONE[taken]}. "
                        f"{_STANDING[order.status]}"
                    )
                elif action == "advance":
                    humans = cmp.human_seats(state)
                    if humans != [seat]:
                        raise ValueError("Only the host advances a shared game.")
                    writer: ProseWriter = (
                        TemplateProseWriter()
                        if self.offline_prose
                        else ClaudeProseWriter(self.transport_factory())
                    )
                    result = realism_tick.run_realism_tick(store, cid, prose=writer)
                    for dropped in result.prose_dropped:
                        log.warning("no prose for %s", dropped)
                    today = load_state(store, cid).in_world_date
                    reply = _calendar_line(
                        today,
                        (
                            result.wake.reason.label
                            if result.wake and result.wake.reason.what != "horizon"
                            else None
                        ),
                    )
                else:
                    raise ApiError(HTTPStatus.NOT_FOUND, "No such call.")
            except (ValueError, KeyError, NotInPlayError) as exc:
                text = str(exc.args[0]) if isinstance(exc, KeyError) and exc.args else str(exc)
                return snapshot.checked(state, seat, {"reply": text, "refused": True})
            return snapshot.checked(state, seat, {"reply": reply, "refused": False})
        finally:
            store.close()


_DONE: dict[StepAction, str] = {
    StepAction.PASS: "you passed it on",
    StepAction.EXPEDITE: "you pressed it forward",
    StepAction.AMEND: "you amended it",
    StepAction.STALL: "you held it back",
    StepAction.LEAK: "you let word of it out",
    StepAction.REFUSE: "you refused it",
}
_STANDING: dict[OrderStatus, str] = {
    OrderStatus.MOVING: "It is still moving.",
    OrderStatus.ENACTED: "It has been carried out.",
    OrderStatus.REFUSED: "It has been refused.",
    OrderStatus.LAPSED: "It has lapsed.",
    OrderStatus.WITHDRAWN: "It has been withdrawn.",
}


def _lever_label(state: State, seat: SeatId, name: str) -> str:
    card = state.cards[state.seats[seat].embodies]
    found = next((x for x in card.levers if x.name == name), None)
    return (
        realism_snapshot.lever_label(found) if found is not None else realism_snapshot.words(name)
    )


def _calendar_line(today: date | None, reason: str | None) -> str:
    """What the web says after an advance, in words rather than the CLI's log line."""
    if today is None:
        return "The calendar moves on."
    line = f"The calendar moves to {today:%A} {today.day} {today:%B %Y}."
    return f"{line} Reason: {reason.rstrip('.')}." if reason else line


def _json_body(raw: bytes) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        body = json.loads(raw)
    except ValueError:
        raise ApiError(HTTPStatus.BAD_REQUEST, "That request was not valid JSON.") from None
    if not isinstance(body, dict):
        raise ApiError(HTTPStatus.BAD_REQUEST, "Send a JSON object.")
    return body


def _text(body: dict[str, Any], key: str, limit: int, *, required: bool = True) -> str:
    value = body.get(key, "")
    if not isinstance(value, str):
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{key} must be text.")
    value = value.strip()
    if required and not value:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{key} is empty.")
    if len(value) > limit:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{key} is longer than {limit} characters.")
    return value


SEAT_ACTIONS: dict[str, Callable[[WebHandlers, str, dict[str, Any]], str]] = {
    "join": lambda h, a, b: h.join(a, _text(b, "code", 40)),
    "roster": lambda h, a, b: h.character(a, "roster"),
    "search": lambda h, a, b: h.character(a, "search", _text(b, "text", 400)),
    "claim": lambda h, a, b: h.character(a, "claim", _text(b, "name", 120)),
    "pick": lambda h, a, b: h.character(a, "pick", _text(b, "name", 120)),
    "goal": lambda h, a, b: h.goal(
        a, _route(b), _text(b, "text", 280, required=_route(b) == "own")
    ),
    "confirm": lambda h, a, b: h.goal(a, "confirm", _text(b, "label", 8)),
    "history": lambda h, a, b: h.history(a),
    "agent": lambda h, a, b: h.agent(
        a,
        Persona(
            name=_text(b, "name", 60),
            role=_text(b, "role", 60),
            voice=_text(b, "voice", 200, required=False),
            backstory=_text(b, "backstory", 400, required=False),
            relationship=_text(b, "relationship", 200, required=False),
        ),
    ),
    "order": lambda h, a, b: h.order(
        a,
        _text(b, "text", 4000),
        _visibility(b),
        _text(b, "spin", 280, required=False) or None,
    ),
    "ask": lambda h, a, b: h.advise(a, _text(b, "question", 1000)),
    "dossier": lambda h, a, b: h.dossier(a),
    "dispatch": lambda h, a, b: h.dispatch(a),
    "status": lambda h, a, b: h.status(a),
    "report": lambda h, a, b: h.report(a),
    "bug": lambda h, a, b: h.bug(a, _text(b, "notes", 4000)),
}


def _params(body: dict[str, Any]) -> dict[str, str]:
    """Order parameters as the CLI passes them: every value a string."""
    raw = body.get("params", {})
    if not isinstance(raw, dict) or not all(isinstance(v, str | int) for v in raw.values()):
        raise ApiError(HTTPStatus.BAD_REQUEST, "params is a JSON object of names and values.")
    return {str(k): str(v) for k, v in raw.items()}


def _route(body: dict[str, Any]) -> str:
    route = body.get("route")
    if route not in ("history", "could_have", "own"):
        raise ApiError(HTTPStatus.BAD_REQUEST, "route is history, could_have or own.")
    return str(route)


def _visibility(body: dict[str, Any]) -> str:
    vis = body.get("visibility", "private")
    if vis not in ("private", "public", "covert"):
        raise ApiError(HTTPStatus.BAD_REQUEST, "visibility is private, public or covert.")
    return str(vis)


REFUSALS = frozenset(
    {
        dh.BROKEN,
        dh.MODEL_DOWN,
        dh.WITHHELD,
        dh.NOT_LINKED,
        dh.NO_GAME,
        dh.PICK_FIRST,
        dh.BAD_CODE,
        dh.ALREADY_SEATED,
        dh.ASKED_OUT,
        dh.EMPTY_ORDER,
    }
)


def make_handler(app: App) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "revisionist-web"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:
            log.info("%s %s", self.address_string(), format % args)

        # --- plumbing ----------------------------------------------------------

        def _headers(self, status: HTTPStatus, kind: str, length: int) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(length))
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")

        def _send_json(self, status: HTTPStatus, payload: Any, cookie: str | None = None) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            self._headers(status, "application/json; charset=utf-8", len(body))
            self.send_header("Cache-Control", "no-store")
            if cookie is not None:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(body)

        def _cookie(self, token: str, max_age: int) -> str:
            secure = "; Secure" if app.secure_cookie else ""
            return f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}{secure}"

        def _token(self) -> str | None:
            jar = SimpleCookie()
            try:
                jar.load(self.headers.get("Cookie", ""))
            except Exception:
                return None
            morsel = jar.get(COOKIE)
            return morsel.value if morsel else None

        def _account(self) -> Account | None:
            token = self._token()
            if not token:
                return None
            store = app.open()
            try:
                return Accounts(store.conn).session(token, app.clock())
            finally:
                store.close()

        def _need_account(self) -> Account:
            account = self._account()
            if account is None:
                raise ApiError(HTTPStatus.UNAUTHORIZED, "Sign in first.")
            return account

        def _same_origin(self) -> None:
            """Writes must come from this page: a JSON body with our own Origin."""
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                raise ApiError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Send JSON.")
            origin = self.headers.get("Origin")
            host = self.headers.get("Host", "")
            if origin is not None and urlsplit(origin).netloc != host:
                raise ApiError(HTTPStatus.FORBIDDEN, "Cross-origin request refused.")

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if length > BODY_MAX:
                raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "That request is too large.")
            return _json_body(self.rfile.read(length) if length else b"")

        # --- verbs -------------------------------------------------------------

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            try:
                if path == "/api/me":
                    account = self._account()
                    if account is None:
                        self._send_json(HTTPStatus.OK, {"account": None})
                        return
                    self._send_json(HTTPStatus.OK, app.state_for(account))
                    return
                if path.startswith("/api/"):
                    raise ApiError(HTTPStatus.NOT_FOUND, "No such call.")
                self._static(path)
            except ApiError as exc:
                self._send_json(exc.status, {"error": exc.message})
            except snapshot.WithheldError:
                log.warning("snapshot withheld: canary hit")
                self._send_json(HTTPStatus.OK, {"error": dh.WITHHELD})
            except Exception:
                log.exception("GET %s failed", path)
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": dh.BROKEN})

        def do_POST(self) -> None:
            path = urlsplit(self.path).path
            try:
                self._same_origin()
                body = self._body()
                if path == "/api/signup":
                    self._signup(body)
                elif path == "/api/signin":
                    self._signin(body)
                elif path == "/api/recover":
                    self._recover(body)
                elif path == "/api/signout":
                    self._signout()
                elif path.startswith("/api/seat/"):
                    action = path.removeprefix("/api/seat/")
                    run = SEAT_ACTIONS.get(action)
                    if run is None:
                        raise ApiError(HTTPStatus.NOT_FOUND, "No such call.")
                    account = self._need_account()
                    reply = str(run(app.handlers, account.account_id, body))
                    self._send_json(HTTPStatus.OK, {"reply": reply, "refused": reply in REFUSALS})
                elif path.startswith("/api/realism/"):
                    account = self._need_account()
                    out = app.realism(account, path.removeprefix("/api/realism/"), body)
                    self._send_json(HTTPStatus.OK, out)
                else:
                    raise ApiError(HTTPStatus.NOT_FOUND, "No such call.")
            except ApiError as exc:
                self._send_json(exc.status, {"error": exc.message})
            except snapshot.WithheldError:
                log.warning("realism reply withheld: canary hit")
                self._send_json(HTTPStatus.OK, {"error": dh.WITHHELD})
            except AccountError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except Exception:
                log.exception("POST %s failed", path)
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": dh.BROKEN})

        # --- accounts ----------------------------------------------------------

        def _signup(self, body: dict[str, Any]) -> None:
            handle = _text(body, "handle", 32)
            password = body.get("password")
            if not isinstance(password, str):
                raise ApiError(HTTPStatus.BAD_REQUEST, "password must be text.")
            store = app.open()
            try:
                accounts = Accounts(store.conn)
                account, codes = accounts.create(handle, password)
                token = accounts.open_session(account.account_id, app.clock())
            finally:
                store.close()
            log.info("web account created: %s", account.account_id)
            self._send_json(
                HTTPStatus.CREATED,
                {"handle": account.handle, "codes": codes},
                cookie=self._cookie(token, SESSION_DAYS * 86400),
            )

        def _signin(self, body: dict[str, Any]) -> None:
            handle = _text(body, "handle", 32)
            password = body.get("password")
            if not isinstance(password, str):
                raise ApiError(HTTPStatus.BAD_REQUEST, "password must be text.")
            if app.throttled(handle):
                raise ApiError(HTTPStatus.TOO_MANY_REQUESTS, "Too many tries. Wait 15 minutes.")
            store = app.open()
            try:
                accounts = Accounts(store.conn)
                account = accounts.sign_in(handle, password)
                if account is None:
                    app.failed(handle)
                    raise ApiError(HTTPStatus.UNAUTHORIZED, "That handle and password don't match.")
                token = accounts.open_session(account.account_id, app.clock())
            finally:
                store.close()
            self._send_json(
                HTTPStatus.OK,
                {"handle": account.handle},
                cookie=self._cookie(token, SESSION_DAYS * 86400),
            )

        def _recover(self, body: dict[str, Any]) -> None:
            handle = _text(body, "handle", 32)
            code = _text(body, "code", 20)
            password = body.get("password")
            if not isinstance(password, str):
                raise ApiError(HTTPStatus.BAD_REQUEST, "password must be text.")
            if app.throttled(handle):
                raise ApiError(HTTPStatus.TOO_MANY_REQUESTS, "Too many tries. Wait 15 minutes.")
            store = app.open()
            try:
                accounts = Accounts(store.conn)
                got = accounts.recover(handle, code, password)
                if got is None:
                    app.failed(handle)
                    raise ApiError(HTTPStatus.UNAUTHORIZED, "That handle and code don't match.")
                account, codes = got
                token = accounts.open_session(account.account_id, app.clock())
            finally:
                store.close()
            self._send_json(
                HTTPStatus.OK,
                {"handle": account.handle, "codes": codes},
                cookie=self._cookie(token, SESSION_DAYS * 86400),
            )

        def _signout(self) -> None:
            token = self._token()
            if token:
                store = app.open()
                try:
                    Accounts(store.conn).close_session(token)
                finally:
                    store.close()
            self._send_json(HTTPStatus.OK, {"ok": True}, cookie=self._cookie("", 0))

        # --- static ------------------------------------------------------------

        def _static(self, path: str) -> None:
            rel = path.lstrip("/") or "index.html"
            target = (app.static / rel).resolve()
            if not target.is_relative_to(app.static) or not target.is_file():
                # A client-side route: hand back the shell, which routes itself.
                if "." in Path(rel).name:
                    raise ApiError(HTTPStatus.NOT_FOUND, "Not found.")
                target = app.static / "index.html"
            data = target.read_bytes()
            kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if kind.startswith("text/") or kind in ("application/javascript",):
                kind += "; charset=utf-8"
            if target.suffix == ".js":
                kind = "text/javascript; charset=utf-8"
            self._headers(HTTPStatus.OK, kind, len(data))
            cache = "no-cache" if target.suffix in (".html", ".js", ".css") else "max-age=86400"
            self.send_header("Cache-Control", cache)
            self.end_headers()
            self.wfile.write(data)

    return Handler


def serve(app: App, host: str, port: int) -> None:
    mimetypes.add_type("font/woff2", ".woff2")
    mimetypes.add_type("text/javascript", ".js")
    server = ThreadingHTTPServer((host, port), make_handler(app))
    server.daemon_threads = True
    log.info("web client on http://%s:%d/", host, port)
    try:
        server.serve_forever()
    finally:
        server.server_close()


__all__ = ["App", "make_handler", "serve"]
