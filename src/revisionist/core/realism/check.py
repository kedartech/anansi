"""Gaps in a world file: what is still unsourced, before anyone plays it.

A gap is a figure the author has not cited yet, so nothing may be computed from
it. Problems (a broken reference) are the compiler's to report; this lists what
is left open even when the file is valid.
"""

from revisionist.core._base import Model
from revisionist.core.world import WorldFile


class Gap(Model):
    section: str
    key: str
    what: str


def gaps(world: WorldFile) -> list[Gap]:
    """Every gap-flagged declaration, unset nullable number, and unverified source, sorted."""
    out: list[Gap] = []
    for q in world.quantities:
        if q.gap:
            out.append(Gap(section="quantities", key=q.key, what="unsourced (gap: true)"))
        if q.value is None and not q.computed:
            out.append(Gap(section="quantities", key=q.key, what="value is None"))
    for f in world.feeds:
        if f.gap:
            out.append(Gap(section="feeds", key=f.key, what="unsourced (gap: true)"))
        if f.every_days is None:
            out.append(Gap(section="feeds", key=f.key, what="every_days is None"))
        if f.latency_days is None:
            out.append(Gap(section="feeds", key=f.key, what="latency_days is None"))
    for pl in world.pipelines:
        if pl.gap:
            out.append(Gap(section="pipelines", key=pl.key, what="unsourced (gap: true)"))
        for i, step in enumerate(pl.steps):
            if step.days is None:
                out.append(Gap(section="pipelines", key=pl.key, what=f"step {i} days is None"))
    for s in world.sources:
        if not s.verified:
            out.append(Gap(section="sources", key=s.key, what="not verified"))
    return sorted(out, key=lambda g: (g.section, g.key, g.what))


def assumed(world: WorldFile) -> list[Gap]:
    """Timings filled from the labelled default policy, not a source. Listed apart
    from gaps: they let the world run, but none of them is data."""
    out: list[Gap] = []
    for f in world.feeds:
        if f.assumed:
            out.append(Gap(section="feeds", key=f.key, what="cadence and latency assumed"))
    for pl in world.pipelines:
        for i, step in enumerate(pl.steps):
            if step.assumed:
                out.append(Gap(section="pipelines", key=pl.key, what=f"step {i} days assumed"))
    return sorted(out, key=lambda g: (g.section, g.key, g.what))


def report(world: WorldFile, problems: list[str], found: list[Gap]) -> str:
    """Problems first, then gaps grouped by section, then assumed timings, then a
    one-line count."""
    lines = [f"{world.world}: {world.name}"]
    if problems:
        lines.append("problems:")
        lines.extend(f"  {p}" for p in problems)
    lines.extend(_gap_lines(found))
    labelled = assumed(world)
    if labelled:
        lines.append("assumed (labelled defaults, not data):")
        lines.extend(f"  {line}" for line in _gap_lines(labelled))
    count = f"{len(problems)} problems, {len(found)} gaps"
    lines.append(count + (f", {len(labelled)} assumed" if labelled else ""))
    return "\n".join(lines)


def gap_report(found: list[Gap]) -> str:
    """The gap list alone, grouped by section."""
    return "\n".join(_gap_lines(found) or ["no gaps"])


def _gap_lines(found: list[Gap]) -> list[str]:
    lines: list[str] = []
    section = None
    for g in found:
        if g.section != section:
            section = g.section
            lines.append(f"{section}:")
        lines.append(f"  {g.key}: {g.what}")
    return lines


__all__ = ["Gap", "assumed", "gap_report", "gaps", "report"]
