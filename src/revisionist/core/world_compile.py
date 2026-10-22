"""Compile a world bible into the creation events of a campaign (build plan Layer 7).

Pure: the world file arrives already parsed and the campaign ID is given, so the
same world into the same campaign yields the same events. Every problem is found
before anything is emitted. The fold runs last, as a check on what was emitted.
"""

from collections.abc import Callable, Mapping, Sequence

from revisionist.core import events as ev
from revisionist.core.events import Event, Versions
from revisionist.core.fold import FoldError, replay
from revisionist.core.goals import (
    MAX_DEPTH,
    MAX_PARTS,
    AllOf,
    AnyOf,
    Goal,
    WinCondition,
    depth,
    goal_kind,
    goal_met,
    leaves,
    parts,
    progress,
)
from revisionist.core.ids import (
    AssetId,
    CampaignId,
    ClockId,
    CommitmentId,
    EntityId,
    EventId,
    FactId,
    GoalId,
    MeterId,
    RelationshipId,
    SeatId,
    derived_id,
)
from revisionist.core.ontology import (
    Asset,
    AssetKind,
    Character,
    Clause,
    Clock,
    ClockKind,
    Commitment,
    Controller,
    Entity,
    EntityKind,
    Fact,
    Meter,
    Persona,
    Relationship,
    Seat,
    SeatKind,
)
from revisionist.core.state import State
from revisionist.core.visibility import PUBLIC, Scope, Visibility, private_to
from revisionist.core.world import ClauseDecl, GoalDecl, ScopeDecl, ScopeSpec, WorldFile


class WorldError(ValueError):
    """Every problem in the world file, one per line, sorted."""

    problems: tuple[str, ...]

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = tuple(sorted(problems))
        super().__init__("\n".join(self.problems))


def object_id(campaign_id: CampaignId, section: str, key: str) -> str:
    return derived_id(campaign_id, section, key)


def resolve_keys(world: WorldFile, campaign_id: CampaignId) -> dict[str, str]:
    """key -> ID for every keyed object in the world. Raises WorldError on duplicate keys."""
    problems: list[str] = []
    sections = _index(world, problems)
    if problems:
        raise WorldError(problems)
    return {key: object_id(campaign_id, section, key) for key, section in sections.items()}


def compile_world(world: WorldFile, campaign_id: CampaignId, versions: Versions) -> list[Event]:
    """Validate everything first, then emit events. Raises WorldError listing ALL problems."""
    problems: list[str] = []
    sections = _index(world, problems)
    _check(world, sections, problems)
    if problems:
        raise WorldError(problems)

    ids = {key: object_id(campaign_id, section, key) for key, section in sections.items()}
    emitted = _emit(world, ids, sections)
    events = [
        Event(
            event_id=EventId(derived_id(campaign_id, "event", str(seq))),
            campaign_id=campaign_id,
            seq=seq,
            tick=0,
            actor=None,
            scope=scope,
            payload=payload,
            versions=versions,
        )
        for seq, (payload, scope) in enumerate(emitted)
    ]
    try:
        state = replay(events)
    except FoldError as exc:
        raise WorldError([f"fold: {exc}"]) from exc
    met = [f"goal {g.key}: already met at tick 0" for g in world.goals if _met(state, g, ids)]
    if met:
        raise WorldError(met)
    return [_started(e, state) for e in events]


def _started(event: Event, state: State) -> Event:
    """A goal's objective clock starts at the parts the world already gives it."""
    p = event.payload
    if not isinstance(p, ev.ClockCreated) or p.clock.kind is not ClockKind.OBJECTIVE:
        return event
    goal = next((g for g in state.goals.values() if g.clock_id == p.clock.clock_id), None)
    if goal is None:
        return event
    clock = p.clock.model_copy(update={"segments_filled": progress(state, goal.condition)})
    return event.model_copy(update={"payload": ev.ClockCreated(clock=clock)})


# --- keys and references ------------------------------------------------------


def _keyed(world: WorldFile) -> list[tuple[str, str]]:
    """Every keyed object in file order, as (section, key)."""
    keyed = [("entity", d.key) for d in world.entities]
    keyed += [("asset", d.key) for d in world.assets]
    keyed += [("relationship", d.key) for d in world.relationships]
    keyed += [("meter", d.key) for d in world.meters]
    keyed += [("clock", d.key) for d in world.clocks]
    keyed += [("fact", d.key) for d in world.facts]
    keyed += [("commitment", d.key) for d in world.commitments]
    keyed += [("seat", d.key) for d in world.seats]
    keyed += [("goal", d.key) for d in world.goals]
    keyed += [("contest", d.key) for d in world.contests]
    keyed += [("history", d.key) for d in world.schedule]
    return keyed


