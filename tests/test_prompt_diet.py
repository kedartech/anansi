"""The resolver and narrator prompts carry state, not the Session Zero roster or nulls."""

from revisionist.core import delta as d
from revisionist.core.fold import replay
from revisionist.core.ids import DeltaId, IntentId
from revisionist.core.intent import Intent, Verb
from revisionist.core.ontology import Character
from revisionist.core.project import project
from revisionist.core.referee import Roll, rate
from revisionist.core.resolution import OutcomeTier
from revisionist.core.state import State
from revisionist.core.visibility import PUBLIC
from revisionist.engine import llm
from tests import fixtures as fx

STATE_EXCLUDE = {"pending", "frozen", "last_seq", "goals"}


def _state_with_roster() -> State:
    state = replay(fx.creation_events())
    ines = Character(
        entity_id=fx.E_NORTH_AIDE,
        pitch="A courier who knows every ford on the river.",
        reach="Two river towns",
        composite=True,
    )
    return state.model_copy(update={"roster": {ines.entity_id: ines}})


def _orders(state: State) -> tuple[list[Intent], dict[IntentId, Roll]]:
    intents = [
        Intent(
            intent_id=IntentId(f"01ARZ3NDEKTSV4RRFFQ69T{n:05d}"),
            actor=fx.NORTH,
            verb=verb,
            scope=PUBLIC,
            filed_tick=0,
        )
        for n, verb in enumerate((Verb.ACQUIRE, Verb.WAIT), start=1)
    ]
    rolls = {
        i.intent_id: Roll(seed="s", outcome=OutcomeTier.SUCCESS, exposure_roll=10, exposed=False)
        for i in intents
    }
    return intents, rolls


def test_resolver_prompt_drops_roster_and_nulls() -> None:
    state = _state_with_roster()
    intents, rolls = _orders(state)
    prompt = llm._resolver_prompt(state, intents, rolls)
    assert "roster" not in prompt
    assert "null" not in prompt


def test_resolver_prompt_is_shorter_than_the_roster_dump() -> None:
    state = _state_with_roster()
    intents, rolls = _orders(state)
    prompt = llm._resolver_prompt(state, intents, rolls)
    new_dump = state.model_dump_json(exclude=STATE_EXCLUDE | {"roster"}, exclude_none=True)
    old_dump = state.model_dump_json(exclude=STATE_EXCLUDE)
    assert len(prompt) < len(prompt.replace(new_dump, old_dump))


def test_resolver_prompt_keeps_every_intent_and_band() -> None:
    state = _state_with_roster()
    intents, rolls = _orders(state)
    prompt = llm._resolver_prompt(state, intents, rolls)
    for i in intents:
        assert i.intent_id in prompt
        assert f'"band": "{rate(state, i).value}"' in prompt


def test_narrator_prompt_drops_roster_and_nulls() -> None:
    state = _state_with_roster()
    view = project(state, fx.SOUTH)
    prompt = llm._narrator_prompt(view, [_meter_delta()])
    assert "roster" not in prompt
    assert "null" not in prompt


def test_narrator_prompt_is_shorter_than_the_roster_dump() -> None:
    state = _state_with_roster()
    view = project(state, fx.SOUTH)
    assert view.roster
    prompt = llm._narrator_prompt(view, [_meter_delta()])
    new_dump = view.model_dump_json(exclude={"own_pending", "roster"}, exclude_none=True)
    old_dump = view.model_dump_json(exclude={"own_pending"})
    assert len(prompt) < len(prompt.replace(new_dump, old_dump))


def test_narrator_prompt_keeps_seat_name_and_deltas() -> None:
    state = _state_with_roster()
    view = project(state, fx.SOUTH)
    prompt = llm._narrator_prompt(view, [_meter_delta()])
    assert view.me.name in prompt
    assert "harvest fails" in prompt


def test_narrator_rules_drop_the_quiet_week_filler() -> None:
    text = llm.prompt_text("narrator")
    assert "say the week was quiet" not in text
    assert (
        "If the list of changes is empty, say so in one short sentence, then spend the briefing "
        "on the most pressing open clock, threat or relationship the seat knows of."
    ) in text
    assert "Never open with 'the week was quiet' or similar filler." in text


def _meter_delta() -> d.Delta:
    return d.MeterDelta(
        delta_id=DeltaId("01ARZ3NDEKTSV4RRFFQ69D00001"),
        reason="harvest fails",
        meter_id=fx.M_NORTH_STABILITY,
        change=-2,
    )
