"""The season report names the drift from history in a dated world (spec §5.4, Wave 3)."""

from datetime import date

from revisionist.core.dashboard import season_report
from revisionist.core.fold import replay
from tests import fixtures as fx


def test_a_dated_world_reports_its_drift() -> None:
    state = replay(fx.creation_events())
    assert not any(line.startswith("Drift from history") for line in season_report(state))
    dated = state.model_copy(update={"in_world_date": date(1478, 5, 1), "divergence": 7})
    assert "Drift from history: 7." in season_report(dated)