def _index(world: WorldFile, problems: list[str]) -> dict[str, str]:
    """key -> section. A repeated key is a problem; the first declaration is kept."""
    sections: dict[str, str] = {}
    for section, key in _keyed(world):
        if key in sections:
            problems.append(f"{section} {key}: duplicate key")
        else:
            sections[key] = section
    return sections


def _ref(
    problems: list[str], where: str, ref: str | None, expected: str, sections: Mapping[str, str]
) -> None:
    if ref is None:
        return
    actual = sections.get(ref)
    if actual is None:
        problems.append(f"{where}: unknown {expected} {ref}")
    elif actual != expected:
        problems.append(f"{where}: {ref} is a {actual}, not a {expected}")


def _refs(
    problems: list[str],
    where: str,
    refs: Sequence[str],
    expected: str,
    sections: Mapping[str, str],
) -> None:
    for ref in refs:
        _ref(problems, where, ref, expected, sections)


def _check_scope(
    problems: list[str], where: str, spec: ScopeSpec, sections: Mapping[str, str]
) -> None:
    if not isinstance(spec, ScopeDecl):
        return
    if spec.visibility is Visibility.PUBLIC or not spec.known_by:
        problems.append(f"{where}: bad scope")
    _refs(problems, where, spec.known_by, "seat", sections)


# --- validation ---------------------------------------------------------------


def _check(world: WorldFile, sections: Mapping[str, str], problems: list[str]) -> None:
    _check_entities(world, sections, problems)
    _check_assets(world, sections, problems)
    _check_relationships(world, sections, problems)
    _check_meters(world, sections, problems)
    _check_clocks(world, sections, problems)
    _check_facts(world, sections, problems)
    _check_commitments(world, sections, problems)
    _check_seats(world, sections, problems)
    _check_goals(world, sections, problems)
    _check_roster(world, sections, problems)
    _check_v3(world, problems)


ROSTER_MIN, ROSTER_MAX = 8, 12
PLACES_MIN = 6


def check_history(world: WorldFile) -> list[str]:
    """What a drafted historical world must also be (spec §5), beyond compiling.

    Humans arrive at the character step and goals at the goal step, so a draft
    has neither; the roster offers 8-12 people to choose from. Factions need
    holdings they can act with, so their people must be linked to them.
    """
    problems: list[str] = []
    if not world.start.strip():
        problems.append("world: no start (place and date)")
    if not ROSTER_MIN <= len(world.roster) <= ROSTER_MAX:
        problems.append(f"roster: {len(world.roster)} characters, not {ROSTER_MIN}-{ROSTER_MAX}")
    for s in world.seats:
        if s.controller is Controller.HUMAN:
            problems.append(f"seat {s.key}: a draft has no human seats")
    if world.goals:
        problems.append("goals: a draft has none; players choose them")
    places = [d for d in world.entities if d.kind is EntityKind.LOCATION]
    if len(places) < PLACES_MIN:
        problems.append(f"places: {len(places)} locations, at least {PLACES_MIN}")
    for a in world.assets:
        if a.kind is AssetKind.FORCE and a.location is None:
            problems.append(f"asset {a.key}: a force needs a location")
    parent = {d.key: d.parent for d in world.entities}
    controllers = {a.controller for a in world.assets}
    for d in world.entities:
        if d.kind is EntityKind.PERSON and d.parent is None and d.key in controllers:
            problems.append(f"entity {d.key}: holds assets but has no parent faction")
    for s in world.seats:
        if s.kind is SeatKind.FACTION and s.controller is Controller.AI:
            held = s.embodies in controllers or any(
                parent.get(c) == s.embodies for c in controllers
            )
            if not held:
                problems.append(
                    f"seat {s.key}: {s.embodies} holds no assets; "
                    f"set its people's parent to {s.embodies}"
                )
    return problems


def _check_v3(world: WorldFile, problems: list[str]) -> None:
    """Session Zero v3 sections parse and keep their keys, but compiling them is
    Phase 2 work (core.contest, core.history). Refuse rather than drop them."""
    if world.contests:
        problems.append("contests: not compiled yet (Session Zero v3, Phase 2)")
    if world.schedule:
        problems.append("schedule: not compiled yet (Session Zero v3, Phase 2)")
    if any(c.sides for c in world.roster):
        problems.append("roster: sides not compiled yet (Session Zero v3, Phase 2)")


