"""Figures built after they are picked (realism, Batch 5).

A generated cast opens with a roster and no cards. Picking a person builds their
card: one call (the prose model, call class ``build``) drafts it with the
quantities, feeds and pipelines it needs and any new people it reaches, from
public facts only: the setting, the person's roster entry and the names already
in the world. Code checks it with ``compile_figure`` and lands it in one batch.
Once play begins the people nobody picked are built the same way in the
background (``build_rest``), so code can run them from their cards.
"""

import json
import logging
import re
import threading
import unicodedata

from pydantic import ValidationError

from revisionist.core import events as ev
from revisionist.core.events import CallCost
from revisionist.core.ids import CampaignId, EntityId
from revisionist.core.state import State
from revisionist.core.world import WorldFile
from revisionist.core.world_compile import WorldError, compile_figure
from revisionist.engine import campaign as cmp
from revisionist.engine.draft import stage_schema
from revisionist.engine.llm import Request, Transport, TransportError, prompt_text
from revisionist.engine.prose import PROSE_MODEL
from revisionist.engine.session_zero import TRIES
from revisionist.engine.store import EventStore, StoreError, load_state

log = logging.getLogger(__name__)

BUILD_MODEL = PROSE_MODEL
"""[proposed] ``REVISIONIST_MODEL_BUILD`` overrides it per call."""
LEVERS_MIN, LEVERS_MAX = 2, 6
FIELDS = ("entities", "quantities", "feeds", "pipelines", "cards")
BUILD_SCHEMA = stage_schema(FIELDS)
APPEND_TRIES = 3
"""Other writers may land between our read and our append; reload and recompile."""

_locks: dict[CampaignId, threading.Lock] = {}
_guard = threading.Lock()


def _lock(campaign_id: CampaignId) -> threading.Lock:
    """One builder appends to a campaign at a time in this process."""
    with _guard:
        return _locks.setdefault(campaign_id, threading.Lock())


