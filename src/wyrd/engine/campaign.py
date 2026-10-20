"""A campaign on disk: the store, the world loader and the providers the CLI uses.

Everything the CLI needs that touches the file system or a model lives here, so
``wyrd.cli`` stays a thin layer of argument parsing and printing.
"""

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from wyrd import __version__
from wyrd.core import SCHEMA_VERSION
from wyrd.core import events as ev
from wyrd.core.events import CallCost, Event, Versions
from wyrd.core.ids import CampaignId, EventId, SeatId
from wyrd.core.ontology import Controller, SeatKind
from wyrd.core.state import State
from wyrd.core.visibility import PUBLIC, private_to
from wyrd.core.world import WorldFile
from wyrd.core.world_compile import compile_world
from wyrd.engine.ids import new_id
from wyrd.engine.llm import ClaudeCli, Transport, prompt_versions
from wyrd.engine.store import SqliteStore, load_state

WORLDS_DIR = Path(__file__).resolve().parents[3] / "worlds"
"""Bundled world bibles. ``wyrd new salt-strait`` reads ``worlds/salt-strait.yaml``."""


def versions() -> Versions:
    return Versions(engine=__version__, schema_version=SCHEMA_VERSION, prompt=prompt_versions())


def db_path() -> Path:
    """``$WYRD_DB`` or ``./wyrd.db``."""
    return Path(os.environ.get("WYRD_DB", "wyrd.db"))


def open_store(path: Path | None = None) -> SqliteStore:
    return SqliteStore(path or db_path())


def world_path(name: str) -> Path:
    """A path as given if it exists, else ``worlds/<name>.yaml``."""
    given = Path(name)
    if given.is_file():
        return given
    bundled = WORLDS_DIR / f"{name}.yaml"
    if bundled.is_file():
        return bundled
    raise FileNotFoundError(f"no world {name!r} (looked for {given} and {bundled})")


def load_world(path: Path) -> WorldFile:
    return WorldFile.model_validate(yaml.safe_load(path.read_text()))


def new_campaign(store: SqliteStore, world: WorldFile) -> CampaignId:
    campaign_id = CampaignId(new_id())
    store.append_all(compile_world(world, campaign_id, versions()))
    return campaign_id


def current_campaign(store: SqliteStore) -> CampaignId:
    """The newest campaign in the file."""
    ids = store.campaign_ids()
    if not ids:
        raise LookupError("no campaign yet: run `wyrd new <world>` first")
    return ids[-1]


def human_seat(state: State) -> SeatId:
    """The one human seat of a solo campaign."""
    humans = sorted(s.seat_id for s in state.seats.values() if s.controller is Controller.HUMAN)
    if len(humans) != 1:
        raise LookupError(f"expected one human seat, found {len(humans)}")
    return humans[0]


def faction_seats(state: State) -> list[SeatId]:
    """AI-run faction seats, in a stable order. These take deputy turns before a tick."""
    return sorted(
        s.seat_id
        for s in state.seats.values()
        if s.kind is SeatKind.FACTION and s.controller is Controller.AI
    )


def log_cost(
    store: SqliteStore,
    campaign_id: CampaignId,
    purpose: str,
    costs: Sequence[CallCost],
    seat: SeatId | None = None,
) -> None:
    """Land a ``cost_logged`` record for calls made outside a tick."""
    if not costs:
        return
    state = load_state(store, campaign_id)
    event = Event(
        event_id=EventId(new_id()),
        campaign_id=campaign_id,
        seq=state.last_seq + 1,
        tick=state.tick,
        actor=seat,
        scope=private_to(seat) if seat else PUBLIC,
        payload=ev.CostLogged(seat_id=seat, purpose=purpose[:60], costs=tuple(costs)),
        versions=versions(),
    )
    store.append(event)


def live_transport() -> Transport:
    """The Claude Code CLI on the host's subscription. Tests replace this."""
    return ClaudeCli()


@dataclass
class FileDeliverer:
    """Writes each briefing to ``<dir>/t<tick>-<seat name>.md``. Idempotent on the key."""

    directory: Path
    names: dict[SeatId, str]
    tick: int

    def deliver(self, seat_id: SeatId, text: str, idempotency_key: str) -> str:
        self.directory.mkdir(parents=True, exist_ok=True)
        slug = "".join(c if c.isalnum() else "-" for c in self.names.get(seat_id, seat_id).lower())
        path = self.directory / f"t{self.tick:03d}-{slug}.md"
        marker = f"<!-- {idempotency_key} -->"
        if path.exists() and marker in path.read_text():
            return "file"
        path.write_text(f"{marker}\n{text}\n")
        return "file"


def briefings_dir(store_path: Path) -> Path:
    return store_path.parent / f"{store_path.stem}-briefings"


__all__ = [
    "WORLDS_DIR",
    "FileDeliverer",
    "briefings_dir",
    "current_campaign",
    "db_path",
    "faction_seats",
    "human_seat",
    "live_transport",
    "load_world",
    "log_cost",
    "new_campaign",
    "open_store",
    "versions",
    "world_path",
]
