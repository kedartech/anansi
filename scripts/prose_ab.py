"""P0 prose A/B: the same five pages written by two models, for a blind read.

Takes five documents from an offline autumn-1941 run (bare pages, no prose), has
each configured model write each page through the real prose path, and saves:

- ``sheet.md``: every page with its two versions labelled A and B in random order,
  and no model named anywhere;
- ``key.json``: which model is A and which is B for each page, what each call cost,
  and whether the number check dropped the prose.

It makes live model calls, so it refuses to run without ``--live``:

    uv run --frozen python scripts/prose_ab.py --live --out ab-out

The owner reads the sheet blind before opening the key.
"""

import argparse
import json
import random
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from revisionist.core.ids import CampaignId, EntityId
from revisionist.core.realism.document import DocumentView
from revisionist.core.state import State
from revisionist.core.world_compile import compile_world, resolve_keys
from revisionist.engine import campaign as cmp
from revisionist.engine import realism_tick
from revisionist.engine.llm import ClaudeCli, Transport, TransportError
from revisionist.engine.prose import ClaudeProseWriter, Written, accepted, author_of
from revisionist.engine.store import MemoryStore, load_state

MODELS = ("claude-haiku-5-5", "claude-sonnet-5-5")
CAMPAIGN = CampaignId("01ARZ3NDEKTSV4RRFFQ69G5FAV")
PAGES = 5
TICKS = 3
SEED = 1941


def offline_state(ticks: int = TICKS) -> State:
    """autumn-1941 as Roosevelt after ``ticks`` offline ticks, pages left bare."""
    store = MemoryStore()
    decl = cmp.load_world(cmp.world_path("autumn-1941"))
    store.append_all(compile_world(decl, CAMPAIGN, cmp.versions()))
    roosevelt = EntityId(resolve_keys(decl, CAMPAIGN)["roosevelt"])
    realism_tick.seat_player(store, CAMPAIGN, roosevelt)
    for _ in range(ticks):
        realism_tick.run_realism_tick(store, CAMPAIGN, versions=cmp.versions())
    return load_state(store, CAMPAIGN)


def pick(state: State, n: int = PAGES) -> list[DocumentView]:
    """``n`` pages that state at least one figure, one per author first, oldest first."""
    pages = sorted(
        (
            d.view
            for d in state.documents.values()
            if any(isinstance(x.value, int) for x in d.view.body)
        ),
        key=lambda v: (v.issued, v.doc_id),
    )
    chosen: list[DocumentView] = []
    for page in pages:
        if len(chosen) < n and page.author not in {c.author for c in chosen}:
            chosen.append(page)
    chosen += [p for p in pages if p not in chosen][: n - len(chosen)]
    return chosen


def _lines(page: DocumentView) -> list[str]:
    out = []
    for x in page.body:
        value = "not stated" if x.value is None else f"{x.value}{' ' + x.unit if x.unit else ''}"
        out.append(f"- {x.label}: {value}")
    return out


def run(
    transport: Transport,
    out: Path,
    models: Sequence[str] = MODELS,
    seed: int = SEED,
    ticks: int = TICKS,
) -> tuple[Path, Path]:
    """Write the sheet and the key under ``out``. Returns their paths."""
    if len(models) != 2:
        raise ValueError("an A/B needs exactly two models")
    state = offline_state(ticks)
    rng = random.Random(seed)
    sheet = [
        "# Prose A/B: blind sheet",
        "",
        "Read each page's A and B and note which you prefer.",
        "",
    ]
    key: dict[str, Any] = {"seed": seed, "models": list(models), "pages": []}
    for n, page in enumerate(pick(state), start=1):
        author = author_of(state, page.author)
        texts: dict[str, str] = {}
        calls: dict[str, Any] = {}
        for model in models:
            writer = ClaudeProseWriter(transport, model=model)
            try:
                written = writer.write(author, [page])
            except TransportError as exc:
                written, calls[model] = Written(prose={}), {"error": str(exc)[:200]}
            keep, dropped = accepted([page], written)
            texts[model] = (
                keep.get(page.doc_id)
                or "(no prose: " + (dropped[0].reason if dropped else "call failed") + ")"
            )
            calls.setdefault(model, {})
            if written.cost is not None:
                calls[model] |= written.cost.model_dump(mode="json")
            calls[model]["dropped"] = [r.reason for r in dropped]
        order = list(models)
        rng.shuffle(order)
        sheet += [
            f"## Page {n}: {page.title}",
            "",
            f"{author.name}, {page.kind.value} by {page.channel.value}, issued {page.issued}",
            "",
            *_lines(page),
            "",
            "### A",
            "",
            texts[order[0]],
            "",
            "### B",
            "",
            texts[order[1]],
            "",
        ]
        key["pages"].append(
            {"page": n, "doc_id": page.doc_id, "A": order[0], "B": order[1], "calls": calls}
        )
    out.mkdir(parents=True, exist_ok=True)
    sheet_path, key_path = out / "sheet.md", out / "key.json"
    sheet_path.write_text("\n".join(sheet))
    key_path.write_text(json.dumps(key, indent=1) + "\n")
    return sheet_path, key_path


def main(argv: Sequence[str] | None = None, transport: Callable[[], Transport] = ClaudeCli) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--live", action="store_true", help="Allow live model calls (required)")
    parser.add_argument("--out", type=Path, default=Path("prose-ab"))
    parser.add_argument("--models", default=",".join(MODELS), help="Two model ids, comma-separated")
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args(argv)
    if not args.live:
        print("refusing to run: this makes live model calls; pass --live", file=sys.stderr)
        return 2
    sheet, key = run(transport(), args.out, tuple(args.models.split(",")), args.seed)
    print(f"sheet: {sheet}\nkey (open after reading): {key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
