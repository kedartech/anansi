"""Route each seat's briefing, and post what is public, on Discord.

A bound seat's briefing goes by DM. With a public channel set (migration
0006), an outlet's narration is posted there, and after a clean tick every
player statement not yet posted. A human seat nobody holds yet is ``held``
(read later with ``/dispatch``), never written to the host's briefings
directory: the host may be the other player. Only the outlet without a public
channel still goes to a file.

``revisionist tick``, ``revisionist scheduler run`` and the bot's ``/advance``
use this when ``$REVISIONIST_DISCORD_TOKEN`` is set and the store is Postgres;
otherwise the tick keeps the Layer 7 file deliverer.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from revisionist.core import events as ev
from revisionist.core.canary import scan_text
from revisionist.core.ids import CampaignId, SeatId, derived_id
from revisionist.core.ontology import Controller, SeatKind
from revisionist.engine.discord.rest import (
    HELD_LABEL,
    PUBLIC_LABEL,
    DiscordDeliverer,
    DiscordRest,
)
from revisionist.engine.discord.tables import Channels, PgOutbox, SeatLinks
from revisionist.engine.runner import DelivererFactory, file_deliverer
from revisionist.engine.store import EventStore, load_state
from revisionist.engine.tick import Deliverer

TOKEN_ENV = "REVISIONIST_DISCORD_TOKEN"
WITHHELD_LABEL = "withheld"


@dataclass
class RoutingDeliverer:
    store: EventStore
    campaign_id: CampaignId
    discord: DiscordDeliverer
    files: Deliverer
    public_channel: str | None = None

    def deliver(self, seat_id: SeatId, text: str, idempotency_key: str) -> str:
        state = load_state(self.store, self.campaign_id)
        to_public = self.public_channel is not None and state.seats[seat_id].kind is SeatKind.OUTLET
        human = state.seats[seat_id].controller is Controller.HUMAN
        if human and seat_id not in self.discord.users:
            return HELD_LABEL
        if seat_id not in self.discord.users and not to_public:
            return self.files.deliver(seat_id, text, idempotency_key)
        # Last check before text leaves the host: a narration naming a canary the
        # seat cannot see goes nowhere, and the tick's leak scan then fails the
        # tick (exit 3, scheduler pause). The text stays in the log only.
        if scan_text(state, seat_id, text):
            return WITHHELD_LABEL
        if to_public:
            assert self.public_channel is not None
            self.discord.post(self.public_channel, text, idempotency_key)
            return PUBLIC_LABEL
        return self.discord.deliver(seat_id, text, idempotency_key)

    def publish(self) -> list[str]:
        """Post every statement and admin read not yet posted. Returns the keys it used."""
        if self.public_channel is None:
            return []
        state = load_state(self.store, self.campaign_id)
        keys = []
        for e in self.store.read(self.campaign_id):
            if isinstance(e.payload, ev.AdminRead):
                key = derived_id(self.campaign_id, e.tick, "admin_read", e.event_id)
                n = len(e.payload.events)
                self.discord.post(
                    self.public_channel,
                    f"Admin read: the host opened {n} private record{'s' if n != 1 else ''}."
                    " It is in the campaign log.",
                    key,
                )
                keys.append(key)
                continue
            if not isinstance(e.payload, ev.StatementIssued):
                continue
            p = e.payload
            # The author's own words; a canary the author cannot know means a leak upstream.
            if scan_text(state, p.seat_id, p.text):
                continue
            key = derived_id(self.campaign_id, e.tick, "statement", p.intent_id)
            self.discord.post(self.public_channel, f"{state.seats[p.seat_id].name}: {p.text}", key)
            keys.append(key)
        return keys


def routing_factory(rest: DiscordRest) -> DelivererFactory:
    def build(store: EventStore, campaign_id: CampaignId, briefings: Path) -> Deliverer:
        from revisionist.engine.pg_store import PostgresEventStore

        if not isinstance(store, PostgresEventStore):
            raise TypeError("Discord delivery needs the Postgres store")
        discord = DiscordDeliverer(
            rest, PgOutbox(store.conn), SeatLinks(store.conn).users(campaign_id)
        )
        return RoutingDeliverer(
            store,
            campaign_id,
            discord,
            file_deliverer(store, campaign_id, briefings),
            Channels(store.conn).public(campaign_id),
        )

    return build


def factory_from_env() -> DelivererFactory | None:
    """The routing factory when ``$REVISIONIST_DISCORD_TOKEN`` is set, else ``None``.

    Callers check the store is Postgres first.
    """
    token = os.environ.get(TOKEN_ENV)
    if not token:
        return None
    return routing_factory(DiscordRest(token))


__all__ = [
    "TOKEN_ENV",
    "WITHHELD_LABEL",
    "RoutingDeliverer",
    "factory_from_env",
    "routing_factory",
]
