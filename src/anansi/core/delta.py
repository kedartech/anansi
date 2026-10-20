"""Deltas: the only thing an LLM may propose (README §11, §12).

Each delta is a typed change against an existing ID. There are no free-form
fields and no deletes. The engine clamps a proposal to the Layer 3 table,
applies it, and logs it; narration is written from the applied result.
"""

from typing import Annotated, Literal

from pydantic import Field

from anansi.core._base import Label, Model
from anansi.core.ids import (
    AssetId,
    ClockId,
    CommitmentId,
    DeltaId,
    EntityId,
    FactId,
    MeterId,
    RelationshipId,
    ScheduledEventId,
    SeatId,
)
from anansi.core.ontology import (
    AssetKind,
    AssetStatus,
    Clause,
    ClockKind,
    ClockStatus,
    EntityStatus,
)
from anansi.core.visibility import Scope


class _Delta(Model):
    delta_id: DeltaId
    reason: str = Field(max_length=200)
    """One line of structured rationale for ``/why``. Never shown to players."""


class AssetTierDelta(_Delta):
    kind: Literal["asset_tier"] = "asset_tier"
    asset_id: AssetId
    change: int = Field(ge=-2, le=2)


class AssetStatusSet(_Delta):
    kind: Literal["asset_status"] = "asset_status"
    asset_id: AssetId
    status: AssetStatus


class AssetControllerSet(_Delta):
    kind: Literal["asset_controller"] = "asset_controller"
    asset_id: AssetId
    controller: EntityId


class AssetCreate(_Delta):
    kind: Literal["asset_create"] = "asset_create"
    asset_id: AssetId
    asset_kind: AssetKind
    name: str = Field(max_length=80)
    tier: int = Field(ge=0, le=1)
    """New assets start at tier 0 or 1. Higher tiers only from Session Zero."""
    controller: EntityId
    scope: Scope


class MeterDelta(_Delta):
    kind: Literal["meter"] = "meter"
    meter_id: MeterId
    change: int


class DispositionDelta(_Delta):
    kind: Literal["disposition"] = "disposition"
    relationship_id: RelationshipId
    change: int = Field(ge=-2, le=2)


class RelationshipTagSet(_Delta):
    kind: Literal["relationship_tags"] = "relationship_tags"
    relationship_id: RelationshipId
    add_price: frozenset[Label] = frozenset()
    add_leverage: frozenset[Label] = frozenset()
    remove_leverage: frozenset[Label] = frozenset()


class ClockAdvance(_Delta):
    kind: Literal["clock_advance"] = "clock_advance"
    clock_id: ClockId
    segments: int = Field(ge=-2, le=2)
    """Negative on complications."""


class ClockCreate(_Delta):
    kind: Literal["clock_create"] = "clock_create"
    clock_id: ClockId
    clock_kind: ClockKind
    name: str = Field(max_length=80)
    owner: SeatId | None = None
    target: EntityId | None = None
    segments_total: int = Field(ge=1, le=12)
    exposure: int = Field(default=0, ge=0, le=5)
    scope: Scope


class ClockStatusSet(_Delta):
    """Fail or abandon a clock. Resolution happens by filling it (``ClockAdvance``)."""

    kind: Literal["clock_status"] = "clock_status"
    clock_id: ClockId
    status: Literal[ClockStatus.FAILED, ClockStatus.ABANDONED]


class ClockExposureDelta(_Delta):
    kind: Literal["clock_exposure"] = "clock_exposure"
    clock_id: ClockId
    change: int = Field(ge=-2, le=2)


class FactAssert(_Delta):
    kind: Literal["fact_assert"] = "fact_assert"
    fact_id: FactId
    subject: EntityId
    predicate: str = Field(max_length=60)
    obj: EntityId | AssetId | ClockId | Label
    truth: bool = True
    exposure: int = Field(default=0, ge=0, le=5)
    scope: Scope


class KnowledgeGrant(_Delta):
    """Widen who knows an existing fact, asset or clock.

    Discovery, leaks, exposure rolls and ``reveal()`` all emit this. Nothing
    else in the engine knows about reveals.
    """

    kind: Literal["knowledge_grant"] = "knowledge_grant"
    target: FactId | AssetId | ClockId | CommitmentId
    seats: frozenset[SeatId]
    make_public: bool = False


class EntityStatusSet(_Delta):
    kind: Literal["entity_status"] = "entity_status"
    entity_id: EntityId
    status: EntityStatus


class CommitmentCreate(_Delta):
    kind: Literal["commitment_create"] = "commitment_create"
    commitment_id: CommitmentId
    parties: frozenset[EntityId]
    clauses: tuple[Clause, ...] = Field(min_length=1, max_length=6)
    scope: Scope


class CommitmentStatusSet(_Delta):
    kind: Literal["commitment_status"] = "commitment_status"
    commitment_id: CommitmentId
    status: Literal["accepted", "broken", "expired", "withdrawn"]


class ScheduledEventQueue(_Delta):
    kind: Literal["schedule"] = "schedule"
    scheduled_id: ScheduledEventId
    fire_in_ticks: int = Field(ge=1, le=24)
    name: str = Field(max_length=80)
    source_clock: ClockId | None = None
    scope: Scope


class SeatSlotsDelta(_Delta):
    kind: Literal["seat_slots"] = "seat_slots"
    seat_id: SeatId
    change: int = Field(ge=-2, le=2)


Delta = Annotated[
    AssetTierDelta
    | AssetStatusSet
    | AssetControllerSet
    | AssetCreate
    | MeterDelta
    | DispositionDelta
    | RelationshipTagSet
    | ClockAdvance
    | ClockCreate
    | ClockStatusSet
    | ClockExposureDelta
    | FactAssert
    | KnowledgeGrant
    | EntityStatusSet
    | CommitmentCreate
    | CommitmentStatusSet
    | ScheduledEventQueue
    | SeatSlotsDelta,
    Field(discriminator="kind"),
]
