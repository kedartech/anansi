import pytest

from revisionist.core import events as ev
from revisionist.core.delta import AssetTierDelta
from revisionist.core.events import CallCost, Event
from revisionist.core.fold import replay
from revisionist.core.ids import DeltaId, IntentId
from revisionist.core.intent import Intent, Verb
from revisionist.core.report import (
    cost_report,
    leak_report,
    render_cost,
    render_leaks,
    render_why,
    why,
)
from revisionist.core.resolution import FeasibilityBand, OutcomeTier, RejectionReason, TickStage
from revisionist.core.visibility import PUBLIC
from tests import fixtures as fx

I1 = IntentId("01ARZ3NDEKTSV4RRFFQ69INT01")
I2 = IntentId("01ARZ3NDEKTSV4RRFFQ69INT02")
D1 = DeltaId("01ARZ3NDEKTSV4RRFFQ69DEL01")
D2 = DeltaId("01ARZ3NDEKTSV4RRFFQ69DEL02")
D9 = DeltaId("01ARZ3NDEKTSV4RRFFQ69DEL09")


def _log() -> list[Event]:
    return fx.creation_events()


def _add(log: list[Event], payload: ev.Payload, *, tick: int = 0) -> Event:
    event = fx.envelope(len(log), payload, tick=tick)
    log.append(event)
    return event


def _call(
    usd: float, *, call_class: str = "resolver", model: str = "model-a", tokens: int = 100
) -> CallCost:
    return CallCost(
        call_class=call_class,
        model=model,
        input_tokens=tokens,
        output_tokens=tokens // 2,
        usd=usd,
    )


def _intent(intent_id: IntentId = I1) -> Intent:
    return Intent(
        intent_id=intent_id,
        actor=fx.NORTH,
        verb=Verb.ATTACK,
        means=(fx.A_NORTH_ARMY,),
        scope=PUBLIC,
        filed_tick=0,
    )


def _delta(delta_id: DeltaId, reason: str = "the levies take the ford") -> AssetTierDelta:
    return AssetTierDelta(delta_id=delta_id, reason=reason, asset_id=fx.A_NORTH_ARMY, change=1)


def _chain_log() -> list[Event]:
    log = _log()
    order = _add(log, ev.OrderReceived(seat_id=fx.NORTH, text="march on the south at dawn"))
    _add(log, ev.IntentFiled(intent=_intent(), from_order=order.event_id))
    _add(log, ev.IntentFiled(intent=_intent(I2)))
    _add(
        log,
        ev.FeasibilityRated(
            intent_id=I1, band=FeasibilityBand.CONTESTED, perceived=FeasibilityBand.LIKELY
        ),
    )
    _add(
        log,
        ev.Rolled(
            intent_id=I1, seed="ab12", outcome=OutcomeTier.SUCCESS, exposure_roll=42, exposed=False
        ),
    )
    _add(log, ev.DeltaProposed(intent_id=I1, deltas=(_delta(D1), _delta(D2))))
    _add(
        log, ev.DeltaRejected(delta_id=D1, reason=RejectionReason.TIER_DISALLOWS, detail="tier cap")
    )
    _add(log, ev.DeltaApplied(intent_id=I1, delta=_delta(D2)))
    _add(log, ev.NarrationWritten(seat_id=fx.NORTH, text="the levies march", from_deltas=(D2,)))
    return log


def test_no_costs_reports_zeros() -> None:
    report = cost_report(_log())
    assert report.lines == ()
    assert report.total_usd == 0.0
    assert report.per_tick_usd == 0.0
    assert report.ticks_closed == 0
    assert report.by_class == {}
    assert report.by_model == {}


def test_tick_closed_costs_counted_once_beside_stage_costs() -> None:
    log = _log()
    _add(log, ev.TickStageCompleted(stage=TickStage.PROPOSE, costs=(_call(0.25),)), tick=1)
    _add(log, ev.TickClosed(costs=(_call(0.25),), state_hash="h"), tick=1)
    report = cost_report(log)
    assert report.total_usd == pytest.approx(0.25)
    assert report.lines[0].tick == 1
    assert report.lines[0].in_tick_usd == pytest.approx(0.25)
    assert report.lines[0].calls == 1


