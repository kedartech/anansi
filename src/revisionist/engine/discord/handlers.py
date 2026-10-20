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
from datetime import datetime
from pathlib import Path

from revisionist.core import events as ev
from revisionist.core.canary import scan_text
from revisionist.core.fold import state_hash
from revisionist.core.ids import CampaignId
from revisionist.core.visibility import Visibility
from revisionist.engine import campaign as cmp
from revisionist.engine import play
from revisionist.engine import scheduler as sch
from revisionist.engine.discord.rest import CANNOT_DM, DiscordError, DiscordRest, nonce_for
from revisionist.engine.discord.tables import Invites, Link, LinkError, SeatLinks, file_bug
from revisionist.engine.llm import Transport, TransportError
from revisionist.engine.pg_store import PostgresEventStore
from revisionist.engine.runner import DelivererFactory
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
NO_DISPATCH = "No briefing yet. One arrives after each tick."
WELCOME = "This is your dispatch for {seat}. Your briefings arrive here after each tick."


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
                play.status_lines(load_state(store, link.campaign_id), link.seat_id)
            ),
        )

    def bug(self, user_id: str, notes: str) -> str:
        def work(store: PostgresEventStore, link: Link) -> str:
            state = load_state(store, link.campaign_id)
            n = file_bug(
                store.conn,
                link.campaign_id,
                link.seat_id,
                state.last_seq,
                state_hash(state),
                notes,
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
            name = load_state(store, campaign_id).seats[seat_id].name
            log.info("/join: user %s now holds %s in %s", user_id, seat_id, campaign_id)
            return f"You hold {name}. " + self._welcome(user_id, campaign_id, name)
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
        return PostgresEventStore(self.config.dsn)

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
            reply = work(store, link)
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
        if not self.config.host_id or user_id != self.config.host_id:
            return HOST_ONLY
        try:
            store = self._open()
        except Exception:
            log.exception("/%s: cannot open the store", name)
            return BROKEN
        try:
            link = SeatLinks(store.conn).for_user(user_id, self.config.guild_id)
            campaign_id = link.campaign_id if link else cmp.current_campaign(store)
            return work(store, campaign_id)
        except Exception:
            log.exception("/%s failed", name)
            return BROKEN
        finally:
            store.close()


__all__ = [
    "ALREADY_SEATED",
    "BAD_CODE",
    "BROKEN",
    "EMPTY_ORDER",
    "HOST_ONLY",
    "MODEL_DOWN",
    "NOT_LINKED",
    "NOT_YET",
    "NO_DISPATCH",
    "WELCOME",
    "WITHHELD",
    "BotConfig",
    "Handlers",
]
