"""The fixed toy scenario the prose recordings were built from.

Ada seated in the toy world under a fixed campaign id, with a page planted from
Cora (a second author) and a page from Bram whose truth carries a canary. The
first tick then issues Bram's count, so the PROSE stage sees two authors.
"""

from revisionist.core import events as ev
from revisionist.core.ids import DocumentId, EventId, QuantityId, derived_id
from revisionist.core.realism.card import ChannelKind
from revisionist.core.realism.document import (
    BodyField,
    DocumentKind,
    DocumentTruth,
    DocumentView,
    TruthRef,
)
from revisionist.core.visibility import private_to
from revisionist.engine import campaign as cmp
from revisionist.engine.store import EventStore, load_state
from tests.test_realism_tick import CAMPAIGN, seated

CANARY = "CANARY-PROSE-TRUTH-3e9a"


def plant(store: EventStore, n: int, author: str, body: tuple[BodyField, ...]) -> DocumentId:
    """A letter from ``author`` to Ada, dated today, with a truth canary on Bram's."""
    state = load_state(store, CAMPAIGN)
    (seat,) = state.seats
    today = state.in_world_date
    assert today is not None
    who = next(e for e, x in state.entities.items() if x.name.split()[0].lower() == author)
    doc_id = DocumentId(derived_id(CAMPAIGN, "planted", n))
    view = DocumentView(
        doc_id=doc_id,
        kind=DocumentKind.LETTER,
        author=who,
        issued=today,
        arrives=today,
        channel=ChannelKind.LETTER,
        title=f"A note from {state.entities[who].name}",
        body=body,
        scope=private_to(seat),
    )
    quantity = QuantityId(next(iter(state.quantities)))
    truth = DocumentTruth(
        doc_id=doc_id,
        truth_refs=(TruthRef(quantity_id=quantity, value=7, canary=CANARY),),
    )
    store.append_all(
        [
            ev.Event(
                event_id=EventId(derived_id(CAMPAIGN, "planted-event", n)),
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


def scenario(store: EventStore) -> tuple[DocumentId, DocumentId]:
    """Seat Ada and plant Cora's and Bram's pages. Returns (cora's, bram's)."""
    seated(store)
    cora = plant(
        store,
        1,
        "cora",
        (
            BodyField(label="Bills enrolled this week", value=3, unit="bills"),
            BodyField(label="Clerks absent", value=None),
        ),
    )
    bram = plant(store, 2, "bram", (BodyField(label="Members wavering", value=2, unit="members"),))
    return cora, bram
