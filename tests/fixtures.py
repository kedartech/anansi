"""A small hand-written campaign for fold, projection and store tests.

Two player seats (north, south), one AI faction, one outlet. North holds a
covert army and a covert fact with a canary; south must never see either.
"""

from anansi.core import events as ev
from anansi.core.events import Event, Versions
from anansi.core.ids import (
    AssetId,
    CampaignId,
    ClockId,
    EntityId,
    EventId,
    FactId,
    MeterId,
    RelationshipId,
    ScheduledEventId,
    SeatId,
)
from anansi.core.ontology import (
    Asset,
    AssetKind,
    Clock,
    ClockKind,
    Controller,
    Entity,
    EntityKind,
    EntityStatus,
    Fact,
    Meter,
    Persona,
    Relationship,
    Seat,
    SeatKind,
)
from anansi.core.settings import Settings
from anansi.core.visibility import PUBLIC, Scope, Visibility, private_to

__all__ = [
    "AssetId",
    "CampaignId",
    "ClockId",
    "EntityId",
    "EntityStatus",
    "FactId",
    "MeterId",
    "ScheduledEventId",
    "SeatId",
]

CAMPAIGN = CampaignId("01ARZ3NDEKTSV4RRFFQ69G5FA0")
NORTH = SeatId("01ARZ3NDEKTSV4RRFFQ69G5SN0")
SOUTH = SeatId("01ARZ3NDEKTSV4RRFFQ69G5SS0")
GUILD = SeatId("01ARZ3NDEKTSV4RRFFQ69G5SG0")
HERALD = SeatId("01ARZ3NDEKTSV4RRFFQ69G5SH0")

E_NORTH = EntityId("01ARZ3NDEKTSV4RRFFQ69G5EN0")
E_SOUTH = EntityId("01ARZ3NDEKTSV4RRFFQ69G5ES0")
E_GUILD = EntityId("01ARZ3NDEKTSV4RRFFQ69G5EG0")
E_HERALD = EntityId("01ARZ3NDEKTSV4RRFFQ69G5EH0")
E_NORTH_AIDE = EntityId("01ARZ3NDEKTSV4RRFFQ69G5EA0")
E_SOUTH_AIDE = EntityId("01ARZ3NDEKTSV4RRFFQ69G5EB0")

A_NORTH_ARMY = AssetId("01ARZ3NDEKTSV4RRFFQ69G5AN0")
A_NORTH_SECRET_ARMY = AssetId("01ARZ3NDEKTSV4RRFFQ69G5AX0")
A_SOUTH_TREASURY = AssetId("01ARZ3NDEKTSV4RRFFQ69G5AS0")
A_SOUTH_GUARD = AssetId("01ARZ3NDEKTSV4RRFFQ69G5AT0")
A_GUILD_NETWORK = AssetId("01ARZ3NDEKTSV4RRFFQ69G5AG0")

R_NORTH_SOUTH = RelationshipId("01ARZ3NDEKTSV4RRFFQ69G5RN0")
M_NORTH_STABILITY = MeterId("01ARZ3NDEKTSV4RRFFQ69G5MN0")
C_NORTH_PLOT = ClockId("01ARZ3NDEKTSV4RRFFQ69G5CN0")
F_NORTH_PLOT = FactId("01ARZ3NDEKTSV4RRFFQ69G5FN0")
F_PUBLIC_TREATY = FactId("01ARZ3NDEKTSV4RRFFQ69G5FP0")

CANARY = "CANARY-NORTH-PLOT-7f3a9c"

VERSIONS = Versions(engine="0.0.1", schema_version="1")


def envelope(seq: int, payload: ev.Payload, *, tick: int = 0, scope: Scope = PUBLIC) -> Event:
    return Event(
        event_id=EventId(f"01ARZ3NDEKTSV4RRFFQ69{seq:05d}"),
        campaign_id=CAMPAIGN,
        seq=seq,
        tick=tick,
        scope=scope,
        payload=payload,
        versions=VERSIONS,
    )


