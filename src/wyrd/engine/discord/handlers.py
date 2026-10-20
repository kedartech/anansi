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

from wyrd.core.canary import scan_text
from wyrd.core.fold import state_hash
from wyrd.core.ids import CampaignId
from wyrd.engine import campaign as cmp
from wyrd.engine import play
from wyrd.engine import scheduler as sch
from wyrd.engine.discord.tables import Link, SeatLinks, file_bug
from wyrd.engine.llm import Transport, TransportError
from wyrd.engine.pg_store import PostgresEventStore
from wyrd.engine.runner import DelivererFactory
from wyrd.engine.store import load_state

log = logging.getLogger("wyrd.discord")

NOT_LINKED = "You don't hold a seat in this game. Ask the host to link you."
HOST_ONLY = "Only the host can do that."
EMPTY_ORDER = "Write what you want done."
MODEL_DOWN = "Your agent can't be reached just now. Try again in a few minutes."
BROKEN = "Something went wrong on the host's side. The host has the details."
WITHHELD = "That reply was withheld: it named something your seat cannot know."
NOT_YET = "Not available yet."


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
    ) -> None:
        self.config = config
        self.transport_factory = transport_factory
        self.clock = clock
        self.briefings = briefings
        self.deliverer_factory = deliverer_factory

    # --- seat commands ------------------------------------------------------

    def order(self, user_id: str, text: str) -> str:
        if not text.strip():
            return EMPTY_ORDER
        return self._as_seat(
            user_id,
            "order",
            lambda store, link: (
                play.give_order(
                    store, link.campaign_id, link.seat_id, text, self.transport_factory()
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
                return "Canary leak: the campaign is paused. Run `wyrd leaks` on the host."
            if o.action is sch.Action.FAILED:
                return "The tick failed and resumes on the next run. See `wyrd scheduler status`."
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
    "BROKEN",
    "EMPTY_ORDER",
    "HOST_ONLY",
    "MODEL_DOWN",
    "NOT_LINKED",
    "NOT_YET",
    "WITHHELD",
    "BotConfig",
    "Handlers",
]