def test_cost_logged_attributed_to_its_tick() -> None:
    log = _log()
    _add(
        log, ev.CostLogged(seat_id=fx.NORTH, purpose="structure order", costs=(_call(0.1),)), tick=2
    )
    report = cost_report(log)
    assert len(report.lines) == 1
    line = report.lines[0]
    assert (line.tick, line.in_tick_usd, line.calls) == (2, 0.0, 1)
    assert line.out_of_tick_usd == pytest.approx(0.1)
    assert report.ticks_closed == 0


def test_by_class_and_by_model_cover_both_kinds() -> None:
    log = _log()
    _add(
        log,
        ev.TickClosed(
            costs=(
                _call(0.1, call_class="resolver", model="model-a"),
                _call(0.2, call_class="narrator", model="model-b"),
            ),
            state_hash="h",
        ),
        tick=1,
    )
    _add(
        log,
        ev.CostLogged(
            purpose="briefing", costs=(_call(0.3, call_class="narrator", model="model-a"),)
        ),
        tick=1,
    )
    report = cost_report(log)
    assert report.by_class == pytest.approx({"resolver": 0.1, "narrator": 0.5})
    assert report.by_model == pytest.approx({"model-a": 0.4, "model-b": 0.2})
    assert report.total_usd == pytest.approx(0.6)
    assert report.input_tokens == 300
    assert report.output_tokens == 150
    assert report.lines[0].calls == 3


def test_per_tick_usd_divides_total_by_closed_ticks() -> None:
    log = _log()
    _add(log, ev.TickClosed(costs=(_call(0.10),), state_hash="h"), tick=1)
    _add(log, ev.TickClosed(costs=(_call(0.30),), state_hash="h"), tick=2)
    _add(log, ev.CostLogged(purpose="deputy", costs=(_call(0.20),)), tick=2)
    report = cost_report(log)
    assert report.ticks_closed == 2
    assert report.total_usd == pytest.approx(0.6)
    assert report.per_tick_usd == pytest.approx(0.3)


def test_quiet_tick_is_flagged_and_still_listed() -> None:
    log = _log()
    _add(log, ev.TickClosed(quiet=True, quiet_reason="resolver failed", state_hash="h"), tick=3)
    _add(log, ev.TickClosed(state_hash="h"), tick=4)
    report = cost_report(log)
    assert [(x.tick, x.quiet, x.calls) for x in report.lines] == [(3, True, 0), (4, False, 0)]
    assert report.per_tick_usd == 0.0


def test_lines_are_sorted_by_tick() -> None:
    log = _log()
    _add(log, ev.CostLogged(purpose="late", costs=(_call(0.1),)), tick=5)
    _add(log, ev.TickClosed(costs=(_call(0.1),), state_hash="h"), tick=2)
    assert [x.tick for x in cost_report(log).lines] == [2, 5]


def test_render_cost_has_table_header_and_total_with_four_decimals() -> None:
    log = _log()
    _add(log, ev.TickClosed(costs=(_call(0.1), _call(0.02)), state_hash="h"), tick=1)
    text = render_cost(cost_report(log))
    assert text.splitlines()[0] == "tick  in-tick  outside  calls"
    assert "total $0.1200 over 1 ticks, $0.1200 per tick" in text.splitlines()


def test_why_from_intent_returns_chain_in_seq_order() -> None:
    lines = why(_chain_log(), I1)
    assert [w.kind for w in lines] == [
        "order_received",
        "intent_filed",
        "feasibility_rated",
        "rolled",
        "delta_proposed",
        "delta_rejected",
        "delta_applied",
        "narration_written",
    ]
    assert [w.seq for w in lines] == sorted(w.seq for w in lines)


def test_why_summaries_follow_the_summary_rules() -> None:
    lines = why(_chain_log(), I1)
    assert lines[0].summary == f"{fx.NORTH} wrote: march on the south at dawn"
    assert lines[1].summary == f"attack by {fx.NORTH} targets=0 means=1"
    assert lines[2].summary == "true contested, perceived likely"
    assert lines[3].summary == "success, exposure 42 exposed=False"
    assert lines[4].summary == "2 deltas"
    assert lines[5].summary == f"{D1} tier_disallows: tier cap"
    assert lines[6].summary == "asset_tier: the levies take the ford"
    assert lines[7].summary == f"to {fx.NORTH}: the levies march"


