"""Layer 5: Claude adapters behind the referee, offline through recorded or fake transports."""

import inspect
import json
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, get_type_hints

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from anansi.core import canary
from anansi.core import delta as d
from anansi.core.events import Versions
from anansi.core.fold import replay, state_hash
from anansi.core.intent import Intent, Verb
from anansi.core.project import project
from anansi.core.state import State, View
from anansi.engine import llm
from anansi.engine.llm import (
    ClaudeCli,
    ClaudeNarrator,
    ClaudeResolver,
    MissingRecordingError,
    RecordedTransport,
    Reply,
    Request,
)
from anansi.engine.providers import KeywordSafety, RecordingDeliverer, TemplateNarrator
from anansi.engine.store import MemoryStore, load_state
from anansi.engine.tick import Narration, Providers, run_tick
from tests import fixtures as fx
from tests.test_tick import _file_some, _intent

RECORDINGS = Path(__file__).parent / "recordings"


class FakeTransport:
    """Returns scripted outputs in order; records every request it saw."""

    def __init__(self, outputs: list[Any]) -> None:
        self.outputs = list(outputs)
        self.requests: list[Request] = []

    def call(self, request: Request) -> Reply:
        self.requests.append(request)
        out = self.outputs.pop(0)
        return Reply(
            output=out,
            model=request.model,
            input_tokens=10,
            output_tokens=5,
            cached_tokens=0,
            usd=0.0,
        )


@pytest.fixture
def state() -> State:
    return replay(fx.creation_events())


def _orders(state: State) -> list[Intent]:
    return [
        _intent(Verb.DECLARE, fx.NORTH, 1, state.tick),
        _intent(Verb.WAIT, fx.GUILD, 3, state.tick),
    ]


def _rolls(state: State, intents: list[Intent]) -> dict[Any, Any]:
    from anansi.engine.tick import _roll

    return {i.intent_id: _roll(state, i) for i in intents}


# --- resolver ------------------------------------------------------------------


def test_resolver_output_without_ids_validates_and_gets_engine_ids(state: State) -> None:
    intents = _orders(state)
    out = {
        "proposals": [
            {
                "intent_id": intents[0].intent_id,
                "deltas": [
                    {"kind": "meter", "meter_id": fx.M_NORTH_STABILITY, "change": 1, "reason": "x"}
                ],
            },
            {"intent_id": intents[1].intent_id, "deltas": []},
        ]
    }
    t = FakeTransport([out])
    res = ClaudeResolver(t).propose(state, intents, _rolls(state, intents))
    assert len(res.proposals) == 2 and len(t.requests) == 1
    delta = res.proposals[0].deltas[0]
    assert isinstance(delta, d.MeterDelta) and len(delta.delta_id) == 26
    assert res.costs[0].call_class == "resolver" and res.costs[0].model == llm.RESOLVER_MODEL
    # truth went in: the covert army is in the resolver prompt
    assert fx.A_NORTH_SECRET_ARMY in t.requests[0].prompt
    assert "delta_id" not in json.dumps(t.requests[0].schema["$defs"]["MeterDelta"]["properties"])


def test_resolver_retries_once_then_drops_the_bad(state: State) -> None:
    intents = _orders(state)
    bad = {
        "proposals": [
            {
                "intent_id": intents[0].intent_id,
                "deltas": [{"kind": "asset_tier", "asset_id": fx.A_NORTH_ARMY, "change": 9}],
            }
        ]
    }
    still_bad = {"proposals": [{"intent_id": "nope", "deltas": []}]}
    t = FakeTransport([bad, still_bad])
    r = ClaudeResolver(t)
    res = r.propose(state, intents, _rolls(state, intents))
    assert len(t.requests) == 2 and "did not validate" in t.requests[1].prompt
    assert res.proposals == () and len(res.costs) == 2
    assert r.dropped and "unknown intent_id" in r.dropped[0][1]


@settings(max_examples=150, deadline=None)
@given(
    junk=st.recursive(
        st.none() | st.booleans() | st.integers() | st.text(max_size=20),
        lambda c: st.lists(c, max_size=4) | st.dictionaries(st.text(max_size=8), c, max_size=4),
        max_leaves=12,
    )
)
def test_resolver_never_raises_on_malformed_output(junk: Any) -> None:
    state = replay(fx.creation_events())
    intents = _orders(state)
    res = ClaudeResolver(FakeTransport([junk, junk])).propose(
        state, intents, _rolls(state, intents)
    )
    for p in res.proposals:
        for x in p.deltas:
            assert isinstance(x, d._Delta)


# --- narrator ----------------------------------------------------------------


def test_narrator_signature_takes_view_not_state() -> None:
    hints = get_type_hints(ClaudeNarrator.narrate)
    assert hints["view"] is View
    params = inspect.signature(ClaudeNarrator.narrate).parameters
    assert "state" not in params


def test_narrator_prompt_carries_only_the_view(state: State) -> None:
    view = project(state, fx.SOUTH)
    t = FakeTransport([{"text": "  A quiet week in the south.  "}])
    n = ClaudeNarrator(t).narrate(view, [])
    assert n.text == "A quiet week in the south."
    assert n.cost is not None and n.cost.call_class == "narrator"
    assert fx.CANARY not in t.requests[0].prompt
    assert fx.A_NORTH_SECRET_ARMY not in t.requests[0].prompt


# --- canary harness -----------------------------------------------------------


def test_canary_scan_catches_a_planted_leak(state: State) -> None:
    hidden = canary.canaries_hidden_from(state, fx.SOUTH)
    assert fx.CANARY in hidden
    assert canary.canaries_hidden_from(state, fx.NORTH) == {}
    assert canary.scan_text(state, fx.SOUTH, f"we hear {fx.CANARY} is afoot") == [
        (fx.CANARY, fx.F_NORTH_PLOT)
    ]
    assert canary.scan_text(state, fx.NORTH, fx.CANARY) == []