def creation_payloads() -> list[ev.Payload]:
    restricted_ns = Scope(visibility=Visibility.RESTRICTED, known_by=frozenset({NORTH, SOUTH}))
    return [
        ev.CampaignCreated(name="Two Rivers", world="two-rivers"),
        ev.SettingsSet(settings=Settings()),
        ev.EntityCreated(entity=Entity(entity_id=E_NORTH, kind=EntityKind.POLITY, name="North")),
        ev.EntityCreated(entity=Entity(entity_id=E_SOUTH, kind=EntityKind.POLITY, name="South")),
        ev.EntityCreated(
            entity=Entity(entity_id=E_GUILD, kind=EntityKind.FACTION, name="River Guild")
        ),
        ev.EntityCreated(entity=Entity(entity_id=E_HERALD, kind=EntityKind.ORG, name="Herald")),
        ev.EntityCreated(
            entity=Entity(
                entity_id=E_NORTH_AIDE, kind=EntityKind.PERSON, name="Ines", parent=E_NORTH
            )
        ),
        ev.EntityCreated(
            entity=Entity(
                entity_id=E_SOUTH_AIDE, kind=EntityKind.PERSON, name="Tomas", parent=E_SOUTH
            )
        ),
        ev.AssetCreated(
            asset=Asset(
                asset_id=A_NORTH_ARMY,
                kind=AssetKind.FORCE,
                name="Northern Levies",
                tier=2,
                controller=E_NORTH,
                scope=PUBLIC,
            )
        ),
        ev.AssetCreated(
            asset=Asset(
                asset_id=A_NORTH_SECRET_ARMY,
                kind=AssetKind.FORCE,
                name="Hill Irregulars",
                tier=1,
                controller=E_NORTH,
                scope=private_to(NORTH, covert=True),
            )
        ),
        ev.AssetCreated(
            asset=Asset(
                asset_id=A_SOUTH_TREASURY,
                kind=AssetKind.WEALTH,
                name="Southern Treasury",
                tier=3,
                controller=E_SOUTH,
                scope=PUBLIC,
            )
        ),
        ev.AssetCreated(
            asset=Asset(
                asset_id=A_SOUTH_GUARD,
                kind=AssetKind.FORCE,
                name="Southern Guard",
                tier=3,
                controller=E_SOUTH,
                scope=PUBLIC,
            )
        ),
        ev.AssetCreated(
            asset=Asset(
                asset_id=A_GUILD_NETWORK,
                kind=AssetKind.NETWORK,
                name="Guild Factors",
                tier=2,
                controller=E_GUILD,
                scope=private_to(GUILD),
            )
        ),
        ev.RelationshipSet(
            relationship=Relationship(
                relationship_id=R_NORTH_SOUTH,
                a=E_NORTH,
                b=E_SOUTH,
                disposition=-1,
                scope=PUBLIC,
            )
        ),
        ev.MeterCreated(
            meter=Meter(
                meter_id=M_NORTH_STABILITY,
                owner=E_NORTH,
                name="Stability",
                value=60,
                scope=restricted_ns,
            )
        ),
        ev.ClockCreated(
            clock=Clock(
                clock_id=C_NORTH_PLOT,
                kind=ClockKind.OPERATION,
                name="Arm the hills",
                owner=NORTH,
                segments_total=4,
                exposure=2,
                holds_slot=True,
                scope=private_to(NORTH, covert=True),
            )
        ),
        ev.FactAsserted(
            fact=Fact(
                fact_id=F_NORTH_PLOT,
                subject=E_NORTH,
                predicate="arms",
                obj=A_NORTH_SECRET_ARMY,
                exposure=2,
                scope=private_to(NORTH, covert=True),
                canary=CANARY,
            )
        ),
        ev.FactAsserted(
            fact=Fact(
                fact_id=F_PUBLIC_TREATY,
                subject=E_NORTH,
                predicate="signed_truce_with",
                obj=E_SOUTH,
                scope=PUBLIC,
            )
        ),
        ev.SeatCreated(
            seat=Seat(
                seat_id=NORTH,
                kind=SeatKind.PLAYER,
                name="Regent of the North",
                embodies=E_NORTH,
                controller=Controller.HUMAN,
                objectives=frozenset({C_NORTH_PLOT}),
                standing_orders=("hold the passes",),
            )
        ),
        ev.SeatCreated(
            seat=Seat(
                seat_id=SOUTH,
                kind=SeatKind.PLAYER,
                name="Chancellor of the South",
                embodies=E_SOUTH,
                controller=Controller.HUMAN,
            )
        ),
        ev.SeatCreated(
            seat=Seat(seat_id=GUILD, kind=SeatKind.FACTION, name="River Guild", embodies=E_GUILD)
        ),
        ev.SeatCreated(
            seat=Seat(seat_id=HERALD, kind=SeatKind.OUTLET, name="The Herald", embodies=E_HERALD)
        ),
        ev.AgentAssigned(
            seat_id=NORTH, agent=E_NORTH_AIDE, persona=Persona(name="Ines", role="chief of staff")
        ),
        ev.AgentAssigned(
            seat_id=SOUTH, agent=E_SOUTH_AIDE, persona=Persona(name="Tomas", role="chief of staff")
        ),
    ]


def creation_events() -> list[Event]:
    return [envelope(i, p) for i, p in enumerate(creation_payloads())]
