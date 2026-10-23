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
COUNSEL = RECORDINGS / "realism-counsel-autumn.json"
AIDE = RECORDINGS / "realism-aide-autumn.json"
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


COUNSEL_BY_QUESTION: Mapping[str, str] = {
    "What does Moscow need from us most, Harry?": (
        "Mr. President, going by the list as Harriman sent it, tanks come first: they ask "
        "for 500 a month, and 400 aircraft a month behind that. That is what they asked "
        "for, mind you, not what they will get. I can't tell you yet what we can ship "
        "against it; that is a question for the Army and for the British, who will want "
        "their share. My advice is to tell Stalin something definite soon, even if it is "
        "less than the list."
    ),
    "What does Moscow need from us most?": (
        "Mr. President, I have only their letter of request so far, and it gives no figures "
        "I would put in front of you. From what I saw in Moscow they need everything, but "
        "I would not guess at numbers until the full list comes through. When it does, I "
        "will bring it to you myself."
    ),
}


def counsel_reply(request: Request) -> Any:
    question = request.prompt.rsplit("\n", 1)[1]
    return {"text": COUNSEL_BY_QUESTION[question]}


AIDE_REPLY: Mapping[str, Any] = {
    "summary": (
        "Admiral Stark's Atlantic report puts Allied merchant losses to U-boats this month at "
        "209,835, with 30 U-boats operational in the Atlantic. The Speaker counts 267 House "
        "Democrats to 162 Republicans; the Majority Leader gives no figure yet for the Senate "
        "neutrality vote. Gallup has 55 calling your course about right. MAGIC and State have "
        "nothing firm on Japanese oil stocks, and the Prime Minister's cables carry no figures."
    ),
    "drafts": [
        {"lever": "send_bill", "params": {"bill": "neutrality_revision", "urgency": "urgent"}},
        {"lever": "naval_order", "params": {"order": "escort_convoys", "zone": "western_atlantic"}},
        # Out of range on purpose: the aide must drop a draft that does not fit the lever.
        {"lever": "lend_lease_finding", "params": {"recipient": "ussr", "share_pct": 150}},
    ],
}


def aide_reply(request: Request) -> Any:
    return AIDE_REPLY


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


def record_counsel() -> None:
    from revisionist.engine.counsel import ClaudeCounsellor, consult
    from revisionist.engine.store import MemoryStore
    from tests.counsel_scenario import (
        QUESTION,
        WIRING_QUESTION,
        hopkins,
        plant_memo,
        roosevelt_seat,
        ticked,
    )
    from tests.test_realism_tick import CAMPAIGN

    hand = Hand(counsel_reply)
    store = MemoryStore()
    ticked(store)
    seat, person = roosevelt_seat(store), hopkins(store)
    consult(store, CAMPAIGN, seat, person, WIRING_QUESTION, ClaudeCounsellor(hand))
    plant_memo(store)
    consult(store, CAMPAIGN, seat, person, QUESTION, ClaudeCounsellor(hand))
    hand.save(COUNSEL)


def record_aide() -> None:
    from revisionist.engine.aide import ClaudeAide, aide_for
    from revisionist.engine.store import MemoryStore
    from tests.counsel_scenario import roosevelt_seat, ticked
    from tests.test_realism_tick import CAMPAIGN

    hand = Hand(aide_reply)
    store = MemoryStore()
    ticked(store)
    aide_for(store, CAMPAIGN, roosevelt_seat(store), ClaudeAide(hand))
    hand.save(AIDE)


def main() -> None:
    record_prose()
    record_counsel()
    record_aide()


if __name__ == "__main__":
    main()
