"""The staged world draft (Session Zero v3, spec §5.8): three scripted stages, no network."""

import copy
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml

from revisionist.core.events import CallCost
from revisionist.core.ids import CampaignId, derived_id
from revisionist.core.settings import Settings
from revisionist.core.world_compile import compile_world
from revisionist.engine import campaign as cmp
from revisionist.engine import draft as dr
from revisionist.engine import session_zero as sz
from revisionist.engine.llm import Reply, Request
from revisionist.engine.pitch import SettingCard, StartPoint

FLORENCE = Path(__file__).resolve().parent / "drafts" / "florence-1478.yaml"
SETTINGS = Settings(tone="grim")
START = StartPoint(
    label="Florence, April 1478",
    start_date=date(1478, 4, 19),
    contests=("The Medici and the Papacy",),
    powers=("florence", "papacy"),
    focus="Florence",
    schedule=(
        "26 April 1478: the Pazzi strike Lorenzo and Giuliano in the cathedral",
        "28 April 1478: the Signoria holds the palace",
    ),
)
CARD = SettingCard(
    reading="The Pazzi conspiracy, in the week before 26 April 1478.", start_points=(START,)
)
FEUD = "The Medici and the Papacy"
SIDES = {
    "lorenzo": "florence",
    "giuliano": "florence",
    "lucrezia": "florence",
    "jacopo_pazzi": "florence",
    "bandini": "florence",
    "petrucci": "florence",
    "salviati": "papacy",
    "riario": "papacy",
    "montesecco": "papacy",
}
# The character rule: each roster person must hold a tier 2+ force, office or
# network in the contest, so the florence draft's tier-1 blades are raised and
# the two people with only a bank or ledgers are left off the roster.
RAISED = {"giuliano_household", "pazzi_retainers", "bandini_blade"}
OFF_ROSTER = {"francesco_pazzi", "clerk"}
PAZZI_STRIKE = {
    "key": "pazzi_strike",
    "name": "The Pazzi strike Lorenzo and Giuliano in the cathedral",
    "on_date": "1478-04-26",
    "condition": {"check": "entity_is", "entity": "lorenzo", "status": "active"},
    "weight": 3,
}
PALACE = {
    "key": "palace_holds",
    "name": "The Signoria holds the palace",
    "on_date": "1478-04-28",
    "condition": {"check": "entity_is", "entity": "petrucci", "status": "active"},
    "weight": 2,
}


def _stages() -> dict[str, dict[str, Any]]:
    """The florence draft, split into the three stage replies."""
    raw: dict[str, Any] = yaml.safe_load(FLORENCE.read_text())
    contests = [
        {
            "key": "feud",
            "kind": "rivalry",
            "name": FEUD,
            "prize": "Florence's bank and its papal money",
            "sides": ["florence", "papacy"],
            "momentum": -4,
            "countdown_days": 120,
        }
    ]
    for a in raw["assets"]:
        if a["key"] in RAISED:
            a["tier"] = 2
    raw["roster"] = [c for c in raw["roster"] if c["entity"] not in OFF_ROSTER]
    for c in raw["roster"]:
        if c["entity"] in SIDES:
            c["sides"] = [{"contest": "feud", "side": SIDES[c["entity"]]}]
    outline = {
        "world": "florence-1478",
        "name": raw["name"],
        "entities": raw["entities"],
        "contests": contests,
        "start": "Florence, April 1478",
        "start_date": "1478-04-19",
    }
    detail = {
        k: raw[k]
        for k in (
            "assets",
            "relationships",
            "meters",
            "clocks",
            "facts",
            "seats",
            "roster",
            "notes",
        )
    }
    return {"outline": outline, "detail": detail, "schedule": {"schedule": [PAZZI_STRIKE, PALACE]}}


class Scripted:
    """Answers each stage from its own queue, by ``call_class``."""

    def __init__(self, **replies: list[Any]) -> None:
        self.replies = replies
        self.requests: list[Request] = []

    def call(self, request: Request) -> Reply:
        self.requests.append(request)
        output = self.replies[request.call_class].pop(0)
        return Reply(output, request.model, 3000, 2000, 0, 0.05)

    def classes(self) -> list[str]:
        return [r.call_class for r in self.requests]


