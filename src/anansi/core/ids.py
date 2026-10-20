"""Typed identifiers.

Every ID is a ``NewType`` over ``str`` so a ``SeatId`` cannot be passed where
an ``AssetId`` is expected under ``mypy --strict``. IDs are ULIDs (26-char
Crockford base32, time-sortable). The core never mints IDs, because minting
reads the clock; ``anansi.engine.ids.new_id`` does that and the event store
rejects malformed IDs on append.
"""

from typing import NewType

ULID_PATTERN = r"^[0-9A-HJKMNP-TV-Z]{26}$"

CampaignId = NewType("CampaignId", str)
EventId = NewType("EventId", str)
EntityId = NewType("EntityId", str)
AssetId = NewType("AssetId", str)
RelationshipId = NewType("RelationshipId", str)
MeterId = NewType("MeterId", str)
ClockId = NewType("ClockId", str)
FactId = NewType("FactId", str)
ScheduledEventId = NewType("ScheduledEventId", str)
CommitmentId = NewType("CommitmentId", str)
SeatId = NewType("SeatId", str)
IntentId = NewType("IntentId", str)
DeltaId = NewType("DeltaId", str)

WorldObjectId = EntityId | AssetId | RelationshipId | MeterId | ClockId | FactId | CommitmentId
"""Anything an Intent may target."""