def test_why_from_rolled_event_returns_same_chain_as_intent() -> None:
    log = _chain_log()
    rolled = next(e for e in log if e.kind == "rolled")
    assert why(log, rolled.event_id) == why(log, I1)


def test_why_from_event_without_intent_returns_one_line() -> None:
    log = _chain_log()
    narration = next(e for e in log if e.kind == "narration_written")
    lines = why(log, narration.event_id)
    assert len(lines) == 1
    assert lines[0].kind == "narration_written"


def test_why_unknown_target_is_empty() -> None:
    log = _chain_log()
    assert why(log, "01ARZ3NDEKTSV4RRFFQ69NOPE0") == []
    assert render_why([]) == "nothing found"


def test_why_links_narration_by_applied_delta_ids() -> None:
    log = _log()
    _add(log, ev.IntentFiled(intent=_intent()))
    _add(log, ev.DeltaProposed(intent_id=I1, deltas=(_delta(D1),)))
    _add(log, ev.DeltaApplied(intent_id=I1, delta=_delta(D1)))
    _add(log, ev.NarrationWritten(seat_id=fx.NORTH, text="linked", from_deltas=(D1,)))
    _add(log, ev.NarrationWritten(seat_id=fx.NORTH, text="unrelated", from_deltas=(D9,)))
    _add(log, ev.NarrationWritten(seat_id=fx.SOUTH, text="unlinked"))
    lines = why(log, I1)
    assert [w.kind for w in lines] == [
        "intent_filed",
        "delta_proposed",
        "delta_applied",
        "narration_written",
    ]
    assert lines[-1].summary == f"to {fx.NORTH}: linked"


def test_why_summary_is_capped_at_200_chars() -> None:
    log = _log()
    _add(log, ev.DeltaApplied(intent_id=I1, delta=_delta(D1, reason="x" * 200)))
    (line,) = why(log, I1)
    assert len(line.summary) == 200
    assert line.summary.startswith("asset_tier: ")


def test_why_flattens_newlines_in_summaries() -> None:
    log = _log()
    _add(
        log,
        ev.IntentRejected(
            intent_id=I1, reason=RejectionReason.MEANS_NOT_HELD, detail="line one\nline two"
        ),
    )
    (line,) = why(log, I1)
    assert line.summary == "means_not_held: line one line two"


def test_why_truncates_order_text_to_120_chars() -> None:
    log = _log()
    order = _add(log, ev.OrderReceived(seat_id=fx.NORTH, text="a" * 4000))
    _add(log, ev.IntentFiled(intent=_intent(), from_order=order.event_id))
    first = why(log, I1)[0]
    assert first.kind == "order_received"
    assert first.summary == f"{fx.NORTH} wrote: " + "a" * 120


def test_render_why_formats_one_line_per_entry() -> None:
    log = _chain_log()
    first_seq = len(fx.creation_events())
    text = render_why(why(log, I1))
    assert (
        text.splitlines()[0] == f"#{first_seq} t0 order_received: {fx.NORTH} wrote: "
        "march on the south at dawn"
    )
    assert len(text.splitlines()) == 8


def test_leak_report_flags_south_narration_and_not_north() -> None:
    log = _log()
    south = _add(log, ev.NarrationWritten(seat_id=fx.SOUTH, text=f"rumour: {fx.CANARY}"))
    _add(log, ev.NarrationWritten(seat_id=fx.NORTH, text=f"you hold {fx.CANARY}"))
    leaks = leak_report(replay(log), log)
    assert len(leaks) == 1
    assert leaks[0].seat_id == fx.SOUTH
    assert leaks[0].fact_id == fx.F_NORTH_PLOT
    assert leaks[0].event_id == south.event_id
    assert leaks[0].kind == "narration_written"


def test_render_leaks_zero_branch() -> None:
    assert render_leaks([]) == "0 leaks"


def test_render_leaks_lists_each_leak_then_count() -> None:
    log = _log()
    south = _add(log, ev.NarrationWritten(seat_id=fx.SOUTH, text=fx.CANARY))
    leaks = leak_report(replay(log), log)
    assert render_leaks(leaks).splitlines() == [
        f"LEAK seat={fx.SOUTH} fact={fx.F_NORTH_PLOT} event={south.event_id}",
        "1 leaks",
    ]
