"""The staged world draft with partial retry (spec §5.2). Phase 2 unit.

After the host confirms a start point, the world is drafted in stages (frame,
contests and schedule, people and roster), and a refused stage is retried on
its own instead of redrawing the whole file.
"""

import copy
import json
import logging
import re
from collections.abc import Callable, Sequence
from typing import Any

from pydantic import ValidationError

from revisionist.core.events import CallCost
from revisionist.core.ids import CampaignId, derived_id
from revisionist.core.settings import Settings
from revisionist.core.world import WorldFile
from revisionist.core.world_compile import WorldError, check_history, compile_world
from revisionist.engine import campaign as cmp
from revisionist.engine.llm import RESOLVER_MODEL, Request, Transport, prompt_text
from revisionist.engine.pitch import SettingCard, StartPoint
from revisionist.engine.session_zero import TRIES, SessionZeroError

log = logging.getLogger(__name__)

STAGES = ("outline", "detail", "schedule")
FIELDS = {
    "outline": ("world", "name", "entities", "contests", "start", "start_date"),
    "detail": (
        "assets",
        "relationships",
        "meters",
        "clocks",
        "facts",
        "commitments",
        "seats",
        "roster",
        "notes",
    ),
    "schedule": ("schedule",),
}
PROMPT = {"outline": "outline", "detail": "draft_detail", "schedule": "draft_schedule"}
OUTLINE_HEADS = frozenset(
    {"contest", "contests", "entity", "entities", "places", "world", "name", "start", "start_date"}
)
SCHEDULE_HEADS = frozenset({"history", "schedule"})


def _refs(node: Any) -> set[str]:
    if isinstance(node, dict):
        found = {node["$ref"].split("/")[-1]} if "$ref" in node else set()
        return found.union(*(_refs(v) for v in node.values()))
    if isinstance(node, list):
        return set().union(*(_refs(v) for v in node))
    return set()


def _strip_discriminator(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _strip_discriminator(v) for k, v in node.items() if k != "discriminator"}
    if isinstance(node, list):
        return [_strip_discriminator(v) for v in node]
    return node


def stage_schema(fields: Sequence[str]) -> dict[str, Any]:
    """The world file's schema restricted to one stage, with only the ``$defs`` it reaches."""
    schema = WorldFile.model_json_schema()
    props = {k: v for k, v in schema["properties"].items() if k in fields}
    defs = schema.get("$defs", {})
    needed: set[str] = set()
    todo = list(_refs(props))
    while todo:
        name = todo.pop()
        if name not in needed:
            needed.add(name)
            todo.extend(_refs(defs[name]))
    stage = {
        **schema,
        "properties": props,
        "required": [k for k in schema.get("required", []) if k in fields],
        "$defs": {k: defs[k] for k in needed},
    }
    restricted: dict[str, Any] = _strip_discriminator(stage)
    return restricted


SCHEMAS = {stage: stage_schema(FIELDS[stage]) for stage in STAGES}


def _fields(output: object, fields: Sequence[str]) -> dict[str, Any]:
    if not isinstance(output, dict):
        return {}
    return {k: v for k, v in output.items() if k in fields}


def _prompt(
    stage: str,
    card: SettingCard,
    start: StartPoint,
    settings: Settings,
    outputs: dict[str, dict[str, Any]],
) -> str:
    if stage == "outline":
        basis = (
            f"## Setting card reading\n{card.reading}\n\n## Start point\n{start.model_dump_json()}"
        )
    elif stage == "detail":
        basis = "## Outline\n" + json.dumps(outputs["outline"])
    else:
        basis = (
            "## Outline\n"
            + json.dumps(outputs["outline"])
            + "\n\n## Start point: what really followed, one line each\n"
            + json.dumps(list(start.schedule))
        )
    return (
        basis
        + "\n\n## Settings (fixed by the table; for tone only)\n"
        + settings.model_dump_json()
        + f"\n\nDraft the {stage}."
    )


