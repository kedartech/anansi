"""Copy a campaign between stores and verify one from its log (build plan Layer 8).

``verify`` is the restore drill's check: replay from event 0, ignoring
snapshots, through the fold, which already checks ``state_hash`` at every
``tick_closed``; then compare the latest snapshot with the replay at its seq.
"""

from dataclasses import dataclass

from wyrd.core.fold import replay, state_hash
from wyrd.core.ids import CampaignId
from wyrd.engine.store import EventStore, StoreError


@dataclass(frozen=True)
class Verified:
    campaign_id: CampaignId
    problem: str = ""
    events: int = 0
    tick: int = 0
    state_hash: str = ""

    @property
    def ok(self) -> bool:
        return not self.problem


def verify_campaign(store: EventStore, campaign_id: CampaignId) -> Verified:
    try:
        events = list(store.read(campaign_id))
        if not events:
            return Verified(campaign_id, problem="no events")
        snapshot = store.latest_snapshot(campaign_id)
        cut = snapshot.last_seq + 1 if snapshot is not None else 0
        at_snapshot = replay(events[:cut]) if cut else None
        full = replay(events[cut:], initial=at_snapshot) if cut < len(events) else at_snapshot
    except (StoreError, ValueError) as exc:  # FoldError and pydantic errors are ValueErrors
        return Verified(campaign_id, problem=f"{type(exc).__name__}: {exc}"[:300])
    assert full is not None
    if snapshot is not None:
        if snapshot.last_seq >= len(events):
            return Verified(
                campaign_id, problem=f"snapshot at seq {snapshot.last_seq} is past the log"
            )
        if at_snapshot != snapshot:
            return Verified(
                campaign_id, problem=f"snapshot at seq {snapshot.last_seq} differs from the replay"
            )
    return Verified(campaign_id, events=len(events), tick=full.tick, state_hash=state_hash(full))


@dataclass(frozen=True)
class Copied:
    campaign_id: CampaignId
    copied: int
    already: int
    verified: Verified


def copy_campaign(source: EventStore, target: EventStore, campaign_id: CampaignId) -> Copied:
    """Append the events ``target`` lacks, then the latest snapshot, then verify.

    Rerunnable: a target that already holds a prefix of the log gets the rest.
    A target that holds anything else is refused.
    """
    events = list(source.read(campaign_id))
    if not events:
        raise StoreError(f"no events for campaign {campaign_id!r} in the source")
    have = list(target.read(campaign_id))
    if len(have) > len(events):
        raise StoreError(f"target is ahead of source ({len(have)} events, source {len(events)})")
    if have != events[: len(have)]:
        first = next(i for i, (a, b) in enumerate(zip(have, events, strict=False)) if a != b)
        raise StoreError(f"target diverges from source at seq {first}")
    tail = events[len(have) :]
    if tail:
        target.append_all(tail)
    snapshot = source.latest_snapshot(campaign_id)
    if snapshot is not None:
        target.save_snapshot(snapshot)
    verified = verify_campaign(target, campaign_id)
    if verified.ok and list(target.read(campaign_id)) != events:
        verified = Verified(campaign_id, problem="target log differs from source after copy")
    elif verified.ok and verified.state_hash != state_hash(replay(events)):
        verified = Verified(campaign_id, problem="state hash differs from source")
    return Copied(campaign_id, copied=len(tail), already=len(have), verified=verified)


__all__ = ["Copied", "Verified", "copy_campaign", "verify_campaign"]
