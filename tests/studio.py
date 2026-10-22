"""Scripted answers for the v3 ``/establish``: the pitch card and the staged draft.

The hand-written drafts in ``tests/drafts/`` are single world files. ``card_for``
turns one into a one-start-point setting card, and ``stages_of`` splits it into
the three stage replies ``engine.draft`` asks for. The worlds stay undated (no
``start_date``, no contests), so they compile and play exactly as the old
one-call draft did; ``tests/test_draft.py`` covers a dated v3 world.

``answer`` is what a scripted transport calls for any v3 call class; it returns
None for every other class so the transport can answer those itself.
"""

from typing import Any

from revisionist.engine import draft as dr

V3_CALLS = frozenset({"pitch", "draft_outline", "draft_detail", "draft_schedule"})
DATES = {"Florence, April 1478": "1478-04-19", "Washington, October 1962": "1962-10-16"}


def card_for(raw: dict[str, Any]) -> dict[str, Any]:
    """The ``pitch`` reply for a draft: one start point, labelled with its name."""
    name = str(raw["name"])
    return {
        "reading": f"{name}, read from the record.",
        "start_points": [
            {
                "label": name,
                "start_date": DATES.get(name, "1478-04-19"),
                "contests": [f"Who holds {name}"],
                "powers": [e["key"] for e in raw["entities"] if e["kind"] == "polity"][:3],
                "focus": name,
                "schedule": ["The first thing history did next.", "The second."],
            }
        ],
        "named": [],
        "refusal": "",
    }


def stages_of(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The three stage replies for a draft, undated."""
    outline: dict[str, Any] = {k: raw[k] for k in ("world", "name", "entities") if k in raw}
    outline |= {"contests": [], "start": raw["name"]}
    detail: dict[str, Any] = {k: raw[k] for k in dr.FIELDS["detail"] if k in raw}
    return {"outline": outline, "detail": detail, "schedule": {"schedule": []}}


class Drafts:
    """A queue of drafts answering v3 calls. A refused stage is asked again and
    gets the same stage of the next draft in the queue, the way the old GM gave a
    retry the next whole draft; the last draft is reused once the queue runs dry."""

    def __init__(self, *raws: dict[str, Any]) -> None:
        self.raws = list(raws)
        self.asked: dict[str, int] = {}

    def answer(self, call_class: str) -> dict[str, Any] | None:
        if call_class not in V3_CALLS:
            return None
        if call_class == "pitch":
            # A new pitch starts a new draft: every stage moves to the same draft.
            at = max(self.asked.values(), default=0)
            self.asked = dict.fromkeys(dr.STAGES, at)
            return card_for(self.raws[min(at, len(self.raws) - 1)])
        stage = call_class.removeprefix("draft_")
        n = self.asked.get(stage, 0)
        self.asked[stage] = n + 1
        return stages_of(self.raws[min(n, len(self.raws) - 1)])[stage]


__all__ = ["DATES", "V3_CALLS", "Drafts", "card_for", "stages_of"]
