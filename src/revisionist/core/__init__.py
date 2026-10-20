"""Pure, deterministic core: schema, fold, projection, referee.

Nothing in this package may perform I/O, read the clock, call a model, or
use unseeded randomness. ``tests/test_purity.py`` enforces this.

Module map (build plan Layer 1):

- ``ids``         typed identifiers
- ``visibility``  Visibility and Scope, the knowledge ACL
- ``settings``    Session Zero settings the engine reads
- ``ontology``    Entity, Asset, Relationship, Meter, Clock, Fact,
                  ScheduledEvent, Commitment, Seat
- ``intent``      Verb and Intent, the resolver's only input
- ``goals``       secret goals and their win conditions
- ``intel``       goal pieces from investigations, decoys from forgeries
- ``dashboard``   one seat's dashboard; the season report
- ``delta``       the Delta union, the LLM's only output
- ``resolution``  FeasibilityBand, OutcomeTier, TickStage, RejectionReason
- ``events``      every event kind and the Event envelope
- ``state``       State (truth) and View (one seat's knowledge)
"""

SCHEMA_VERSION = "9"
"""Bumped on any change to events, deltas or the ontology."""
