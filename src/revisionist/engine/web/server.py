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

from revisionist.core.ids import CampaignId, DocumentId, EntityId, OrderId, SeatId
from revisionist.core.ontology import Persona, Phase
from revisionist.core.project import project
from revisionist.core.realism.card import ChannelKind
from revisionist.core.realism.pipeline import OrderStatus, StepAction
from revisionist.core.state import State
from revisionist.engine import aide, build, cast, counsel, directive, realism_tick, session_zero
from revisionist.engine import campaign as cmp
from revisionist.engine import scheduler as sch
from revisionist.engine.discord import handlers as dh
from revisionist.engine.discord.tables import Invites, SeatLinks
from revisionist.engine.ids import new_id
from revisionist.engine.llm import Transport, TransportError
from revisionist.engine.pg_store import PostgresEventStore
from revisionist.engine.pitch import PITCH_MAX, PendingCard, PitchError, read_pitch
from revisionist.engine.prose import ClaudeProseWriter, ProseWriter, TemplateProseWriter
from revisionist.engine.store import load_state
from revisionist.engine.tick import NotInPlayError
from revisionist.engine.web import realism_snapshot, snapshot
from revisionist.engine.web.accounts import (
    SESSION_DAYS,
    Account,
    AccountError,
    Accounts,
    WebSeats,
)
from revisionist.engine.web.handlers import NOT_LINKED, WebHandlers

log = logging.getLogger("revisionist.web")

