"""Route each seat's briefing: Discord DM for a bound seat, a file for the rest.

``revisionist tick``, ``revisionist scheduler run`` and the bot's ``/advance`` use this
when ``$REVISIONIST_DISCORD_TOKEN`` is set and the store is Postgres; otherwise the
tick keeps the Layer 7 file deliverer.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from revisionist.core.canary import scan_text
from revisionist.core.ids import CampaignId, SeatId
from revisionist.engine.discord.rest import DiscordDeliverer, DiscordRest
from revisionist.engine.discord.tables import PgOutbox, SeatLinks
from revisionist.engine.runner import DelivererFactory, file_deliverer
from revisionist.engine.store import EventStore, load_state
from revisionist.engine.tick import Deliverer

TOKEN_ENV = "REVISIONIST_DISCORD_TOKEN"
FALLBACK_ENV = "REVISIONIST_DISCORD_FALLBACK_CHANNEL"
WITHHELD_LABEL = "withheld"


@dataclass
class RoutingDeliverer:
    store: EventStore
    campaign_id: CampaignId
    discord: DiscordDeliverer
    files: Deliverer

    def deliver(self, seat_id: SeatId, text: str, idempotency_key: str) -> str:
        if seat_id not in self.discord.users:
            return self.files.deliver(seat_id, text, idempotency_key)
        # Last check before text leaves the host: a narration naming a canary the
        # seat cannot see goes to the host's briefings file instead, and the
        # tick's leak scan then fails the tick (exit 3, scheduler pause).
        if scan_text(load_state(self.store, self.campaign_id), seat_id, text):
            self.files.deliver(seat_id, text, idempotency_key)
            return WITHHELD_LABEL
        return self.discord.deliver(seat_id, text, idempotency_key)


def routing_factory(rest: DiscordRest, fallback_channel: str | None) -> DelivererFactory:
    def build(store: EventStore, campaign_id: CampaignId, briefings: Path) -> Deliverer:
        from revisionist.engine.pg_store import PostgresEventStore

        if not isinstance(store, PostgresEventStore):
            raise TypeError("Discord delivery needs the Postgres store")
        discord = DiscordDeliverer(
            rest,
            PgOutbox(store.conn),
            SeatLinks(store.conn).users(campaign_id),
            fallback_channel,
        )
        return RoutingDeliverer(
            store, campaign_id, discord, file_deliverer(store, campaign_id, briefings)
        )

    return build


def factory_from_env() -> DelivererFactory | None:
    """The routing factory when ``$REVISIONIST_DISCORD_TOKEN`` is set, else ``None``.

    Callers check the store is Postgres first.
    """
    token = os.environ.get(TOKEN_ENV)
    if not token:
        return None
    return routing_factory(DiscordRest(token), os.environ.get(FALLBACK_ENV) or None)


__all__ = [
    "FALLBACK_ENV",
    "TOKEN_ENV",
    "WITHHELD_LABEL",
    "RoutingDeliverer",
    "factory_from_env",
    "routing_factory",
]