def test_canary_scan_over_fifty_fixture_ticks() -> None:
    """Scripted narrator that echoes everything it was given: the projection alone must hold."""
    from anansi.engine.providers import ScriptedResolver
    from anansi.engine.tick import Narration

    class EchoNarrator:
        def narrate(self, view: View, deltas: Sequence[d.Delta]) -> Narration:
            return Narration(
                text=view.model_dump_json()
                + json.dumps([x.model_dump(mode="json") for x in deltas])
            )

    store = MemoryStore()
    store.append_all(fx.creation_events())
    deliverer = RecordingDeliverer()
    providers = Providers(
        resolver=ScriptedResolver(), narrator=EchoNarrator(), deliverer=deliverer, safety=None
    )
    for tick in range(50):
        _file_some(store, tick, 1000 + tick * 10)
        assert not run_tick(store, fx.CAMPAIGN, providers).quiet
    state = load_state(store, fx.CAMPAIGN)
    assert state.tick == 50
    assert canary.scan(state, store.read(fx.CAMPAIGN)) == []
    assert canary.scan_deliveries(state, [(s, t) for s, t, _ in deliverer.sent]) == []
    assert len(deliverer.sent) == 150


# --- recorded transport -------------------------------------------------------


def test_recorded_transport_fails_closed(tmp_path: Path) -> None:
    t = RecordedTransport(tmp_path / "none.json", live=None)
    with pytest.raises(MissingRecordingError):
        t.call(Request("resolver", "m", "s", "p", {}))


def test_recorded_transport_replays_by_request_key(tmp_path: Path) -> None:
    req = Request("narrator", "m", "sys", "prompt", {"type": "object"})
    reply = Reply({"text": "hi"}, "m", 1, 2, 3, 0.0)
    path = tmp_path / "r.json"
    path.write_text(json.dumps({req.key(): {"reply": reply.__dict__}}))
    t = RecordedTransport(path)
    assert t.call(req) == reply and t.hits == 1
    assert Request("narrator", "m", "sys", "prompt!", {"type": "object"}).key() != req.key()


# --- the live tick, recorded -----------------------------------------------------


@pytest.fixture
def recorded() -> Iterator[RecordedTransport]:
    t = RecordedTransport(RECORDINGS / "two-rivers-tick-1.json", live=ClaudeCli())
    yield t
    if t.recorded:
        print(f"recorded {t.recorded} new replies into {t.path}")


class _RecordedOrFixture:
    """Claude for calls the recording has, the fixture narrator otherwise."""

    def __init__(self, recorded: RecordedTransport) -> None:
        self.recorded = recorded
        self.claude = ClaudeNarrator(recorded)
        self.fixture = TemplateNarrator()

    def narrate(self, view: View, deltas: Sequence[d.Delta]) -> Narration:
        try:
            return self.claude.narrate(view, deltas)
        except MissingRecordingError:
            return self.fixture.narrate(view, deltas)


def test_one_live_tick_from_recording(recorded: RecordedTransport) -> None:
    """One real tick of Two Rivers through Claude, replayed from the committed recording.

    Refresh with ``ANANSI_RECORD=1 uv run pytest tests/test_llm.py -k live`` on a
    host where ``claude`` is logged in; review the diff before committing.
    """
    store = MemoryStore()
    store.append_all(fx.creation_events())
    _file_some(store, 0, 1)
    deliverer = RecordingDeliverer()
    resolver = ClaudeResolver(recorded)
    # The recording predates outlet narration, so the outlet falls back to a
    # fixture narrator while the two human briefings still come from Claude.
    providers = Providers(
        resolver=resolver,
        narrator=_RecordedOrFixture(recorded),
        deliverer=deliverer,
        safety=KeywordSafety(),
    )
    versions = Versions(engine="test", schema_version="1", prompt=llm.prompt_versions())
    result = run_tick(store, fx.CAMPAIGN, providers, versions=versions)
    assert not result.quiet, result.quiet_reason
    assert result.applied >= 1 and fx.NORTH in result.delivered and fx.SOUTH in result.delivered
    state = load_state(store, fx.CAMPAIGN)
    assert state.tick == 1 and state.pending == {}
    # canaries: in the log, in what was sent, and by the resolver's own prompt not leaking out
    assert canary.scan(state, store.read(fx.CAMPAIGN)) == []
    assert canary.scan_deliveries(state, [(s, t) for s, t, _ in deliverer.sent]) == []
    # costs recorded per call class; prompt version on every envelope
    closed = [e for e in store.read(fx.CAMPAIGN) if e.kind == "tick_closed"][-1]
    classes = sorted(c.call_class for c in closed.payload.costs)  # type: ignore[union-attr]
    assert classes.count("resolver") >= 1 and classes.count("narrator") >= 2
    recorded_costs = [c for c in closed.payload.costs if c.model != "fixture"]  # type: ignore[union-attr]
    assert recorded_costs and all(c.input_tokens > 0 for c in recorded_costs)
    tick_events = [e for e in store.read(fx.CAMPAIGN) if e.kind == "tick_opened"]
    assert all(e.versions.prompt == llm.prompt_versions() for e in tick_events)
    events = list(store.read(fx.CAMPAIGN))
    assert state_hash(replay(events[:-1])) == result.state_hash
    for _seat, text, _ in deliverer.sent:
        assert 40 < len(text.split()) < 320, text
        assert "intent_id" not in text and "01ARZ" not in text
