"""What each Discord command does, with no Discord in it.

Every method takes the Discord user ID the interaction came from, runs on a
worker thread with its own store connection, and returns the reply text. The
acting seat always comes from ``discord_seats``; no command names a seat.
Model calls go through ``engine.play`` and the scheduler, exactly as the CLI
makes them; the bot has none of its own.

Replies are out of character and short. Errors are logged here, in full, for
the host; the player gets one line and never a stack trace.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from revisionist.core import events as ev
from revisionist.core.canary import scan_text
from revisionist.core.fold import state_hash
from revisionist.core.ids import CampaignId, SeatId
from revisionist.core.ontology import Controller, Persona, Phase
from revisionist.core.settings import Settings
from revisionist.core.visibility import Visibility
from revisionist.engine import campaign as cmp
from revisionist.engine import play, season, session_zero
from revisionist.engine import scheduler as sch
from revisionist.engine.discord.rest import CANNOT_DM, DiscordError, DiscordRest, nonce_for
from revisionist.engine.discord.tables import (
    INVITE_HOURS,
    Channels,
    Invites,
    Link,
    LinkError,
    SeatLinks,
    file_bug,
)
from revisionist.engine.ids import new_id
from revisionist.engine.llm import Transport, TransportError
from revisionist.engine.pg_store import PostgresEventStore
from revisionist.engine.runner import DelivererFactory, Publishes
from revisionist.engine.store import load_state

log = logging.getLogger("revisionist.discord")

NOT_LINKED = "You don't hold a seat in this game. Ask the host to link you."
HOST_ONLY = "Only the host can do that."
EMPTY_ORDER = "Write what you want done."
MODEL_DOWN = "Your agent can't be reached just now. Try again in a few minutes."
BROKEN = "Something went wrong on the host's side. The host has the details."
WITHHELD = "That reply was withheld: it named something your seat cannot know."
NOT_YET = "Not available yet."
BAD_CODE = "That code doesn't work. Ask the host for a new one."
ALREADY_SEATED = "You already hold a seat here."
PICK_FIRST = "Session Zero is open: pick your character first with /character."
JOINED_ZERO = "You are in. Session Zero is open: see who you could play with /character roster."
ESTABLISHED = "Session Zero is open at {place}. {n} characters to choose from: {names}."
DRAFT_REFUSED = (
    "The GM's draft of that moment did not hold together. Try again, or name it more precisely."
)
NO_DISPATCH = "No briefing yet. One arrives after each tick."
DRAFTING = (
    "Drafting {place} from the record. This takes about five minutes; "
    "I'll post here when Session Zero is open."
)
ONE_GAME = (
    "{name} ({campaign}) is still live: one game at a time. "
    "End it with /season-end, or set it aside with /retire {campaign}."
)
WELCOME = "This is your dispatch for {seat}. Your briefings arrive here after each tick."


ZERO_COMMANDS = frozenset({"character", "goal", "history", "agent"})
"""Seat commands that work before the seat exists: it appears at the claim."""


@dataclass(frozen=True)
class BotConfig:
    dsn: str
    guild_id: str
    host_id: str | None = None


class Handlers:
    """Constructed once per bot; connects to the database only inside each call."""

    def __init__(
        self,
        config: BotConfig,
        *,
        transport_factory: Callable[[], Transport],
        clock: Callable[[], datetime],
        briefings: Path,
        deliverer_factory: DelivererFactory | None,
        rest: DiscordRest | None = None,
    ) -> None:
        self.config = config
        self.transport_factory = transport_factory
        self.clock = clock
        self.briefings = briefings
        self.deliverer_factory = deliverer_factory
        self.rest = rest
        """Sends the welcome DM after ``/join``; without it the join just links."""

    # --- seat commands ------------------------------------------------------

    def order(
        self, user_id: str, text: str, visibility: str = "private", spin: str | None = None
    ) -> str:
        """An order, as ``revisionist order`` takes it. A DM is private with no spin."""
        if not text.strip():
            return EMPTY_ORDER
        return self._as_seat(
            user_id,
            "order",
            lambda store, link: (
                play.give_order(
                    store,
                    link.campaign_id,
                    link.seat_id,
                    text,
                    self.transport_factory(),
                    visibility=Visibility(visibility),
                    spin=spin,
                ).line
            ),
        )

    def briefing(self, user_id: str) -> str:
        def work(store: PostgresEventStore, link: Link) -> str:
            brief = play.get_briefing(
                store, link.campaign_id, link.seat_id, self.transport_factory()
            )
            return play.WITHHELD if brief.withheld else "\n".join(brief.lines)

        return self._as_seat(user_id, "briefing", work)

    def status(self, user_id: str) -> str:
        return self._as_seat(
            user_id,
            "status",
            lambda store, link: "\n".join(
                play.status_lines(
                    load_state(store, link.campaign_id),
                    link.seat_id,
                    store.read(link.campaign_id),
                )
            ),
        )

    def bug(self, user_id: str, notes: str) -> str:
        def work(store: PostgresEventStore, link: Link) -> str:
            state = load_state(store, link.campaign_id)
            text = notes[:4000]
            if store.sealer is not None:  # a seat's own words, like its orders
                text = store.sealer.seal_text(link.campaign_id, link.seat_id, text)
            n = file_bug(
                store.conn,
                link.campaign_id,
                link.seat_id,
                state.last_seq,
                state_hash(state),
                text,
            )
            return f"Bug {n} filed at tick {state.tick}. Thank you."

        return self._as_seat(user_id, "bug", work)

    def dispatch(self, user_id: str) -> str:
        """The seat's latest tick briefing, read back from the log. No model call."""

        def work(store: PostgresEventStore, link: Link) -> str:
            latest: ev.Event | None = None
            for e in store.read(link.campaign_id):
                if isinstance(e.payload, ev.NarrationWritten) and e.payload.seat_id == link.seat_id:
                    latest = e
            if latest is None:
                return NO_DISPATCH
            assert isinstance(latest.payload, ev.NarrationWritten)
            return f"Tick {latest.tick}:\n{latest.payload.text}"

        return self._as_seat(user_id, "dispatch", work)

    def join(self, user_id: str, code: str) -> str:
        """Redeem a host's invite code: bind this user to its seat, then say hello by DM.

        Unknown, spent and expired codes get the same answer.
        """
        try:
            store = self._open()
        except Exception:
            log.exception("/join: cannot open the store")
            return BROKEN
        try:
            if SeatLinks(store.conn).for_user(user_id, self.config.guild_id) is not None:
                return ALREADY_SEATED
            try:
                got = Invites(store.conn).redeem(code, user_id, self.config.guild_id, self.clock())
            except LinkError:
                got = None
            if got is None:
                return BAD_CODE
            campaign_id, seat_id = got
            log.info("/join: user %s now holds %s in %s", user_id, seat_id, campaign_id)
            seat = load_state(store, campaign_id).seats.get(seat_id)
            if seat is None:  # Session Zero: the seat appears when they claim a character
                return JOINED_ZERO + " " + self._welcome(user_id, campaign_id, "your seat")
            return f"You hold {seat.name}. " + self._welcome(user_id, campaign_id, seat.name)
        except Exception:
            log.exception("/join failed")
            return BROKEN
        finally:
            store.close()

    def _welcome(self, user_id: str, campaign_id: CampaignId, seat_name: str) -> str:
        if self.rest is None:
            return "Use /dispatch after each tick to read your briefing."
        try:
            channel = self.rest.open_dm(user_id)
            nonce = nonce_for(f"welcome:{campaign_id}:{user_id}", 0)
            self.rest.send(channel, WELCOME.format(seat=seat_name), nonce)
        except DiscordError as exc:
            if exc.code == CANNOT_DM:
                return (
                    "Your DMs are closed to me, so your briefings will wait for you: "
                    "use /dispatch after each tick."
                )
            log.warning("/join: welcome DM failed: %s", exc)
            return "I couldn't send you a test DM; use /dispatch after each tick."
        return "I've sent you a test DM; your briefings arrive there."

    # --- host commands ------------------------------------------------------

    def establish_refusal(self, user_id: str) -> str:
        """Why ``/establish`` cannot start now, or ``""``. Checked before the slow draft."""

        def work(store: PostgresEventStore) -> str:
            return self._one_game(store)

        return self._host_store(user_id, "establish", work)

    def _one_game(self, store: PostgresEventStore) -> str:
        live = sch.live_campaigns(store, self.clock())
        if not live:
            return ""
        return ONE_GAME.format(name=sch.campaign_name(store, live[-1]), campaign=live[-1])

    def establish(self, user_id: str, place: str) -> str:
        """Host: lock a point in history and draft its world (spec §5 step 2).

        Host only until co-GM votes exist (Layer 10 item 7); spec §5 has it pass
        by the table's vote at three or more humans.
        """

        def work(store: PostgresEventStore) -> str:
            if refused := self._one_game(store):
                return refused
            try:
                campaign_id = session_zero.establish(
                    store, place, Settings(), self.transport_factory()
                )
            except session_zero.SessionZeroError:
                log.warning("/establish: the draft for %r did not compile", place)
                return DRAFT_REFUSED
            sch.register(store, self.clock())
            state = load_state(store, campaign_id)
            names = sorted(state.entities[c].name for c in state.roster)
            return ESTABLISHED.format(place=place.strip(), n=len(names), names=", ".join(names))

        return self._host_store(user_id, "establish", work)

    def dashboard(self, user_id: str) -> str:
        return self._as_seat(
            user_id,
            "dashboard",
            lambda store, link: "\n".join(
                play.dashboard_lines(load_state(store, link.campaign_id), link.seat_id)
            ),
        )

    def season_end(self, user_id: str) -> str:
        """Host: the full reveal, the winners, and the report posted to the public channel."""

        def work(store: PostgresEventStore, campaign_id: CampaignId) -> str:
            try:
                lines = season.end_season(store, campaign_id)
            except season.SeasonError as exc:
                return str(exc)
            if self.deliverer_factory is not None:
                deliverer = self.deliverer_factory(store, campaign_id, self.briefings)
                if isinstance(deliverer, Publishes):
                    deliverer.publish()
            return "\n".join(lines)

        return self._as_host(user_id, "season-end", work)

    def retire(self, user_id: str, campaign: str = "") -> str:
        """Host: set a campaign aside for good, with no reveal. No argument lists them."""

        def work(store: PostgresEventStore) -> str:
            now = self.clock()
            live = sch.live_campaigns(store, now)
            names = {c: sch.campaign_name(store, c) for c in live}
            if not campaign.strip():
                if not live:
                    return "No live game to retire."
                return (
                    "Live: "
                    + "; ".join(f"{n} ({c})" for c, n in names.items())
                    + (". Retire one with /retire <id>.")
                )
            want = campaign.strip()
            hits = [
                c for c, n in names.items() if want in (c, n) or want.casefold() == n.casefold()
            ]
            if len(hits) != 1:
                return f"No single live game matches {want!r}. Run /retire alone to list them."
            target = hits[0]
            sch.ScheduleTable(store.conn).retire(target, now)
            links = SeatLinks(store.conn)
            for seat in links.users(target):
                links.unlink(target, seat)
            log.info("/retire: %s (%s) retired by the host", names[target], target)
            return (
                f"Retired {names[target]} ({target}): it never ticks again "
                "and its invites no longer work."
            )

        return self._host_store(user_id, "retire", work)

    def invite(self, user_id: str, seat: str = "") -> str:
        """Host: a single-use /join code for the live game, shown only to the host."""

        def work(store: PostgresEventStore, campaign_id: CampaignId) -> str:
            state = load_state(store, campaign_id)
            linked = SeatLinks(store.conn).users(campaign_id)
            if state.phase is Phase.SESSION_ZERO and not seat.strip():
                seat_id, who = SeatId(new_id()), "a new player"
            else:
                free = [
                    s.seat_id
                    for s in state.seats.values()
                    if s.controller is Controller.HUMAN and s.seat_id not in linked
                ]
                if seat.strip():
                    try:
                        seat_id = cmp.resolve_seat(state, seat.strip())
                    except LookupError:
                        return f"No seat called {seat.strip()!r} here."
                    if seat_id not in free:
                        return "That seat is not a free human seat."
                elif len(free) == 1:
                    seat_id = free[0]
                else:
                    return (
                        "No free human seat to invite into."
                        if not free
                        else (
                            "Name the seat: " + ", ".join(state.seats[s].name for s in free) + "."
                        )
                    )
                who = state.seats[seat_id].name
            now = self.clock()
            code = Invites(store.conn).issue(campaign_id, seat_id, now)
            expires = now + timedelta(hours=INVITE_HOURS)
            log.info("/invite: issued for %s in %s", seat_id, campaign_id)
            return (
                f"Invite for {who} in {sch.campaign_name(store, campaign_id)}, "
                f"good once until {expires:%Y-%m-%d %H:%M} UTC:\n{code}\n"
                "Send it to the player privately; they run /join with it."
            )

        return self._as_host(user_id, "invite", work)

    def channel(self, user_id: str, channel_id: str, in_guild: bool) -> str:
        """Host: make the channel this is run in the live game's public channel."""
        if not in_guild or not channel_id.isdigit():
            return "Run /channel in the server channel you want the public news posted in."

        def work(store: PostgresEventStore, campaign_id: CampaignId) -> str:
            Channels(store.conn).set_public(campaign_id, channel_id, self.config.guild_id)
            name = sch.campaign_name(store, campaign_id)
            return f"Public statements and the press for {name} now post in this channel."

        return self._as_host(user_id, "channel", work)

    # --- Session Zero (Layer 10 item 1) -----------------------------------------

    def character(self, user_id: str, action: str, text: str = "") -> str:
        """``roster``, ``search <what you want>``, ``claim <name>`` or ``pick <name>``."""

        def work(store: PostgresEventStore, link: Link) -> str:
            c, seat = link.campaign_id, link.seat_id
            match action:
                case "roster":
                    return "\n".join(session_zero.roster_lines(load_state(store, c)))
                case "search":
                    found = session_zero.search(store, c, seat, text, self.transport_factory())
                    log.info("/character search: %d chars asked, %d found", len(text), len(found))
                    return "\n".join(found) or (
                        "No one on the roster fits. Try other words, see everyone with "
                        "/character roster, or play anyone from the record with /character pick."
                    )
                case "claim":
                    return f"You play {session_zero.claim(store, c, seat, text)}."
                case "pick":
                    who = session_zero.pick(store, c, seat, text, self.transport_factory())
                    return f"You play {who}."
            return "Choose roster, search, claim or pick."

        return self._as_seat(user_id, "character", work)

    def goal(self, user_id: str, action: str, text: str = "") -> str:
        """``history``, ``could_have``, ``own <your goal>`` or ``confirm <number>``."""

        def work(store: PostgresEventStore, link: Link) -> str:
            c, seat = link.campaign_id, link.seat_id
            match action:
                case "history" | "could_have" | "own":
                    lines = session_zero.propose_goals(
                        store, c, seat, action, self.transport_factory(), text
                    )
                    return "\n".join(lines) + "\nChoose one with /goal confirm <number>."
                case "confirm":
                    if not text.strip().isdigit():
                        return "Give the number of the goal you choose."
                    return session_zero.confirm_goal(store, c, seat, int(text))
            return "Choose history, could_have, own or confirm."

        return self._as_seat(user_id, "goal", work)

    def history(self, user_id: str) -> str:
        def work(store: PostgresEventStore, link: Link) -> str:
            return session_zero.history_brief(
                store, link.campaign_id, link.seat_id, self.transport_factory()
            )

        return self._as_seat(user_id, "history", work)

    def agent(self, user_id: str, persona: Persona) -> str:
        def work(store: PostgresEventStore, link: Link) -> str:
            return session_zero.design_agent(store, link.campaign_id, link.seat_id, persona)

        return self._as_seat(user_id, "agent", work)

    def begin(self, user_id: str) -> str:
        def work(store: PostgresEventStore, campaign_id: CampaignId) -> str:
            try:
                return session_zero.begin(store, campaign_id)
            except session_zero.StepRefusedError as exc:
                return str(exc)

        return self._as_host(user_id, "begin", work)

    def advance(self, user_id: str) -> str:
        def work(store: PostgresEventStore, campaign_id: CampaignId) -> str:
            sch.register(store, self.clock()).advance(campaign_id)
            outcomes = sch.run(
                store,
                self.clock,
                offline=False,
                transport_factory=self.transport_factory,
                briefings=self.briefings,
                warn=log.warning,
                campaign=campaign_id,
                deliverer_factory=self.deliverer_factory,
            )
            # Fixed text for the failure cases: their details can quote a narration
            # or a model error, and those stay in the host's terminal.
            o = outcomes[0]
            if o.action is sch.Action.LOCKED:
                return "A tick is already running."
            if o.action is sch.Action.LEAK:
                return "Canary leak: the campaign is paused. Run `revisionist leaks` on the host."
            if o.action is sch.Action.FAILED:
                return (
                    "The tick failed and resumes on the next run. "
                    "See `revisionist scheduler status`."
                )
            return f"{o.action}: {o.detail}".rstrip(": ")

        return self._as_host(user_id, "advance", work)

    def pause(self, user_id: str) -> str:
        def work(store: PostgresEventStore, campaign_id: CampaignId) -> str:
            sch.register(store, self.clock()).pause(campaign_id, "paused by host on Discord")
            return "Paused. No ticks until /resume."

        return self._as_host(user_id, "pause", work)

    def resume(self, user_id: str) -> str:
        def work(store: PostgresEventStore, campaign_id: CampaignId) -> str:
            sch.register(store, self.clock()).resume(campaign_id)
            return "Resumed."

        return self._as_host(user_id, "resume", work)

    # --- plumbing -----------------------------------------------------------

    def _open(self) -> PostgresEventStore:
        store = cmp.open_store(self.config.dsn)
        assert isinstance(store, PostgresEventStore)
        return store

    def _as_seat(
        self, user_id: str, name: str, work: Callable[[PostgresEventStore, Link], str]
    ) -> str:
        try:
            store = self._open()
        except Exception:
            log.exception("/%s: cannot open the store", name)
            return BROKEN
        try:
            link = SeatLinks(store.conn).for_user(user_id, self.config.guild_id)
            if link is None:
                return NOT_LINKED
            if (
                name not in ZERO_COMMANDS
                and link.seat_id not in load_state(store, link.campaign_id).seats
            ):
                return PICK_FIRST
            try:
                reply = work(store, link)
            except session_zero.StepRefusedError as exc:
                return str(exc)
            # Nothing should reach here that the seat cannot see; check anyway.
            if scan_text(load_state(store, link.campaign_id), link.seat_id, reply):
                log.warning("/%s reply for %s withheld: canary hit", name, link.seat_id)
                return WITHHELD
            return reply
        except TransportError as exc:
            log.warning("/%s: model call failed: %s", name, exc)
            return MODEL_DOWN
        except Exception:
            log.exception("/%s failed", name)
            return BROKEN
        finally:
            store.close()

    def _as_host(
        self, user_id: str, name: str, work: Callable[[PostgresEventStore, CampaignId], str]
    ) -> str:
        def current(store: PostgresEventStore) -> str:
            # The host's own game if they hold a seat in it, else the live game.
            link = SeatLinks(store.conn).for_user(user_id, self.config.guild_id)
            if link is not None:
                return work(store, link.campaign_id)
            live = sch.live_campaigns(store, self.clock())
            return work(store, live[-1] if live else cmp.current_campaign(store))

        return self._host_store(user_id, name, current)

    def _host_store(
        self, user_id: str, name: str, work: Callable[[PostgresEventStore], str]
    ) -> str:
        if not self.config.host_id or user_id != self.config.host_id:
            return HOST_ONLY
        try:
            store = self._open()
        except Exception:
            log.exception("/%s: cannot open the store", name)
            return BROKEN
        try:
            return work(store)
        except Exception:
            log.exception("/%s failed", name)
            return BROKEN
        finally:
            store.close()


__all__ = [
    "ALREADY_SEATED",
    "BAD_CODE",
    "BROKEN",
    "DRAFTING",
    "DRAFT_REFUSED",
    "EMPTY_ORDER",
    "ESTABLISHED",
    "HOST_ONLY",
    "MODEL_DOWN",
    "NOT_LINKED",
    "NOT_YET",
    "NO_DISPATCH",
    "ONE_GAME",
    "WELCOME",
    "WITHHELD",
    "BotConfig",
    "Handlers",
]
