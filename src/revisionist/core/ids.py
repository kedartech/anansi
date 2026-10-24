"""Typed identifiers.

Every ID is a ``NewType`` over ``str`` so a ``SeatId`` cannot be passed where
an ``AssetId`` is expected under ``mypy --strict``. IDs are ULIDs (26-char
Crockford base32, time-sortable). The core never mints IDs, because minting
reads the clock; ``revisionist.engine.ids.new_id`` does that and the event store
rejects malformed IDs on append.
"""

import hashlib
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
GoalId = NewType("GoalId", str)
ContestId = NewType("ContestId", str)
DocumentId = NewType("DocumentId", str)
OrderId = NewType("OrderId", str)
QuantityId = NewType("QuantityId", str)
PipelineId = NewType("PipelineId", str)
FeedId = NewType("FeedId", str)
FeatureId = NewType("FeatureId", str)

WorldObjectId = EntityId | AssetId | RelationshipId | MeterId | ClockId | FactId | CommitmentId
"""Anything an Intent may target."""


_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def derived_id(*parts: str | int) -> str:
    """A deterministic 26-char Crockford ID from its inputs.

    The core may not read a clock, so anything it must name (a reveal grant,
    a roll seed) gets an ID derived from the campaign, the tick and the
    object it concerns. Same inputs, same ID, on every replay.
    """
    digest = hashlib.blake2b("\x1f".join(str(p) for p in parts).encode(), digest_size=17).digest()
    n = int.from_bytes(digest, "big")
    out = []
    for _ in range(26):
        out.append(_CROCKFORD[n & 31])
        n >>= 5
    return "".join(reversed(out))