def _check_roster(world: WorldFile, sections: Mapping[str, str], problems: list[str]) -> None:
    people = {d.key for d in world.entities if d.kind is EntityKind.PERSON}
    held_by = {d.key: d.controller for d in world.assets}
    seen: set[str] = set()
    for c in world.roster:
        where = f"character {c.entity}"
        _ref(problems, where, c.entity, "entity", sections)
        if c.entity in sections and c.entity not in people:
            problems.append(f"{where}: not a person")
        if c.entity in seen:
            problems.append(f"{where}: listed twice")
        seen.add(c.entity)
        for m in c.means:
            _ref(problems, where, m, "asset", sections)
            if m in held_by and held_by[m] != c.entity:
                problems.append(f"{where}: does not control {m}")


def _check_entities(world: WorldFile, sections: Mapping[str, str], problems: list[str]) -> None:
    for d in world.entities:
        where = f"entity {d.key}"
        _ref(problems, where, d.parent, "entity", sections)
        _ref(problems, where, d.location, "entity", sections)


def _check_assets(world: WorldFile, sections: Mapping[str, str], problems: list[str]) -> None:
    for d in world.assets:
        where = f"asset {d.key}"
        _ref(problems, where, d.controller, "entity", sections)
        _ref(problems, where, d.location, "entity", sections)
        _check_scope(problems, where, d.scope, sections)


def _check_relationships(
    world: WorldFile, sections: Mapping[str, str], problems: list[str]
) -> None:
    for d in world.relationships:
        where = f"relationship {d.key}"
        _ref(problems, where, d.a, "entity", sections)
        _ref(problems, where, d.b, "entity", sections)
        _check_scope(problems, where, d.scope, sections)


def _check_meters(world: WorldFile, sections: Mapping[str, str], problems: list[str]) -> None:
    for d in world.meters:
        where = f"meter {d.key}"
        _ref(problems, where, d.owner, "entity", sections)
        _check_scope(problems, where, d.scope, sections)
        if d.lo >= d.hi or not d.lo <= d.value <= d.hi:
            problems.append(f"{where}: out of range")


def _check_clocks(world: WorldFile, sections: Mapping[str, str], problems: list[str]) -> None:
    for d in world.clocks:
        where = f"clock {d.key}"
        _ref(problems, where, d.owner, "seat", sections)
        _ref(problems, where, d.target, "entity", sections)
        _check_scope(problems, where, d.scope, sections)
        if d.segments_filled > d.segments_total:
            problems.append(f"{where}: overfilled")


def _check_facts(world: WorldFile, sections: Mapping[str, str], problems: list[str]) -> None:
    canaries: set[str] = set()
    for d in world.facts:
        where = f"fact {d.key}"
        _ref(problems, where, d.subject, "entity", sections)
        _check_scope(problems, where, d.scope, sections)
        if d.canary is not None:
            if d.canary in canaries:
                problems.append(f"{where}: duplicate canary")
            canaries.add(d.canary)


def _check_commitments(world: WorldFile, sections: Mapping[str, str], problems: list[str]) -> None:
    for d in world.commitments:
        where = f"commitment {d.key}"
        _refs(problems, where, d.parties, "entity", sections)
        for clause in d.clauses:
            _check_clause(problems, where, clause, sections)
        _check_scope(problems, where, d.scope, sections)


def _check_clause(
    problems: list[str], where: str, clause: ClauseDecl, sections: Mapping[str, str]
) -> None:
    _ref(problems, where, clause.party, "entity", sections)
    _ref(problems, where, clause.beneficiary, "entity", sections)
    _ref(problems, where, clause.asset, "asset", sections)


def _check_seats(world: WorldFile, sections: Mapping[str, str], problems: list[str]) -> None:
    entity_kinds = {d.key: d.kind for d in world.entities}
    human = [d.key for d in world.seats if d.controller is Controller.HUMAN]
    if len(human) > world.settings.max_human_seats:
        problems.append(f"seat {human[-1]}: too many human seats")
    for d in world.seats:
        where = f"seat {d.key}"
        _ref(problems, where, d.embodies, "entity", sections)
        _refs(problems, where, d.objectives, "clock", sections)
        if d.agent is not None:
            _ref(problems, where, d.agent.entity, "entity", sections)
            if entity_kinds.get(d.agent.entity, EntityKind.PERSON) is not EntityKind.PERSON:
                problems.append(f"{where}: agent must be a person")


_CONDITION_REFS = {
    "holder": "entity",
    "office": "asset",
    "asset": "asset",
    "entity": "entity",
    "meter": "meter",
    "commitment": "commitment",
    "relationship": "relationship",
}
"""Which section each reference field of a win condition names."""