class BuildError(RuntimeError):
    """The card never passed the checks. Nothing was written but the cost."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = tuple(problems)
        super().__init__("the card did not compile:\n" + "\n".join(self.problems))


def _key(name: str) -> str:
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    key = re.sub(r"[^a-z0-9]+", "_", plain).strip("_")[:36]
    return key if key[:1].isalpha() else f"x_{key}"[:36]


def known_keys(state: State) -> dict[str, EntityId]:
    """A key for everything already in the world, from its name: what the builder
    may refer to. Stable for one state: same names, same keys."""
    out: dict[str, EntityId] = {}
    for e in sorted(state.entities.values(), key=lambda x: (x.name, x.entity_id)):
        key, n = _key(e.name), 2
        while key in out:
            key, n = f"{_key(e.name)[:33]}_{n}", n + 1
        out[key] = e.entity_id
    return out


def build_prompt(
    state: State, world_name: str, figure: EntityId, known: dict[str, EntityId]
) -> str:
    """Public facts only: no other card's policy, no quantity's value."""
    by_id = {e: k for k, e in known.items()}
    people = [
        {
            "key": k,
            "name": state.entities[e].name,
            "kind": state.entities[e].kind.value,
            **({"parent": by_id[p]} if (p := state.entities[e].parent) in by_id else {}),
        }
        for k, e in known.items()
    ]
    ch = state.roster.get(figure)
    entry = f"{ch.pitch} Reach: {ch.reach}" if ch is not None else ""
    today = state.in_world_date.isoformat() if state.in_world_date else ""
    return (
        f"## The world\n{world_name}, {today}\n\n"
        f"## Already in the world\n{json.dumps(people, ensure_ascii=False)}\n\n"
        f"## The person\n{by_id[figure]}: {state.entities[figure].name}\n{entry}\n\n"
        "Build this person's card."
    )


def _request(prompt: str, problems: list[str], previous: object) -> Request:
    if problems:
        prompt += (
            "\n\n## Your last card was refused. Fix every problem and return the whole card.\n"
            + "\n".join(problems)
            + "\n\n## Your previous reply\n"
            + json.dumps(previous)
        )
    return Request(
        call_class="build",
        model=BUILD_MODEL,
        system=prompt_text("build"),
        prompt=prompt,
        schema=BUILD_SCHEMA,
        max_usd=0.50,
    )


def _part(output: object, playable: bool) -> tuple[WorldFile | None, list[str]]:
    fields = {k: v for k, v in output.items() if k in FIELDS} if isinstance(output, dict) else {}
    try:
        part = WorldFile.model_validate(
            {**fields, "world": "figure", "name": "figure", "ruleset": "realism"}
        )
    except ValidationError as exc:
        return None, [
            f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in exc.errors()[:20]
        ]
    problems = [
        f"card {c.person}: {len(c.levers)} levers, not {LEVERS_MIN}-{LEVERS_MAX}"
        for c in part.cards
        if not LEVERS_MIN <= len(c.levers) <= LEVERS_MAX
    ]
    cards = tuple(c.model_copy(update={"playable": playable}) for c in part.cards)
    return part.model_copy(update={"cards": cards}), problems


def _world_name(store: EventStore, campaign_id: CampaignId) -> str:
    for e in store.read(campaign_id):
        if isinstance(e.payload, ev.CampaignCreated):
            return e.payload.name
    return ""


def build_figure(
    store: EventStore, campaign_id: CampaignId, figure: EntityId, transport: Transport
) -> bool:
    """Build and land ``figure``'s card. False when it already has one (built by
    someone else meanwhile). Raises ``BuildError``; ``TransportError`` passes through."""
    state = load_state(store, campaign_id)
    if figure in state.cards:
        return False
    known = known_keys(state)
    prompt = build_prompt(state, _world_name(store, campaign_id), figure, known)
    playable = figure in state.roster
    costs: list[CallCost] = []
    problems: list[str] = []
    previous: object = {}
    part: WorldFile | None = None
    try:
        for _ in range(TRIES):
            reply = transport.call(_request(prompt, problems, previous))
            costs.append(reply.cost("build"))
            previous = reply.output
            part, problems = _part(reply.output, playable)
            if part is not None and not problems:
                try:
                    compile_figure(state, figure, part, known, cmp.versions())
                except WorldError as exc:
                    problems = list(exc.problems)
            if not problems:
                break
        if part is None or problems:
            raise BuildError(problems)
        with _lock(campaign_id):
            for attempt in range(APPEND_TRIES):
                state = load_state(store, campaign_id)
                if figure in state.cards:
                    return False
                try:
                    events = compile_figure(state, figure, part, known, cmp.versions())
                except WorldError as exc:
                    raise BuildError(list(exc.problems)) from exc
                try:
                    store.append_all(events)
                    return True
                except StoreError:
                    if attempt == APPEND_TRIES - 1:
                        raise
        raise AssertionError("unreachable")
    finally:
        cmp.log_cost(store, campaign_id, "build", costs)


def build_rest(store: EventStore, campaign_id: CampaignId, transport: Transport) -> list[str]:
    """Build every roster person still without a card, in name order. Returns the
    names that failed; each failure is logged and the rest carry on."""
    state = load_state(store, campaign_id)
    waiting = sorted(
        (c for c in state.roster if c not in state.cards), key=lambda c: state.entities[c].name
    )
    failed: list[str] = []
    for figure in waiting:
        name = state.entities[figure].name
        try:
            build_figure(store, campaign_id, figure, transport)
        except (BuildError, TransportError, StoreError):
            log.exception("could not build %s in %s", name, campaign_id)
            failed.append(name)
    return failed


__all__ = [
    "BUILD_MODEL",
    "BUILD_SCHEMA",
    "BuildError",
    "build_figure",
    "build_prompt",
    "build_rest",
    "known_keys",
]
