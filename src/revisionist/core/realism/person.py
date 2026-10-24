"""``project_person(state, person) -> PersonView``: what one person on a card knows.

Counsel speaks as a person who need not hold a seat (Hopkins, Hull), so the seat
projection does not fit. A person holds the pages they wrote, the pages from
the feeds on their own card and any page whose scope admits a seat that
embodies them, each once it has arrived. Only ``DocumentView`` halves are
copied, and the card shows as its face plus its voice: loyalty, doctrine and
interests never leave the state.
"""

from datetime import date

from pydantic import Field

from revisionist.core._base import Label, Model
from revisionist.core.ids import EntityId
from revisionist.core.realism.card import CardFace
from revisionist.core.realism.document import DocumentView
from revisionist.core.state import State


class PersonView(Model):
    """One person's knowledge, as the counsel model may read it. Never truth."""

    person: EntityId
    name: Label
    face: CardFace | None = None
    voice: str = Field(default="", max_length=200)
    today: date
    documents: tuple[DocumentView, ...] = ()
    """Arrived pages this person holds, oldest first."""


def project_person(state: State, person: EntityId) -> PersonView:
    if person not in state.entities:
        raise KeyError(f"unknown person {person!r}")
    today = state.in_world_date
    if today is None:
        raise ValueError("a person projection needs an in-world date")
    card = state.cards.get(person)
    feeds = set(card.feeds) if card is not None else set()
    seats = {s.seat_id for s in state.seats.values() if s.embodies == person}
    held = [
        d.view
        for d in state.documents.values()
        if d.view.arrives <= today
        and (
            d.view.author == person
            or (d.truth.feed is not None and d.truth.feed in feeds)
            or any(d.view.scope.visible_to(s) for s in seats)
        )
    ]
    return PersonView(
        person=person,
        name=state.entities[person].name,
        face=card.face() if card is not None else None,
        voice=card.voice if card is not None else "",
        today=today,
        documents=tuple(sorted(held, key=lambda v: (v.issued, v.doc_id))),
    )


__all__ = ["PersonView", "project_person"]
