"""Seat commands for web accounts: the Discord ``Handlers``, with the seat looked up
in ``web_seats`` instead of ``discord_seats``.

Every command a player runs from the web is the Discord command's own code, so a
reply reads the same on both. Only three things differ: who holds the seat, how an
invite is redeemed (``join``), and the welcome line. Host commands stay on Discord
and the CLI; a web account is never the host.
"""

from collections.abc import Callable
from datetime import datetime

from revisionist.engine import scheduler as sch
from revisionist.engine.discord import handlers as dh
from revisionist.engine.discord.handlers import BotConfig, Handlers
from revisionist.engine.discord.tables import Invites, Link, LinkError
from revisionist.engine.llm import Transport
from revisionist.engine.pg_store import PostgresEventStore
from revisionist.engine.store import load_state
from revisionist.engine.web.accounts import WebSeats, person

WEB = "web"
"""The ``guild_id`` a web seat's ``Link`` carries, so Discord code that reads one works."""

NOT_LINKED = "You don't hold a seat in this game. Ask the host for an invite code, then join."
JOINED_ZERO = "You are in. Session Zero is open: choose who you will play from the roster."
WELCOME = "Your briefing appears here after each tick."


class WebHandlers(Handlers):
    """``user_id`` is a web account ID throughout."""

    def __init__(
        self,
        dsn: str,
        *,
        transport_factory: Callable[[], Transport],
        clock: Callable[[], datetime],
        briefings: "object",
    ) -> None:
        super().__init__(
            BotConfig(dsn, WEB, None),
            transport_factory=transport_factory,
            clock=clock,
            briefings=briefings,  # type: ignore[arg-type]
            deliverer_factory=None,
        )

    def _game_of(self, store: PostgresEventStore, user_id: str, name: str) -> Link | str:
        """The account's seat in the game in play, else the reply that says why not.

        As on Discord: only a live game counts, and ``report`` also reaches a season
        that is over but not retired.
        """
        now = self.clock()
        live = sch.live_campaigns(store, now)
        held = WebSeats(store.conn).for_account(user_id)
        allowed = list(live)
        if name == "report":
            table = sch.register(store, now)
            allowed += [c for c in store.campaign_ids() if table.get(c).retired_at is None]
        for seat in held:
            if seat.campaign_id in allowed:
                return Link(seat.campaign_id, seat.seat_id, user_id, WEB, seat.linked_at)
        if live:
            return NOT_LINKED
        return dh.NO_GAME

    def seat_of(self, store: PostgresEventStore, account_id: str) -> Link | None:
        found = self._game_of(store, account_id, "status")
        return found if isinstance(found, Link) else None

    def join(self, user_id: str, code: str) -> str:
        """Redeem a host's invite code for this account. The codes are the ones
        ``/invite`` and ``revisionist discord invite`` issue; each still works once."""
        try:
            store = self._open()
        except Exception:
            dh.log.exception("web join: cannot open the store")
            return dh.BROKEN
        try:
            seats = WebSeats(store.conn)
            if self.seat_of(store, user_id) is not None:
                return dh.ALREADY_SEATED

            def bind(campaign_id: object, seat_id: object) -> None:
                seats.link(campaign_id, seat_id, user_id)  # type: ignore[arg-type]

            try:
                got = Invites(store.conn).redeem(
                    code, person(user_id), WEB, self.clock(), bind=bind
                )
            except LinkError:
                got = None
            if got is None:
                return dh.BAD_CODE
            campaign_id, seat_id = got
            dh.log.info("web join: account %s now holds %s in %s", user_id, seat_id, campaign_id)
            seat = load_state(store, campaign_id).seats.get(seat_id)
            if seat is None:
                return JOINED_ZERO
            return f"You hold {seat.name}. {WELCOME}"
        except Exception:
            dh.log.exception("web join failed")
            return dh.BROKEN
        finally:
            store.close()

    def _welcome(self, user_id: str, campaign_id: object, seat_name: str) -> str:
        return WELCOME


__all__ = ["JOINED_ZERO", "NOT_LINKED", "WEB", "WebHandlers"]