def _check_goals(world: WorldFile, sections: Mapping[str, str], problems: list[str]) -> None:
    seats = {d.key: d for d in world.seats}
    offices = {d.key for d in world.assets if d.kind is AssetKind.OFFICE}
    canaries = {d.canary for d in world.facts if d.canary is not None}
    owners: set[str] = set()
    for g in world.goals:
        where = f"goal {g.key}"
        _ref(problems, where, g.seat, "seat", sections)
        seat = seats.get(g.seat)
        if seat is not None and seat.kind is not SeatKind.PLAYER:
            problems.append(f"{where}: only a player seat has a goal")
        if g.seat in owners:
            problems.append(f"{where}: seat {g.seat} already has a goal")
        owners.add(g.seat)
        if depth(g.condition) > MAX_DEPTH or len(leaves(g.condition)) > MAX_PARTS:
            problems.append(f"{where}: condition too large")
        for leaf in leaves(g.condition):
            for name, section in _CONDITION_REFS.items():
                ref = getattr(leaf, name, None)
                _ref(problems, where, ref, section, sections)
                if name == "office" and ref in sections and ref not in offices:
                    problems.append(f"{where}: {ref} is not an office")
        if g.canary is not None:
            if g.canary in canaries:
                problems.append(f"{where}: duplicate canary")
            canaries.add(g.canary)


def _bind(c: WinCondition, ids: Mapping[str, str]) -> WinCondition:
    """The same condition with every key replaced by its ID."""
    if isinstance(c, AllOf | AnyOf):
        return c.model_copy(update={"conditions": tuple(_bind(x, ids) for x in c.conditions)})
    update = {name: ids[getattr(c, name)] for name in _CONDITION_REFS if hasattr(c, name)}
    return c.model_copy(update=update)


def _goal_clock(goal_id: str) -> ClockId:
    return ClockId(derived_id(goal_id, "clock"))


def _met(state: State, g: GoalDecl, ids: Mapping[str, str]) -> bool:
    return goal_met(state, _bind(g.condition, ids))


# --- emission -----------------------------------------------------------------


def _maybe[T](ids: Mapping[str, str], key: str | None, to: Callable[[str], T]) -> T | None:
    return None if key is None else to(ids[key])


def _scope(spec: ScopeSpec, ids: Mapping[str, str]) -> Scope:
    if isinstance(spec, ScopeDecl):
        known = frozenset(SeatId(ids[key]) for key in spec.known_by)
        return Scope(visibility=spec.visibility, known_by=known)
    return PUBLIC


