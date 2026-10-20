"""Enums shared by the referee and the log (README §13)."""

from enum import StrEnum


class FeasibilityBand(StrEnum):
    IMPOSSIBLE = "impossible"
    LONG_SHOT = "long_shot"
    CONTESTED = "contested"
    LIKELY = "likely"
    ASSURED = "assured"


class OutcomeTier(StrEnum):
    FAIL_COMPLICATION = "fail_complication"
    PARTIAL = "partial"
    SUCCESS = "success"
    SUCCESS_OPPORTUNITY = "success_opportunity"


SUCCESS_CHANCE: dict[FeasibilityBand, float] = {
    FeasibilityBand.IMPOSSIBLE: 0.0,
    FeasibilityBand.LONG_SHOT: 0.20,
    FeasibilityBand.CONTESTED: 0.50,
    FeasibilityBand.LIKELY: 0.75,
    FeasibilityBand.ASSURED: 0.95,
}
"""Build plan Layer 3, accepted as starting numbers (Josh, 2026-10-19)."""

SUCCESS_SPLIT: tuple[tuple[OutcomeTier, float], ...] = (
    (OutcomeTier.PARTIAL, 0.40),
    (OutcomeTier.SUCCESS, 0.45),
    (OutcomeTier.SUCCESS_OPPORTUNITY, 0.15),
)


class TickStage(StrEnum):
    FREEZE = "freeze"
    PROPOSE = "propose"
    VALIDATE = "validate"
    APPLY = "apply"
    NARRATE = "narrate"
    DELIVER = "deliver"


class RejectionReason(StrEnum):
    MEANS_NOT_HELD = "means_not_held"
    TARGET_UNKNOWN = "target_unknown"
    OUT_OF_REACH = "out_of_reach"
    NO_SLOT = "no_slot"
    SAFETY_LINE = "safety_line"
    MALFORMED = "malformed"
    CLAMPED = "clamped"
    TIER_DISALLOWS = "tier_disallows"
    LETHALITY_DISALLOWS = "lethality_disallows"
    VOTE_CANNOT_CANCEL = "vote_cannot_cancel"
    DUPLICATE = "duplicate"
