"""Historical Session Zero (Layer 10 item 1; README §5).

``establish`` locks a point in history: the GM drafts the world bible from the
record at that date, the Layer 7 compiler validates it, and only a draft that
compiles is ever written to the log. The settings are the table's, never the
model's.
"""

from collections.abc import Sequence

from pydantic import ValidationError

from revisionist.core.events import CallCost
from revisionist.core.ids import CampaignId, derived_id
from revisionist.core.settings import Settings
from revisionist.core.world import WorldFile
from revisionist.core.world_compile import WorldError, check_history, compile_world
from revisionist.engine import campaign as cmp
from revisionist.engine.ids import new_id
from revisionist.engine.llm import RESOLVER_MODEL, Request, Transport, prompt_text
from revisionist.engine.store import EventStore, Sealer

SESSION_ZERO_MODEL = RESOLVER_MODEL
"""Session Zero runs on the frontier model, like the resolver (README §20)."""

DRAFT_SCHEMA = WorldFile.model_json_schema()
PLACE_MAX = 80
TRIES = 2
"""One draft and one retry with the compiler's problems fed back."""


class SessionZeroError(RuntimeError):
    """The draft never compiled. Nothing was written."""

    def __init__(self, problems: Sequence[str], costs: Sequence[CallCost]) -> None:
        self.problems = tuple(problems)
        self.costs = tuple(costs)
        super().__init__("the world draft did not compile:\n" + "\n".join(self.problems))


def _slug(place: str) -> str:
    return "history-" + derived_id("establish", place).lower()[:10]


def _request(place: str, settings: Settings, problems: Sequence[str]) -> Request:
    prompt = (
        f"## Place and date\n{place}\n\n"
        "## Settings (fixed by the table; for tone only)\n"
        + settings.model_dump_json()
        + "\n\nDraft the world file."
    )
    if problems:
        prompt += (
            "\n\n## Your last draft was refused. Fix every problem and return the whole file.\n"
            + "\n".join(problems)
        )
    return Request(
        call_class="session_zero",
        model=SESSION_ZERO_MODEL,
        system=prompt_text("establish"),
        prompt=prompt,
        schema=DRAFT_SCHEMA,
        max_usd=2.0,
    )


def _check(output: object, place: str, settings: Settings) -> tuple[WorldFile | None, list[str]]:
    if not isinstance(output, dict):
        return None, ["the reply was not a world file"]
    try:
        draft = WorldFile.model_validate(output)
    except ValidationError as exc:
        return None, [
            f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in exc.errors()[:20]
        ]
    world = draft.model_copy(update={"world": _slug(place), "start": place, "settings": settings})
    problems = check_history(world)
    try:
        compile_world(world, CampaignId("0" * 26), cmp.versions())
    except WorldError as exc:
        problems += list(exc.problems)
    return (world if not problems else None), problems


def draft_world(
    place: str, settings: Settings, transport: Transport
) -> tuple[WorldFile, list[CallCost]]:
    """Ask for the world bible, check it, and retry once with the problems."""
    place = place.strip()
    if not place or len(place) > PLACE_MAX:
        raise SessionZeroError([f"a place and date of 1-{PLACE_MAX} characters"], [])
    costs: list[CallCost] = []
    problems: list[str] = []
    for _ in range(TRIES):
        reply = transport.call(_request(place, settings, problems))
        costs.append(reply.cost("session_zero"))
        world, problems = _check(reply.output, place, settings)
        if world is not None:
            return world, costs
    raise SessionZeroError(problems, costs)


def establish(
    store: EventStore, place: str, settings: Settings, transport: Transport
) -> CampaignId:
    """Draft, compile and open a new campaign at ``place``. Its first tick waits for
    the character, goal and brief steps."""
    world, costs = draft_world(place, settings, transport)
    campaign_id = CampaignId(new_id())
    sealer: Sealer | None = getattr(store, "sealer", None)
    if sealer is not None:
        sealer.begin(campaign_id)
    store.append_all(compile_world(world, campaign_id, cmp.versions()))
    cmp.log_cost(store, campaign_id, "session_zero", costs)
    return campaign_id


__all__ = [
    "DRAFT_SCHEMA",
    "SESSION_ZERO_MODEL",
    "SessionZeroError",
    "draft_world",
    "establish",
]