def _emit(
    world: WorldFile, ids: Mapping[str, str], sections: Mapping[str, str]
) -> list[tuple[ev.Payload, Scope]]:
    out: list[tuple[ev.Payload, Scope]] = [
        (ev.CampaignCreated(name=world.name, world=world.world), PUBLIC),
        (ev.SettingsSet(settings=world.settings), PUBLIC),
    ]
    for e in world.entities:
        entity = Entity(
            entity_id=EntityId(ids[e.key]),
            kind=e.kind,
            name=e.name,
            parent=_maybe(ids, e.parent, EntityId),
            location=_maybe(ids, e.location, EntityId),
            tags=frozenset(e.tags),
        )
        out.append((ev.EntityCreated(entity=entity), PUBLIC))
    for a in world.assets:
        scope = _scope(a.scope, ids)
        asset = Asset(
            asset_id=AssetId(ids[a.key]),
            kind=a.kind,
            name=a.name,
            tier=a.tier,
            controller=EntityId(ids[a.controller]),
            location=_maybe(ids, a.location, EntityId),
            loyalty=a.loyalty,
            scope=scope,
        )
        out.append((ev.AssetCreated(asset=asset), scope))
    for r in world.relationships:
        scope = _scope(r.scope, ids)
        relationship = Relationship(
            relationship_id=RelationshipId(ids[r.key]),
            a=EntityId(ids[r.a]),
            b=EntityId(ids[r.b]),
            disposition=r.disposition,
            price_tags=frozenset(r.price_tags),
            leverage_tags=frozenset(r.leverage_tags),
            scope=scope,
        )
        out.append((ev.RelationshipSet(relationship=relationship), scope))
    for m in world.meters:
        scope = _scope(m.scope, ids)
        meter = Meter(
            meter_id=MeterId(ids[m.key]),
            owner=EntityId(ids[m.owner]),
            name=m.name,
            value=m.value,
            lo=m.lo,
            hi=m.hi,
            max_delta_per_tick=m.max_delta_per_tick,
            scope=scope,
        )
        out.append((ev.MeterCreated(meter=meter), scope))
    for c in world.clocks:
        scope = _scope(c.scope, ids)
        clock = Clock(
            clock_id=ClockId(ids[c.key]),
            kind=c.kind,
            name=c.name,
            owner=_maybe(ids, c.owner, SeatId),
            target=_maybe(ids, c.target, EntityId),
            segments_total=c.segments_total,
            segments_filled=c.segments_filled,
            exposure=c.exposure,
            holds_slot=c.holds_slot,
            scope=scope,
        )
        out.append((ev.ClockCreated(clock=clock), scope))
    for g in world.goals:
        owner = SeatId(ids[g.seat])
        clock = Clock(
            clock_id=_goal_clock(ids[g.key]),
            kind=ClockKind.OBJECTIVE,
            name="Goal",
            owner=owner,
            segments_total=parts(g.condition),
            scope=private_to(owner),
        )
        out.append((ev.ClockCreated(clock=clock), clock.scope))
    for f in world.facts:
        scope = _scope(f.scope, ids)
        fact = Fact(
            fact_id=FactId(ids[f.key]),
            subject=EntityId(ids[f.subject]),
            predicate=f.predicate,
            obj=_fact_obj(f.obj, ids, sections),
            truth=f.truth,
            exposure=f.exposure,
            scope=scope,
            canary=f.canary,
        )
        out.append((ev.FactAsserted(fact=fact), scope))
    for cm in world.commitments:
        scope = _scope(cm.scope, ids)
        commitment = Commitment(
            commitment_id=CommitmentId(ids[cm.key]),
            parties=frozenset(EntityId(ids[p]) for p in cm.parties),
            clauses=tuple(_clause(c, ids) for c in cm.clauses),
            status=cm.status,
            scope=scope,
        )
        out.append((ev.CommitmentCreated(commitment=commitment), scope))
    for s in world.seats:
        seat = Seat(
            seat_id=SeatId(ids[s.key]),
            kind=s.kind,
            name=s.name,
            embodies=EntityId(ids[s.embodies]),
            controller=s.controller,
            slots=s.slots if s.slots is not None else world.settings.starting_slots,
            objectives=frozenset(ClockId(ids[k]) for k in s.objectives)
            | {_goal_clock(ids[g.key]) for g in world.goals if g.seat == s.key},
            standing_orders=s.standing_orders,
        )
        out.append((ev.SeatCreated(seat=seat), PUBLIC))
    for s in world.seats:
        if s.agent is None:
            continue
        persona = Persona(
            name=s.agent.name,
            role=s.agent.role,
            voice=s.agent.voice,
            backstory=s.agent.backstory,
            relationship=s.agent.relationship,
        )
        assigned = ev.AgentAssigned(
            seat_id=SeatId(ids[s.key]),
            agent=EntityId(ids[s.agent.entity]),
            persona=persona,
        )
        out.append((assigned, PUBLIC))
    for g in world.goals:
        condition = _bind(g.condition, ids)
        owner = SeatId(ids[g.seat])
        goal = Goal(
            goal_id=GoalId(ids[g.key]),
            seat_id=owner,
            text=g.text,
            condition=condition,
            kind=goal_kind(condition),
            clock_id=_goal_clock(ids[g.key]),
            scope=private_to(owner),
            canary=g.canary,
        )
        out.append((ev.GoalSet(goal=goal), goal.scope))
    for ch in world.roster:
        character = Character(
            entity_id=EntityId(ids[ch.entity]),
            pitch=ch.pitch,
            reach=ch.reach,
            composite=ch.composite,
            means=frozenset(AssetId(ids[m]) for m in ch.means),
        )
        out.append((ev.CharacterListed(character=character), PUBLIC))
    if world.start:
        out.append((ev.SessionZeroOpened(start=world.start, notes=world.notes), PUBLIC))
    return out


def _fact_obj(
    obj: str, ids: Mapping[str, str], sections: Mapping[str, str]
) -> EntityId | AssetId | ClockId | str:
    match sections.get(obj):
        case "entity":
            return EntityId(ids[obj])
        case "asset":
            return AssetId(ids[obj])
        case "clock":
            return ClockId(ids[obj])
        case _:
            return obj


def _clause(decl: ClauseDecl, ids: Mapping[str, str]) -> Clause:
    return Clause(
        kind=decl.kind,
        party=EntityId(ids[decl.party]),
        beneficiary=EntityId(ids[decl.beneficiary]),
        asset=_maybe(ids, decl.asset, AssetId),
        verb=decl.verb,
        amount=decl.amount,
        until_tick=decl.until_tick,
    )