def _good(**overrides: list[Any]) -> Scripted:
    stages = _stages()
    queues: dict[str, list[Any]] = {
        "draft_outline": [stages["outline"]],
        "draft_detail": [stages["detail"]],
        "draft_schedule": [stages["schedule"]],
    }
    return Scripted(**(queues | overrides))


def test_a_staged_draft_compiles_with_contests_and_history() -> None:
    transport = _good()
    world, costs = dr.draft(CARD, START, SETTINGS, transport, 2, detail_effort=3)
    assert transport.classes() == ["draft_outline", "draft_detail", "draft_schedule"]
    assert [r.effort for r in transport.requests] == [2, 3, 3]
    assert [c.call_class for c in costs] == transport.classes()
    assert world.settings == SETTINGS
    assert world.start_date == date(1478, 4, 19)
    assert [c.key for c in world.contests] == ["feud"]
    assert [h.key for h in world.schedule] == ["pazzi_strike", "palace_holds"]
    campaign = CampaignId(derived_id("draft", world.world))
    assert compile_world(world, campaign, cmp.versions())


def test_progress_lines_follow_the_stages() -> None:
    progress: list[str] = []
    dr.draft(CARD, START, SETTINGS, _good(), progress=progress.append)
    assert progress == [
        "Outline done. Drafting assets, roster, schedule...",
        "Checking the world...",
    ]


def test_a_schedule_problem_retries_only_the_schedule() -> None:
    stages = _stages()
    early = copy.deepcopy(PAZZI_STRIKE) | {"on_date": "1478-04-01"}
    transport = _good(draft_schedule=[{"schedule": [early, PALACE]}, stages["schedule"]])
    progress: list[str] = []
    world, costs = dr.draft(CARD, START, SETTINGS, transport, progress=progress.append)
    assert transport.classes() == [
        "draft_outline",
        "draft_detail",
        "draft_schedule",
        "draft_schedule",
    ]
    assert len(costs) == 4
    retry = transport.requests[-1].prompt
    assert "history pazzi_strike: dated before the start date" in retry
    assert json.dumps({"schedule": [early, PALACE]}["schedule"][0]) in retry
    assert progress == [
        "Outline done. Drafting assets, roster, schedule...",
        "Checking the world...",
        "Fixing schedule...",
        "Checking the world...",
    ]
    assert [h.key for h in world.schedule] == ["pazzi_strike", "palace_holds"]


def test_a_seat_key_for_an_entity_is_repaired_without_a_retry() -> None:
    stages = _stages()
    outline = copy.deepcopy(stages["outline"])
    outline["contests"][0]["sides"] = ["florence", "papacy_seat"]
    transport = _good(draft_outline=[outline])
    world, _ = dr.draft(CARD, START, SETTINGS, transport)
    assert transport.classes() == ["draft_outline", "draft_detail", "draft_schedule"]
    assert world.contests[0].sides == ("florence", "papacy")


def test_failure_after_tries_raises_with_every_cost() -> None:
    early = copy.deepcopy(PAZZI_STRIKE) | {"on_date": "1478-04-01"}
    bad = {"schedule": [early, PALACE]}
    transport = _good(draft_schedule=[bad, bad])
    with pytest.raises(sz.SessionZeroError) as info:
        dr.draft(CARD, START, SETTINGS, transport)
    assert sz.TRIES == 2
    assert [c.call_class for c in info.value.costs] == [
        "draft_outline",
        "draft_detail",
        "draft_schedule",
        "draft_schedule",
    ]
    assert all(isinstance(c, CallCost) for c in info.value.costs)
    assert "history pazzi_strike: dated before the start date" in info.value.problems


def test_stage_schemas_carry_no_discriminator_and_only_their_fields() -> None:
    transport = _good()
    dr.draft(CARD, START, SETTINGS, transport)
    outline, detail, schedule = (r.schema for r in transport.requests)
    for schema in (outline, detail, schedule):
        assert "discriminator" not in json.dumps(schema)
    assert {"contests", "start_date", "entities"} <= set(outline["properties"])
    assert "contests" not in detail["properties"] and "roster" in detail["properties"]
    assert set(schedule["properties"]) == {"schedule"}
    assert "HistoryDecl" in schedule["$defs"] and "ContestDecl" not in schedule["$defs"]
