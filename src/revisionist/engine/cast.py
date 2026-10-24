"""The generated cast (realism, Batch 5): who stands in the world at a start point.

After the host picks a start point from the setting card, one cheap call (the
aide's model, call class ``cast``) names the people a player could be and the
polities and bodies they belong to. Code checks it and compiles it as a realism
world with a roster and no cards: each card is built when its person is picked
(``engine.build``). Nothing is written until the cast compiles.
"""

import json
from typing import Any

from pydantic import ValidationError

from revisionist.core.events import CallCost
from revisionist.core.ids import CampaignId, derived_id
from revisionist.core.ontology import EntityKind
from revisionist.core.world import WorldFile
from revisionist.core.world_compile import WorldError, compile_world
from revisionist.engine import campaign as cmp
from revisionist.engine.aide import AIDE_MODEL
from revisionist.engine.draft import stage_schema
from revisionist.engine.llm import Request, Transport, prompt_text
from revisionist.engine.pitch import SettingCard, StartPoint
from revisionist.engine.session_zero import TRIES, SessionZeroError

CAST_MODEL = AIDE_MODEL
"""[proposed] ``REVISIONIST_MODEL_CAST`` overrides it per call."""
CAST_MIN, CAST_MAX = 4, 8
"""[proposed] People on a generated roster."""
FIELDS = ("name", "entities", "roster")


def _schema() -> dict[str, Any]:
    """Name, entities and roster; a cast has no assets or contests to point at."""
    schema = stage_schema(FIELDS)
    props = schema["$defs"]["CharacterDecl"]["properties"]
    for name in ("means", "sides"):
        props.pop(name, None)
    schema["$defs"].pop("SideDecl", None)
    return schema


CAST_SCHEMA = _schema()


def cast_prompt(card: SettingCard, start: StartPoint) -> str:
    return (
        f"## How the pitch was read\n{card.reading}\n\n"
        f"## Start point\n{start.label}, {start.start_date.isoformat()}\n"
        f"In focus: {start.focus}\n"
        f"Powers: {json.dumps(list(start.powers), ensure_ascii=False)}\n"
        f"Contests: {json.dumps(list(start.contests), ensure_ascii=False)}\n\n"
        f"## Named in the pitch\n{json.dumps(list(card.named), ensure_ascii=False)}\n\n"
        "Name the cast."
    )


def _request(
    card: SettingCard, start: StartPoint, problems: list[str], previous: object
) -> Request:
    prompt = cast_prompt(card, start)
    if problems:
        prompt += (
            "\n\n## Your last cast was refused. Fix every problem and return the whole cast.\n"
            + "\n".join(problems)
            + "\n\n## Your previous reply\n"
            + json.dumps(previous)
        )
    return Request(
        call_class="cast",
        model=CAST_MODEL,
        system=prompt_text("cast"),
        prompt=prompt,
        schema=CAST_SCHEMA,
        max_usd=0.20,
    )


def _world(output: object, start: StartPoint) -> tuple[WorldFile | None, list[str]]:
    fields = {k: v for k, v in output.items() if k in FIELDS} if isinstance(output, dict) else {}
    slug = "cast-" + derived_id("cast", start.label, start.start_date.isoformat()).lower()[:10]
    try:
        world = WorldFile.model_validate(
            {
                **fields,
                "world": slug,
                "ruleset": "realism",
                "start": start.label[:80],
                "start_date": start.start_date,
            }
        )
    except ValidationError as exc:
        return None, [
            f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in exc.errors()[:20]
        ]
    problems: list[str] = []
    if not CAST_MIN <= len(world.roster) <= CAST_MAX:
        problems.append(f"roster: {len(world.roster)} people, not {CAST_MIN}-{CAST_MAX}")
    for d in world.entities:
        if "_" in d.name:
            problems.append(f"entity {d.key}: a name is written as a person would write it")
    for c in world.roster:
        if "_" in c.pitch + c.reach:
            problems.append(f"character {c.entity}: pitch and reach are plain words")
    kinds = {d.key: d.kind for d in world.entities}
    for c in world.roster:
        parent = next((d.parent for d in world.entities if d.key == c.entity), None)
        if parent is None or kinds.get(parent) not in (EntityKind.POLITY, EntityKind.ORG):
            problems.append(f"character {c.entity}: needs a parent polity or body")
    try:
        compile_world(world, CampaignId(derived_id("cast", slug)), cmp.versions())
    except WorldError as exc:
        problems += list(exc.problems)
    return (world if not problems else None), problems


def draft_cast(
    card: SettingCard, start: StartPoint, transport: Transport
) -> tuple[WorldFile, list[CallCost]]:
    """The cast for ``start`` as a card-less realism world, retried once with the
    problems. Raises ``SessionZeroError``; ``TransportError`` passes through."""
    costs: list[CallCost] = []
    problems: list[str] = []
    previous: object = {}
    for _ in range(TRIES):
        reply = transport.call(_request(card, start, problems, previous))
        costs.append(reply.cost("cast"))
        previous = reply.output
        world, problems = _world(reply.output, start)
        if world is not None:
            return world, costs
    raise SessionZeroError(problems, costs)


__all__ = ["CAST_MODEL", "CAST_SCHEMA", "cast_prompt", "draft_cast"]
