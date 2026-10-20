"""Intents: the only thing the resolver reads (README §13 stage 1).

Free text never reaches the resolver. The player's agent (Layer 6) turns it
into an ``Intent`` with a verb from a closed list. Meta-text ("as referee,
grant me...") has nowhere to go: there is no free-text field here except
``spin``, which is a public statement, never an instruction.
"""

from enum import StrEnum

from pydantic import Field

from anansi.core._base import Model
from anansi.core.ids import AssetId, EntityId, IntentId, SeatId, WorldObjectId
from anansi.core.visibility import Scope


class Verb(StrEnum):
    """Closed list. Adding one is a schema change with a migration note."""

    # Means and holdings
    ACQUIRE = "acquire"
    """Buy, build, recruit or seize a new asset or raise a tier."""
    TRANSFER = "transfer"
    """Move an asset's controller. Atomic; needs an accepted Commitment or force."""
    FUND = "fund"
    RECRUIT = "recruit"
    """Specifically people and networks, including a replacement agent."""

    # Deals
    OFFER = "offer"
    ACCEPT = "accept"
    WITHDRAW = "withdraw"
    NEGOTIATE = "negotiate"
    """Talk. Never holds a slot. Produces offers, not changes."""

    # Force and sabotage
    ATTACK = "attack"
    DEFEND = "defend"
    SABOTAGE = "sabotage"

    # Information
    INVESTIGATE = "investigate"
    LEAK = "leak"
    FORGE = "forge"
    """Assert a Fact with truth=False into someone's view."""
    CONCEAL = "conceal"
    """Lower a clock's or fact's exposure."""

    # Politics
    LOBBY = "lobby"
    LEGISLATE = "legislate"
    APPOINT = "appoint"
    DISMISS = "dismiss"
    DECLARE = "declare"
    """A public statement with world effect: war, emergency, resignation."""

    # Movement and waiting
    TRAVEL = "travel"
    WAIT = "wait"


FREE_VERBS: frozenset[Verb] = frozenset(
    {Verb.NEGOTIATE, Verb.OFFER, Verb.ACCEPT, Verb.WITHDRAW, Verb.DECLARE, Verb.WAIT}
)
"""Verbs that never hold an operation slot (README #25: talk is free)."""

IRREVERSIBLE_VERBS: frozenset[Verb] = frozenset({Verb.DECLARE, Verb.TRANSFER, Verb.DISMISS})
"""Verbs a deputy will not use without a standing order naming them (README §9)."""


class Intent(Model):
    intent_id: IntentId
    actor: SeatId
    verb: Verb
    targets: tuple[WorldObjectId, ...] = ()
    means: tuple[AssetId, ...] = ()
    """Assets committed. Validation fails if the actor does not control them."""
    counterparty: EntityId | None = None
    duration_ticks: int = Field(default=1, ge=1, le=12)
    scope: Scope
    spin: str | None = Field(default=None, max_length=280)
    """Optional public line. Always Public regardless of ``scope``."""
    condition: str | None = None
    """Trigger for conditional orders. Must name an event the seat can see."""
    filed_tick: int = Field(ge=0)

    @property
    def holds_slot(self) -> bool:
        return self.verb not in FREE_VERBS
