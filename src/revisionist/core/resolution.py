"""Enums shared by the referee and the log (spec §13)."""

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
    FeasibilityBand.LONG_SHOT: 0.15,
    FeasibilityBand.CONTESTED: 0.40,
    FeasibilityBand.LIKELY: 0.60,
    FeasibilityBand.ASSURED: 0.85,
}
"""Revised for playtest #25 (Josh, 2026-10-21).

Long shot 15%, Contested 40%, Likely 60%, Assured 85%.
"""

SUCCESS_SPLIT: tuple[tuple[OutcomeTier, float], ...] = (
    (OutcomeTier.PARTIAL, 0.40),
    (OutcomeTier.SUCCESS, 0.45),
    (OutcomeTier.SUCCESS_OPPORTUNITY, 0.15),
)


EXPOSURE_CHANCE: dict[int, int] = {0: 0, 1: 5, 2: 10, 3: 20, 4: 35, 5: 50}
"""Percent chance per resolved intent that a clock at this exposure is noticed [proposed]."""


class TickStage(StrEnum):
    FREEZE = "freeze"
    PROPOSE = "propose"
    VALIDATE = "validate"
    APPLY = "apply"
    NARRATE = "narrate"
    DELIVER = "deliver"
    # The realism runner's stages, appended so legacy markers keep their values.
    ADVANCE = "advance"
    PIPELINES = "pipelines"
    RESOLVE = "resolve"
    FEEDS = "feeds"
    PROSE = "prose"


LEGACY_STAGES: tuple[TickStage, ...] = (
    TickStage.FREEZE,
    TickStage.PROPOSE,
    TickStage.VALIDATE,
    TickStage.APPLY,
    TickStage.NARRATE,
    TickStage.DELIVER,
)
"""The stages ``run_tick`` completes, in order. Realism's run in their own runner."""


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
    SEAT_INACTIVE = "seat_inactive"