def _request(
    stage: str,
    card: SettingCard,
    start: StartPoint,
    settings: Settings,
    outputs: dict[str, dict[str, Any]],
    problems: Sequence[str],
    effort: int | None,
) -> Request:
    prompt = _prompt(stage, card, start, settings, outputs)
    if problems:
        prompt += (
            "\n\n## Your last reply was refused. Fix every problem and return the whole stage.\n"
            + "\n".join(problems)
            + "\n\n## Your previous reply\n"
            + json.dumps(outputs.get(stage, {}))
        )
    return Request(
        call_class=f"draft_{stage}",
        model=RESOLVER_MODEL,
        system=prompt_text(PROMPT[stage]),
        prompt=prompt,
        schema=SCHEMAS[stage],
        effort=effort,
    )


def _stage_of(problem: str) -> str:
    head = re.match(r"\w+", problem)
    word = head.group(0) if head else ""
    if word in OUTLINE_HEADS:
        return "outline"
    if word in SCHEDULE_HEADS:
        return "schedule"
    return "detail"


def _repaired(merged: dict[str, Any]) -> dict[str, Any]:
    """A seat key where an entity key is required stands for that seat's entity."""
    people = {e.get("key") for e in merged.get("entities") or [] if isinstance(e, dict)}
    embodies = {
        s["key"]: s.get("embodies")
        for s in merged.get("seats") or []
        if isinstance(s, dict) and "key" in s
    }

    def entity(key: Any) -> Any:
        if isinstance(key, str) and key not in people and key in embodies:
            return embodies[key]
        return key

    fixed = copy.deepcopy(merged)
    for a in fixed.get("assets") or []:
        if isinstance(a, dict) and "controller" in a:
            a["controller"] = entity(a["controller"])
    for r in fixed.get("roster") or []:
        if isinstance(r, dict) and "entity" in r:
            r["entity"] = entity(r["entity"])
    for c in fixed.get("contests") or []:
        if isinstance(c, dict) and isinstance(c.get("sides"), list):
            c["sides"] = [entity(s) for s in c["sides"]]
    return fixed


def _check(merged: dict[str, Any], settings: Settings) -> tuple[WorldFile | None, list[str]]:
    try:
        world = WorldFile.model_validate(_repaired(merged))
    except ValidationError as exc:
        return None, [
            f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in exc.errors()[:20]
        ]
    world = world.model_copy(update={"settings": settings})
    problems = check_history(world)
    try:
        compile_world(world, CampaignId(derived_id("draft", world.world)), cmp.versions())
    except WorldError as exc:
        problems += list(exc.problems)
    return (world if not problems else None), problems


def draft(
    card: SettingCard,
    start: StartPoint,
    settings: Settings,
    transport: Transport,
    effort: int | None = None,
    *,
    detail_effort: int | None = None,
    progress: Callable[[str], None] | None = None,
) -> tuple[WorldFile, list[CallCost]]:
    """Draft and check a v3 world file for ``start`` (its ``schedule`` already
    has the host's strikes removed). Raises ``SessionZeroError`` if a stage never
    passes the checker. ``progress`` gets one plain line per finished stage."""
    emit = progress or (lambda _line: None)
    outputs: dict[str, dict[str, Any]] = {}
    costs: list[CallCost] = []
    failing: Sequence[str] = STAGES
    notes: dict[str, list[str]] = {}
    problems: list[str] = []
    for attempt in range(TRIES):
        for stage in failing:
            if attempt:
                emit(f"Fixing {stage}...")
            effort_for = effort if stage == "outline" else detail_effort
            request = _request(
                stage, card, start, settings, outputs, notes.get(stage, []), effort_for
            )
            reply = transport.call(request)
            costs.append(reply.cost(request.call_class))
            outputs[stage] = _fields(reply.output, FIELDS[stage])
            if stage == "outline" and attempt == 0:
                emit("Outline done. Drafting assets, roster, schedule...")
        merged: dict[str, Any] = {}
        for stage in STAGES:
            merged |= outputs.get(stage, {})
        emit("Checking the world...")
        world, problems = _check(merged, settings)
        if world is not None:
            return world, costs
        log.info("draft attempt %d refused: %s", attempt + 1, "; ".join(problems))
        notes = {}
        for problem in problems:
            notes.setdefault(_stage_of(problem), []).append(problem)
        failing = tuple(s for s in STAGES if s in notes)
    raise SessionZeroError(problems, costs)
