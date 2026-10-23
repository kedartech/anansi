"""Hand-written recordings for the Phase 4 model paths. No model wrote these.

Each fixture in ``tests/recordings/realism-*.json`` is in ``RecordedTransport``'s
format, built by running the fixed scenario against ``Hand``, which answers each
request from the replies below (written by hand for the page or question asked)
and marks the entry ``hand_written``. Regenerate after changing a prompt:

    uv run --frozen python -m tests.handwritten
"""

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from revisionist.engine.llm import Reply, Request

RECORDINGS = Path(__file__).parent / "recordings"
PROSE = RECORDINGS / "realism-prose-toy.json"
NOTE = "hand-written for tests; not a model reply"

PROSE_BY_TITLE: Mapping[str, str] = {
    "A note from Bram Tull": (
        "Madam,\n\nI have gone along the benches twice before writing. I make it 2 members "
        "wavering, and I would not put it higher or lower until I have spoken to each."
        "\n\nYour servant,\nBram Tull"
    ),
    "Bram's count of the Council, 1900-04-02": (
        "Madam,\n\nMy count of the Council as it stands: 8 votes for the levy. I have counted "
        "twice. I will write again when it moves.\n\nB. Tull"
    ),
    "Bram's count of the Council, 1900-04-09": (
        "Madam,\n\nThe count holds at 8 votes for the levy. Nobody has come over and nobody "
        "has gone off; I give it you as I find it.\n\nB. Tull"
    ),
    "Bram's count of the Council, 1900-04-16": (
        "Madam,\n\nStill 8 votes for the levy by my reckoning. I would sooner send you a dull "
        "count than a hopeful one.\n\nB. Tull"
    ),
    "A note from Cora Senn": (
        "Dear First Citizen,\n\nWe enrolled 3 bills this week, all in good order. As to the "
        "clerks absent, I would rather not set down a figure until the roll is checked."
        "\n\nC. Senn"
    ),
}


def prose_reply(request: Request) -> Any:
    pages = json.loads(request.prompt.split("## Pages to write\n", 1)[1].rsplit("\n\n", 1)[0])
    return {"pages": [{"doc_id": p["doc_id"], "prose": PROSE_BY_TITLE[p["title"]]} for p in pages]}


class Hand:
    """A transport that answers from ``answer`` and keeps each exchange as a recording."""

    def __init__(self, answer: Callable[[Request], Any]) -> None:
        self.answer = answer
        self.entries: dict[str, dict[str, Any]] = {}

    def call(self, request: Request) -> Reply:
        output = self.answer(request)
        tokens_in = len(request.system + request.prompt) // 4
        tokens_out = len(json.dumps(output)) // 4
        reply = Reply(
            output=output,
            model=request.model,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            cached_tokens=len(request.system) // 4,
            usd=round(tokens_in * 3e-6 + tokens_out * 15e-6, 6),  # an estimate at list price
        )
        self.entries[request.key()] = {
            "call_class": request.call_class,
            "model": request.model,
            "prompt_head": request.prompt[:120],
            "hand_written": True,
            "note": NOTE,
            "reply": reply.__dict__,
        }
        return reply

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.entries, indent=1, sort_keys=True) + "\n")


def record_prose() -> None:
    from revisionist.engine.prose import ClaudeProseWriter
    from revisionist.engine.realism_tick import run_realism_tick
    from revisionist.engine.store import MemoryStore
    from tests.prose_scenario import scenario
    from tests.test_realism_tick import CAMPAIGN

    hand = Hand(prose_reply)
    store = MemoryStore()
    scenario(store)
    run_realism_tick(store, CAMPAIGN, prose=ClaudeProseWriter(hand))
    hand.save(PROSE)


def main() -> None:
    record_prose()


if __name__ == "__main__":
    main()
