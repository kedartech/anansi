"""Fixture providers (build plan Layer 4).

Enough to run the orchestrator end to end with no model in the loop. Layer 5
replaces the resolver and narrator with Anthropic adapters behind the same
protocols; Layer 9 replaces the deliverer with Discord. These stay as the
test doubles and the ``--dry-run`` path.
"""

from collections.abc import Callable, Mapping, Sequence

from revisionist.core import delta as d
from revisionist.core.events import CallCost
from revisionist.core.ids import IntentId, SeatId
from revisionist.core.intent import Intent
from revisionist.core.referee import Roll
from revisionist.core.state import State, View
from revisionist.engine.tick import Narration, Proposal, Resolution

Script = Callable[[State, Intent, Roll], Sequence[d.Delta]]


def _zero(call_class: str) -> CallCost:
    return CallCost(
        call_class=call_class, model="fixture", input_tokens=0, output_tokens=0, usd=0.0
    )


class ScriptedResolver:
    """Proposes whatever ``script`` returns per intent. With no script, proposes nothing.

    Reports one zero-cost call per invocation so the budget plumbing is exercised.
    """

    def __init__(self, script: Script | None = None) -> None:
        self.script = script
        self.calls = 0

    def propose(
        self, state: State, intents: Sequence[Intent], rolls: Mapping[IntentId, Roll]
    ) -> Resolution:
        self.calls += 1
        proposals = tuple(
            Proposal(
                intent_id=i.intent_id,
                deltas=tuple(self.script(state, i, rolls[i.intent_id])) if self.script else (),
            )
            for i in intents
        )
        return Resolution(proposals=proposals, costs=(_zero("resolver"),))


class FailingResolver:
    """Raises every time. Produces quiet ticks."""

    def __init__(self, message: str = "model unavailable") -> None:
        self.message = message
        self.calls = 0

    def propose(
        self, state: State, intents: Sequence[Intent], rolls: Mapping[IntentId, Roll]
    ) -> Resolution:
        self.calls += 1
        raise RuntimeError(self.message)


class TemplateNarrator:
    """Deterministic prose: one line per visible delta, written from the view only."""

    def narrate(self, view: View, deltas: Sequence[d.Delta]) -> Narration:
        if not deltas:
            text = f"Tick {view.tick}: a quiet week for {view.me.name}."
        else:
            lines = [f"Tick {view.tick}, as seen by {view.me.name}:"]
            lines += [f"- {x.kind}: {x.reason or 'no reason given'}" for x in deltas]
            text = "\n".join(lines)
        return Narration(text=text, cost=_zero("narrator"))


class RecordingDeliverer:
    """Keeps every delivery in memory. The test double for Discord."""

    def __init__(self) -> None:
        self.sent: list[tuple[SeatId, str, str]] = []

    def deliver(self, seat_id: SeatId, text: str, idempotency_key: str) -> str:
        self.sent.append((seat_id, text, idempotency_key))
        return "memory"


class KeywordSafety:
    """Hard-line keyword classifier (spec §17). Real classifiers arrive in Layer 10.

    Refuses an intent whose free text (``spin``, ``condition``) contains any word
    from ``state.settings.lines`` or the extra words given here.
    """

    def __init__(self, extra: Sequence[str] = ()) -> None:
        self.extra = tuple(w.lower() for w in extra)

    def __call__(self, state: State, intent: Intent, /) -> str | None:
        text = " ".join(filter(None, (intent.spin, intent.condition))).lower()
        if not text:
            return None
        for word in (*state.settings.lines, *self.extra):
            if word.lower() in text:
                return f"line: {word}"
        return None


__all__ = [
    "FailingResolver",
    "KeywordSafety",
    "RecordingDeliverer",
    "Script",
    "ScriptedResolver",
    "TemplateNarrator",
]
