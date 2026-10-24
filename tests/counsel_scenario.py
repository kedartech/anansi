"""The fixed autumn-1941 scenario the counsel and aide recordings were built from.

Roosevelt seated under the fixed campaign id, two offline ticks (template prose),
and, for the unit tests, a memo from Hopkins whose truth carries a canary.
"""

from revisionist.core import events as ev
from revisionist.core.ids import DocumentId, EntityId, EventId, QuantityId, SeatId, derived_id
from revisionist.core.realism.card import ChannelKind
from revisionist.core.realism.document import (
    BodyField,
    Classification,
    DocumentKind,
    DocumentTruth,
    DocumentView,
    TruthRef,
)
from revisionist.core.visibility import private_to
from revisionist.engine import campaign as cmp
from revisionist.engine.prose import TemplateProseWriter
from revisionist.engine.realism_tick import run_realism_tick
from revisionist.engine.store import EventStore, load_state
from tests.test_realism_tick import CAMPAIGN, seated

CANARY = "CANARY-COUNSEL-TRUTH-5b70"
TICKS = 2
WIRING_QUESTION = "What does Moscow need from us most?"
QUESTION = "What does Moscow need from us most, Harry?"


def hopkins(store: EventStore) -> EntityId:
    state = load_state(store, CAMPAIGN)
    return next(e for e, x in state.entities.items() if x.name == "Harry L. Hopkins")


def roosevelt_seat(store: EventStore) -> SeatId:
    (seat,) = load_state(store, CAMPAIGN).seats
    return seat


def ticked(store: EventStore) -> None:
    """Roosevelt seated and the world run ``TICKS`` offline ticks, as the wiring gate does."""
    seated(store, "autumn-1941", "roosevelt")
    for _ in range(TICKS):
        run_realism_tick(store, CAMPAIGN, prose=TemplateProseWriter())


def plant_memo(store: EventStore) -> DocumentId:
    """A memo from Hopkins to the President, arrived today, its truth canaried."""
    state = load_state(store, CAMPAIGN)
    today = state.in_world_date
    assert today is not None
    doc_id = DocumentId(derived_id(CAMPAIGN, "hopkins-memo"))
    view = DocumentView(
        doc_id=doc_id,
        kind=DocumentKind.MEMO,
        author=hopkins(store),
        issued=today,
        arrives=today,
        channel=ChannelKind.IN_PERSON,
        classification=Classification.CONFIDENTIAL,
        title="Soviet list, as Harriman sent it",
        body=(
            BodyField(label="Tanks asked for a month", value=500, unit="tanks"),
            BodyField(label="Aircraft asked for a month", value=400, unit="aircraft"),
        ),
        scope=private_to(roosevelt_seat(store)),
    )
    truth = DocumentTruth(
        doc_id=doc_id,
        truth_refs=(
            TruthRef(quantity_id=QuantityId(next(iter(state.quantities))), value=1, canary=CANARY),
        ),
    )
    store.append_all(
        [
            ev.Event(
                event_id=EventId(derived_id(CAMPAIGN, "hopkins-memo-event")),
                campaign_id=CAMPAIGN,
                seq=state.last_seq + 1,
                tick=state.tick,
                scope=view.scope,
                payload=ev.DocumentIssued(document=view, truth=truth),
                versions=cmp.versions(),
            )
        ]
    )
    return doc_id
