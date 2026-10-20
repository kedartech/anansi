"""A campaign on disk: the store, the world loader and the providers the CLI uses.

Everything the CLI needs that touches the file system or a model lives here, so
``revisionist.cli`` stays a thin layer of argument parsing and printing.
"""

import getpass
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from revisionist import __version__
from revisionist.core import SCHEMA_VERSION
from revisionist.core import events as ev
from revisionist.core.events import CallCost, Event, Versions
from revisionist.core.ids import CampaignId, EventId, SeatId
from revisionist.core.ontology import Controller, SeatKind
from revisionist.core.state import State
from revisionist.core.visibility import PUBLIC, private_to
from revisionist.core.world import WorldFile
from revisionist.core.world_compile import compile_world
from revisionist.engine.ids import new_id
from revisionist.engine.llm import ClaudeCli, Transport, prompt_versions
from revisionist.engine.store import EventStore, Sealer, SqliteStore, load_state

if TYPE_CHECKING:
    from revisionist.engine.pg_store import PostgresEventStore

WORLDS_DIR = Path(__file__).resolve().parents[3] / "worlds"
"""Bundled world bibles. ``revisionist new salt-strait`` reads ``worlds/salt-strait.yaml``."""


def versions() -> Versions:
    return Versions(engine=__version__, schema_version=SCHEMA_VERSION, prompt=prompt_versions())


def db_target() -> str:
    """``$REVISIONIST_DB``: a ``postgresql://`` URL or a SQLite file path
    (default ``./revisionist.db``)."""
    return os.environ.get("REVISIONIST_DB", "revisionist.db")


def is_postgres(target: str) -> bool:
    return target.startswith(("postgresql://", "postgres://"))


def db_path() -> Path:
    """The SQLite file ``$REVISIONIST_DB`` names."""
    return Path(db_target())


def open_store(target: Path | str | None = None) -> EventStore:
    """The store ``$REVISIONIST_DB`` names. On Postgres with a master key set
    (``$REVISIONIST_MASTER_KEY_FILE``), it seals new campaigns and reads sealed
    ones (Layer 9b)."""
    target = str(target) if target is not None else db_target()
    if is_postgres(target):
        # Imported here so SQLite play never needs the ``postgres`` extra.
        from revisionist.engine.pg_store import PostgresEventStore

        store = PostgresEventStore(target)
        store.sealer = _sealer(store)
        return store
    return SqliteStore(target)


def _sealer(store: "PostgresEventStore") -> "Sealer | None":
    if not os.environ.get("REVISIONIST_MASTER_KEY_FILE") and not os.environ.get(
        "CREDENTIALS_DIRECTORY"
    ):
        return None
    # The seal extra (cryptography) is needed only when a master key is configured.
    from revisionist.engine.seal import KeyringSealer, PgKeyring, load_master_key, master_key_path

    path = master_key_path()
    if path is None:
        return None
    return KeyringSealer(PgKeyring(store.conn, load_master_key(path)))


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


def new_campaign(store: EventStore, world: WorldFile) -> CampaignId:
    campaign_id = CampaignId(new_id())
    sealer: Sealer | None = getattr(store, "sealer", None)
    if sealer is not None:
        sealer.begin(campaign_id)
    store.append_all(compile_world(world, campaign_id, versions()))
    return campaign_id


def current_campaign(store: EventStore) -> CampaignId:
    """The newest campaign in the file."""
    ids = store.campaign_ids()
    if not ids:
        raise LookupError("no campaign yet: run `revisionist new <world>` first")
    return ids[-1]


def human_seats(state: State) -> list[SeatId]:
    """Every human seat, in a stable order."""
    return sorted(s.seat_id for s in state.seats.values() if s.controller is Controller.HUMAN)


def human_seat(state: State) -> SeatId:
    """The one human seat. With several, the caller has to say which (``--seat``)."""
    humans = human_seats(state)
    if not humans:
        raise LookupError("this campaign has no human seat")
    if len(humans) > 1:
        names = ", ".join(f"{state.seats[s].name} ({s})" for s in humans)
        raise LookupError(
            f"this campaign has {len(humans)} human seats: {names}; pick one with --seat"
        )
    return humans[0]


def resolve_seat(state: State, ref: str) -> SeatId:
    """A seat by ID, or by its name ignoring case."""
    if ref in state.seats:
        return SeatId(ref)
    named = [s.seat_id for s in state.seats.values() if s.name.casefold() == ref.casefold()]
    if len(named) != 1:
        raise LookupError(f"no seat {ref!r} in {state.campaign_id}")
    return named[0]


def faction_seats(state: State) -> list[SeatId]:
    """AI-run faction seats, in a stable order. These take deputy turns before a tick."""
    return sorted(
        s.seat_id
        for s in state.seats.values()
        if s.kind is SeatKind.FACTION and s.controller is Controller.AI
    )


def log_cost(
    store: EventStore,
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


def log_admin_read(
    store: EventStore, campaign_id: CampaignId, events: Sequence[EventId], purpose: str
) -> None:
    """Land an ``admin_read`` before a host is shown private records (Layer 9b).

    It is Public: everyone in the campaign learns that the host looked, and how
    much, though not at what. If it cannot be logged, the caller shows nothing.
    """
    state = load_state(store, campaign_id)
    store.append(
        Event(
            event_id=EventId(new_id()),
            campaign_id=campaign_id,
            seq=state.last_seq + 1,
            tick=state.tick,
            scope=PUBLIC,
            payload=ev.AdminRead(
                actor=f"host:{getpass.getuser()}", events=tuple(events), purpose=purpose[:200]
            ),
            versions=versions(),
        )
    )


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


def briefings_dir(target: str | None = None) -> Path:
    """``$REVISIONIST_BRIEFINGS_DIR``, else ``<db>-briefings`` beside a SQLite file, else
    ``./revisionist-briefings`` for Postgres."""
    if override := os.environ.get("REVISIONIST_BRIEFINGS_DIR"):
        return Path(override)
    target = target if target is not None else db_target()
    if is_postgres(target):
        return Path("revisionist-briefings")
    path = Path(target)
    return path.parent / f"{path.stem}-briefings"


__all__ = [
    "WORLDS_DIR",
    "FileDeliverer",
    "briefings_dir",
    "current_campaign",
    "db_path",
    "db_target",
    "faction_seats",
    "human_seat",
    "human_seats",
    "is_postgres",
    "live_transport",
    "load_world",
    "log_admin_read",
    "log_cost",
    "new_campaign",
    "open_store",
    "resolve_seat",
    "versions",
    "world_path",
]
