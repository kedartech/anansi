"""World check: the gap list and the `world check` command over the bundled worlds."""

from pathlib import Path

from typer.testing import CliRunner

from revisionist import cli
from revisionist.core.realism.check import gaps
from revisionist.core.world import WorldFile
from revisionist.engine import campaign as cmp

runner = CliRunner()


def _load(name: str) -> WorldFile:
    return cmp.load_world(cmp.world_path(name))


def test_slice_gaps_list_the_unsourced_quantity_and_pipeline() -> None:
    found = {(g.section, g.key) for g in gaps(_load("realism-slice-1941"))}
    assert ("quantities", "house_votes_neutrality") in found
    assert ("pipelines", "bill_through_congress") in found
    assert ("sources", "frus_1941") in found


def test_toy_has_no_gap_flags_and_lists_an_unverified_source(tmp_path: Path) -> None:
    assert gaps(_load("realism-toy")) == []
    text = cmp.world_path("realism-toy").read_text().replace("verified: true", "verified: false")
    unverified = tmp_path / "toy.yaml"
    unverified.write_text(text)
    assert [(g.section, g.key) for g in gaps(cmp.load_world(unverified))] == [("sources", "toy")]


def test_world_check_passes_the_toy() -> None:
    result = runner.invoke(cli.app, ["world", "check", "realism-toy"])
    assert result.exit_code == 0, result.output
    assert "0 problems, 0 gaps" in result.output


def test_world_check_reports_a_broken_reference(tmp_path: Path) -> None:
    text = cmp.world_path("realism-toy").read_text().replace("owner: council", "owner: atlantis")
    bad = tmp_path / "bad.yaml"
    bad.write_text(text)
    result = runner.invoke(cli.app, ["world", "check", str(bad)])
    assert result.exit_code == 1
    assert "problems:" in result.output and "atlantis" in result.output
    assert "1 problems" in result.output


def test_world_check_gaps_only_prints_the_gap_list() -> None:
    result = runner.invoke(cli.app, ["world", "check", "realism-slice-1941", "--gaps-only"])
    assert "house_votes_neutrality" in result.output
    assert "problems:" not in result.output and "1 problems" not in result.output


def test_world_check_rejects_an_unparseable_file(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("world: [unclosed\n")
    result = runner.invoke(cli.app, ["world", "check", str(bad)])
    assert result.exit_code == 1


def test_legacy_world_checks_clean_with_no_gaps() -> None:
    assert gaps(_load("salt-strait")) == []
    result = runner.invoke(cli.app, ["world", "check", "salt-strait"])
    assert result.exit_code == 0, result.output
    assert "0 problems, 0 gaps" in result.output