COOKIE = "rv_session"
BODY_MAX = 64 * 1024
SIGNIN_TRIES = 10
"""Failed sign-ins (or recoveries) per handle per 15 minutes."""
READY = ("autumn-1941",)
"""[proposed] The ready-made realism worlds a web account may start."""
MODEL_DOWN = "The model cannot be reached just now. Try again in a minute."

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
        self._pitches: dict[str, PendingCard] = {}
        """The setting card each account is choosing a start point from. Lost on a
        restart, which only means reading the pitch again."""
        self.spawn: Callable[[Callable[[], None]], None] = lambda run: threading.Thread(
            target=run, daemon=True
        ).start()

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
            if state.ruleset == "realism":
                seats = WebSeats(store.conn)
                game["host"] = seats.host(link.campaign_id) == account.account_id
                game["waiting"] = sum(
                    1 for s in seats.for_campaign(link.campaign_id) if s.seat_id not in state.seats
                )
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
                seat_view["realism"]["counsel"] = realism_snapshot.counsel_lines(
                    project(state, link.seat_id), counsel.counsel_records(log_now, link.seat_id)
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
                elif action in ("directive", "sign"):
                    asked = directive.Proposal(
                        person=_contact(state, seat, _text(body, "to", 120)),
                        channel=_channel(body),
                        text=_text(body, "text", directive.TEXT_MAX, required=False),
                        **(
                            {"lever": _text(body, "lever", 120), "params": _params(body)}
                            if action == "sign"
                            else {}
                        ),
                    )
                    if action == "directive":
                        read = directive.propose(
                            store,
                            cid,
                            seat,
                            asked.person,
                            asked.channel,
                            asked.text,
                            directive.ClaudeDirectiveReader(self.transport_factory()),
                        )
                        shown = realism_snapshot.proposal(project(state, seat), read)
                        said = (
                            f"Your aide reads this as: {shown['label']}."
                            if shown["label"]
                            else str(shown["reason"])
                        )
                        out = {"reply": said, "refused": not read.lever, "proposal": shown}
                        return snapshot.checked(state, seat, out)
                    order = directive.sign(store, cid, seat, asked)
                    first = state.pipelines[order.pipeline].steps[0]
                    who = state.entities[first.person].name
                    reply = f"Sent to {who}: {_lever_label(state, seat, order.lever)}."
                elif action == "aide":
                    writer: aide.AideWriter = (
                        aide.TemplateAide()
                        if self.offline_prose
                        else aide.ClaudeAide(self.transport_factory())
                    )
                    try:
                        held = aide.aide_for(store, cid, seat, writer)
                    except TransportError:
                        return {"reply": MODEL_DOWN, "refused": True}
                    brief = realism_snapshot.aide_brief(project(state, seat), held)
                    out = {"reply": "Your aide has read your papers.", "refused": False}
                    return snapshot.checked(state, seat, out | {"brief": brief})
                elif action == "consult":
                    person = _contact(state, seat, _text(body, "to", 120))
                    try:
                        answered = counsel.consult(
                            store,
                            cid,
                            seat,
                            person,
                            _text(body, "question", counsel.QUESTION_MAX),
                            counsel.ClaudeCounsellor(self.transport_factory()),
                        )
                    except TransportError:
                        return {"reply": MODEL_DOWN, "refused": True}
                    reply = f"{answered.person} has answered."
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
                    if humans != [seat] and WebSeats(store.conn).host(cid) != account.account_id:
                        raise ValueError("Only the host advances a shared game.")
                    prose: ProseWriter = (
                        TemplateProseWriter()
                        if self.offline_prose
                        else ClaudeProseWriter(self.transport_factory())
                    )
                    result = realism_tick.run_realism_tick(store, cid, prose=prose)
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
            except (ValueError, KeyError, NotInPlayError, counsel.CounselRefusedError) as exc:
                text = str(exc.args[0]) if isinstance(exc, KeyError) and exc.args else str(exc)
                return snapshot.checked(state, seat, {"reply": text, "refused": True})
            return snapshot.checked(state, seat, {"reply": reply, "refused": False})
        finally:
            store.close()

    # --- the game menu ---------------------------------------------------------

    def game(self, account: Account, action: str) -> dict[str, Any]:
        """One ``/api/game/<action>``: the host pauses, resumes or ends the game. Ending
        retires it as ``/retire`` does on Discord: it never ticks again, every seat is
        unlinked, and the log is kept. A refusal is a reply, not an error."""
        store = self.open()
        try:
            link = self.handlers._game_of(store, account.account_id, "report")
            if isinstance(link, str):
                raise ApiError(HTTPStatus.BAD_REQUEST, link)
            if action not in ("pause", "resume", "end"):
                raise ApiError(HTTPStatus.NOT_FOUND, "No such call.")
            cid = link.campaign_id
            if WebSeats(store.conn).host(cid) != account.account_id:
                return {"reply": "Only the host can do that.", "refused": True}
            table = sch.register(store, self.clock())
            name = sch.campaign_name(store, cid)
            if action == "pause":
                table.pause(cid, "paused by host on the web")
                return {
                    "reply": f"{name} is paused. Nothing moves until you resume.",
                    "refused": False,
                }
            if action == "resume":
                table.resume(cid)
                return {"reply": f"{name} is running again.", "refused": False}
            table.retire(cid, self.clock())
            links = SeatLinks(store.conn)
            for seat in links.users(cid):
                links.unlink(cid, seat)
            WebSeats(store.conn).unlink_campaign(cid)
            log.info("game ended on the web: %s (%s)", name, cid)
            return {"reply": f"{name} has ended. Its record is kept.", "refused": False}
        finally:
            store.close()

    # --- a new game ------------------------------------------------------------

    def new_game(self, account: Account, action: str, body: dict[str, Any]) -> dict[str, Any]:
        """One ``/api/new/<action>``: start a realism game, choose its people, invite
        the others and begin. A refusal is a reply, not an error."""
        store = self.open()
        try:
            if action == "worlds":
                decls = [(k, cmp.load_world(cmp.world_path(k))) for k in READY]
                return {"worlds": [{"key": k, "name": d.name, "start": d.start} for k, d in decls]}
            link = self.handlers.seat_of(store, account.account_id)
            if action in ("ready", "pitch", "cast"):
                if link is not None:
                    return {"reply": dh.ALREADY_SEATED, "refused": True}
                return self._start(store, account, action, body)
            if link is None:
                raise ApiError(HTTPStatus.BAD_REQUEST, NOT_LINKED)
            cid = link.campaign_id
            state = load_state(store, cid)
            if state.ruleset != "realism" or state.phase is not Phase.SESSION_ZERO:
                raise ApiError(HTTPStatus.BAD_REQUEST, "This game is not choosing its people.")
            if action == "pick":
                return self._pick(store, state, link.seat_id, _text(body, "name", 120))
            if action not in ("invite", "begin"):
                raise ApiError(HTTPStatus.NOT_FOUND, "No such call.")
            if WebSeats(store.conn).host(cid) != account.account_id:
                return {"reply": "Only the host can do that.", "refused": True}
            if action == "invite":
                code = Invites(store.conn).issue(cid, SeatId(new_id()), self.clock())
                return {
                    "reply": "Send this code to one player privately. It works once.",
                    "refused": False,
                    "code": code,
                }
            waiting = [
                s for s in WebSeats(store.conn).for_campaign(cid) if s.seat_id not in state.seats
            ]
            if waiting:
                return {"reply": f"Still to choose: {len(waiting)} player(s).", "refused": True}
            try:
                realism_tick.open_play(store, cid)
            except session_zero.StepRefusedError as exc:
                return {"reply": str(exc), "refused": True}
            self.spawn(lambda: self._build_rest(cid))
            return {
                "reply": "The game has begun. The rest of the cast is being drawn up "
                "and will act on their own.",
                "refused": False,
            }
        finally:
            store.close()

    def _start(
        self, store: PostgresEventStore, account: Account, action: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        """A ready-made world, a pitch read into start points, or the cast for one."""
        if action == "pitch":
            pitch = _text(body, "text", PITCH_MAX)
            try:
                card, costs = read_pitch(pitch, self.transport_factory())
            except PitchError as exc:
                return {"reply": exc.reason, "refused": True}
            except TransportError:
                return {"reply": MODEL_DOWN, "refused": True}
            with self._lock:
                self._pitches[account.account_id] = PendingCard(
                    pitch=pitch, card=card, costs=tuple(costs)
                )
            points = [{"label": p.label, "focus": p.focus} for p in card.start_points]
            return {
                "reply": card.reading,
                "refused": False,
                "card": {"reading": card.reading, "start_points": points},
            }
        if action == "ready":
            key = _text(body, "world", 40)
            if key not in READY:
                return {"reply": "There is no such game to start.", "refused": True}
            cid = cmp.new_campaign(store, cmp.load_world(cmp.world_path(key)))
        else:
            with self._lock:
                pending = self._pitches.get(account.account_id)
            if pending is None:
                return {"reply": "Write your pitch first.", "refused": True}
            choice = body.get("choice")
            starts = pending.card.start_points
            if not isinstance(choice, int) or not 1 <= choice <= len(starts):
                return {"reply": "Choose one of the start points.", "refused": True}
            try:
                world, costs = cast.draft_cast(
                    pending.card, starts[choice - 1], self.transport_factory()
                )
            except session_zero.SessionZeroError:
                log.warning("cast refused for %s", account.account_id)
                return {"reply": "The cast could not be drawn up. Try again.", "refused": True}
            except TransportError:
                return {"reply": MODEL_DOWN, "refused": True}
            cid = cmp.new_campaign(store, world)
            cmp.log_cost(store, cid, "session_zero", [*pending.costs, *costs])
            with self._lock:
                self._pitches.pop(account.account_id, None)
        WebSeats(store.conn).start(cid, SeatId(new_id()), account.account_id)
        return {"reply": "The cast is assembled. Choose who you will play.", "refused": False}

    def _pick(
        self, store: PostgresEventStore, state: State, seat: SeatId, name: str
    ) -> dict[str, Any]:
        """Take a person from the cast, building their card first if no one has."""
        if seat in state.seats:
            return {"reply": f"You already play {state.seats[seat].name}.", "refused": True}
        person = next(
            (c for c in state.roster if state.entities[c].name.casefold() == name.casefold()),
            None,
        )
        if person is None:
            return {"reply": "No one of that name is in the cast.", "refused": True}
        if person not in state.cards:
            try:
                build.build_figure(store, state.campaign_id, person, self.transport_factory())
            except build.BuildError:
                log.warning("card for %s refused in %s", person, state.campaign_id)
                return {
                    "reply": f"The papers for {state.entities[person].name} could not be "
                    "drawn up. Try again.",
                    "refused": True,
                }
            except TransportError:
                return {"reply": MODEL_DOWN, "refused": True}
        try:
            held = session_zero.claim(store, state.campaign_id, seat, state.entities[person].name)
        except session_zero.StepRefusedError as exc:
            return {"reply": str(exc), "refused": True}
        return {"reply": f"You are {held}.", "refused": False}

    def _build_rest(self, campaign_id: CampaignId) -> None:
        store = self.open()
        try:
            failed = build.build_rest(store, campaign_id, self.transport_factory())
            if failed:
                log.warning("not built in %s: %s", campaign_id, ", ".join(failed))
        except Exception:
            log.exception("building the rest of the cast failed in %s", campaign_id)
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


def _contact(state: State, seat: SeatId, name: str) -> EntityId:
    """The contact on the seat's card called ``name``. Raises ``ValueError``."""
    card = state.cards[state.seats[seat].embodies]
    for c in card.contacts:
        if state.entities[c.person].name == name:
            return c.person
    raise ValueError("That person is not within your reach.")


def _channel(body: dict[str, Any]) -> ChannelKind:
    try:
        return ChannelKind(_text(body, "channel", 20))
    except ValueError:
        raise ValueError("Choose how the directive is to travel.") from None


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
                elif path.startswith("/api/new/"):
                    account = self._need_account()
                    out = app.new_game(account, path.removeprefix("/api/new/"), body)
                    self._send_json(HTTPStatus.OK, out)
                elif path.startswith("/api/game/"):
                    account = self._need_account()
                    out = app.game(account, path.removeprefix("/api/game/"))
                    self._send_json(HTTPStatus.OK, out)
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
