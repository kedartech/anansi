"""Historical Session Zero (Layer 10 item 1; spec §5).

``establish`` locks a point in history: the GM drafts the world bible from the
record at that date, the Layer 7 compiler validates it, and only a draft that
compiles is ever written to the log. The settings are the table's, never the
model's.
"""

import json
import logging
import re
import unicodedata
from collections.abc import Callable, Sequence
from datetime import date
from typing import Any, Literal

from pydantic import TypeAdapter, ValidationError

from revisionist.core import events as ev
from revisionist.core import goals, roster
from revisionist.core.canary import canaries_hidden_from_human
from revisionist.core.events import CallCost, Event
from revisionist.core.fold import replay
from revisionist.core.ids import (
    AssetId,
    CampaignId,
    ClockId,
    EntityId,
    EventId,
    GoalId,
    SeatId,
    derived_id,
)
from revisionist.core.ontology import (
    Asset,
    AssetKind,
    Character,
    Clock,
    ClockKind,
    Controller,
    Entity,
    EntityKind,
    Persona,
    Phase,
)
from revisionist.core.project import project
from revisionist.core.settings import Settings
from revisionist.core.state import State
from revisionist.core.visibility import PUBLIC, Scope, private_to
from revisionist.core.world import WorldFile
from revisionist.core.world_compile import WorldError, check_history, compile_world
from revisionist.engine import campaign as cmp
from revisionist.engine.agent import AGENT_MODEL
from revisionist.engine.ids import new_id
from revisionist.engine.llm import RESOLVER_MODEL, Request, Transport, TransportError, prompt_text
from revisionist.engine.pitch import SettingCard, read_pitch
from revisionist.engine.store import EventStore, Sealer, StoreError, load_state

log = logging.getLogger(__name__)

SESSION_ZERO_MODEL = RESOLVER_MODEL
"""Session Zero runs on the frontier model, like the resolver (spec §20)."""


def _draft_schema() -> dict[str, Any]:
    """The world file the draft may return. Session Zero v3 sections (contests,
    history's clock, sides, start date) stay out until Phase 2 compiles them."""
    schema = WorldFile.model_json_schema()
    for name in ("start_date", "contests", "schedule"):
        schema["properties"].pop(name, None)
    defs = schema.get("$defs", {})
    defs.get("CharacterDecl", {}).get("properties", {}).pop("sides", None)
    for name in ("ContestDecl", "HistoryDecl", "SideDecl"):
        defs.pop(name, None)
    return schema


DRAFT_SCHEMA = _draft_schema()
PLACE_MAX = 80
TRIES = 2
"""One draft and one retry with the compiler's problems fed back."""


class SessionZeroError(RuntimeError):
    """The draft never compiled. Nothing was written."""

    def __init__(self, problems: Sequence[str], costs: Sequence[CallCost]) -> None:
        self.problems = tuple(problems)
        self.costs = tuple(costs)
        super().__init__("the world draft did not compile:\n" + "\n".join(self.problems))


def _slug(place: str) -> str:
    return "history-" + derived_id("establish", place).lower()[:10]


def _request(
    place: str, settings: Settings, problems: Sequence[str], effort: int | None = None
) -> Request:
    prompt = (
        f"## Place and date\n{place}\n\n"
        "## Settings (fixed by the table; for tone only)\n"
        + settings.model_dump_json()
        + "\n\nDraft the world file."
    )
    if problems:
        prompt += (
            "\n\n## Your last draft was refused. Fix every problem and return the whole file.\n"
            + "\n".join(problems)
        )
    return Request(
        call_class="session_zero",
        model=SESSION_ZERO_MODEL,
        system=prompt_text("establish"),
        prompt=prompt,
        schema=DRAFT_SCHEMA,
        max_usd=2.0,
        effort=effort,
    )


def _check(output: object, place: str, settings: Settings) -> tuple[WorldFile | None, list[str]]:
    if not isinstance(output, dict):
        return None, ["the reply was not a world file"]
    try:
        draft = WorldFile.model_validate(output)
    except ValidationError as exc:
        return None, [
            f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in exc.errors()[:20]
        ]
    world = draft.model_copy(update={"world": _slug(place), "start": place, "settings": settings})
    problems = check_history(world)
    try:
        compile_world(world, CampaignId("0" * 26), cmp.versions())
    except WorldError as exc:
        problems += list(exc.problems)
    return (world if not problems else None), problems


def draft_world(
    place: str, settings: Settings, transport: Transport, effort: int | None = None
) -> tuple[WorldFile, list[CallCost]]:
    """Ask for the world bible, check it, and retry once with the problems.

    ``effort`` (1-4, low..max) is passed to the model; None keeps its default.
    """
    place = place.strip()
    if not place or len(place) > PLACE_MAX:
        raise SessionZeroError([f"a place and date of 1-{PLACE_MAX} characters"], [])
    costs: list[CallCost] = []
    problems: list[str] = []
    for _ in range(TRIES):
        reply = transport.call(_request(place, settings, problems, effort))
        costs.append(reply.cost("session_zero"))
        world, problems = _check(reply.output, place, settings)
        if world is not None:
            return world, costs
    raise SessionZeroError(problems, costs)


def establish(
    store: EventStore,
    place: str,
    settings: Settings,
    transport: Transport,
    effort: int | None = None,
) -> CampaignId:
    """Draft, compile and open a new campaign at ``place``. Its first tick waits for
    the character, goal and brief steps."""
    world, costs = draft_world(place, settings, transport, effort)
    return _open_campaign(store, world, costs)


def _open_campaign(store: EventStore, world: WorldFile, costs: Sequence[CallCost]) -> CampaignId:
    campaign_id = CampaignId(new_id())
    sealer: Sealer | None = getattr(store, "sealer", None)
    if sealer is not None:
        sealer.begin(campaign_id)
    store.append_all(compile_world(world, campaign_id, cmp.versions()))
    cmp.log_cost(store, campaign_id, "session_zero", costs)
    return campaign_id


# --- Session Zero v3: pitch, card, confirm (spec §5.2) -------------------------


def propose(
    pitch: str, transport: Transport, effort: int | None = None
) -> tuple[SettingCard, list[CallCost]]:
    """Read the pitch into a setting card for the host. Logs nothing: a card is
    not a campaign until the host confirms it, so the caller keeps the costs and
    hands them to ``confirm``. Raises ``PitchError``."""
    return read_pitch(pitch, transport, effort)


def confirm(
    store: EventStore,
    card: SettingCard,
    choice: int,
    settings: Settings,
    transport: Transport,
    *,
    strikes: Sequence[int] = (),
    end_date: date | None = None,
    real_days: int | None = None,
    effort: int | None = None,
    progress: Callable[[str], None] | None = None,
    card_costs: Sequence[CallCost] = (),
) -> CampaignId:
    """Draft (``engine.draft``), compile and open the campaign for start point
    ``choice`` (1-based), without the struck schedule items (1-based). The end
    date and real-life length go into the settings; ``card_costs`` are logged with
    the draft's. Raises ``SessionZeroError`` (draft) or ``StepRefusedError`` (a bad
    choice, strike, end date or length)."""
    if not 1 <= choice <= len(card.start_points):
        raise StepRefusedError(f"Pick a start point from 1 to {len(card.start_points)}.")
    start = card.start_points[choice - 1]
    if any(not 1 <= n <= len(start.schedule) for n in strikes):
        raise StepRefusedError(
            f"Strike items from 1 to {len(start.schedule)} of that start point's schedule."
        )
    if end_date is not None and end_date <= start.start_date:
        raise StepRefusedError(
            f"The end date must come after the start, {start.start_date.isoformat()}."
        )
    if real_days is not None and not 1 <= real_days <= 365:
        raise StepRefusedError("The real-life length is 1 to 365 days.")
    struck = set(strikes)
    schedule = tuple(item for n, item in enumerate(start.schedule, 1) if n not in struck)
    start = start.model_copy(update={"schedule": schedule})
    dates = {k: v for k, v in (("end_date", end_date), ("real_days", real_days)) if v is not None}
    settings = settings.model_copy(update=dates)
    # Imported here: engine.draft imports this module for its error and retry limit.
    from revisionist.engine import draft as world_draft

    world, costs = world_draft.draft(card, start, settings, transport, effort, progress=progress)
    return _open_campaign(store, world, [*card_costs, *costs])


# --- the private steps: character, goal, brief, agent (spec §5 steps 3-5) --------


class StepRefusedError(ValueError):
    """A Session Zero step the player has to redo. The message is safe to show them."""


Route = Literal["history", "could_have", "own"]
ROUTES: tuple[Route, ...] = ("history", "could_have", "own")
PICK_TIER_MAX = 3
PICK_MEANS_MAX = 3


def _events(
    state: State,
    campaign_id: CampaignId,
    items: Sequence[tuple[ev.Payload, Scope, SeatId | None]],
    *,
    first_id: str | None = None,
) -> list[Event]:
    """``items`` as events after ``state``. ``first_id`` names the first event deterministically."""
    return [
        Event(
            event_id=EventId(first_id if (i == 0 and first_id) else new_id()),
            campaign_id=campaign_id,
            seq=state.last_seq + 1 + i,
            tick=state.tick,
            actor=actor,
            scope=scope,
            payload=payload,
            versions=cmp.versions(),
        )
        for i, (payload, scope, actor) in enumerate(items)
    ]


def _log(
    store: EventStore,
    campaign_id: CampaignId,
    items: Sequence[tuple[ev.Payload, Scope, SeatId | None]],
    *,
    first_id: str | None = None,
) -> None:
    """Append ``items`` in one batch. ``first_id`` names the first event deterministically."""
    state = load_state(store, campaign_id)
    store.append_all(_events(state, campaign_id, items, first_id=first_id))


def _open(store: EventStore, campaign_id: CampaignId) -> State:
    state = load_state(store, campaign_id)
    if state.phase is not Phase.SESSION_ZERO:
        raise StepRefusedError("Session Zero is not open in this campaign.")
    return state


def _start(store: EventStore, campaign_id: CampaignId) -> str:
    for e in store.read(campaign_id):
        if isinstance(e.payload, ev.SessionZeroOpened):
            return e.payload.start
    return ""


def _line(state: State, ch: Character) -> str:
    name = state.entities[ch.entity_id].name
    tags = (" (composite)" if ch.composite else "") + (" [taken]" if ch.claimed_by else "")
    stands = [
        f"{state.contests[s.contest_id].name}: {state.entities[s.side].name}" for s in ch.sides
    ]
    sides = f" Sides: {'; '.join(stands)}." if stands else ""
    return f"{name}{tags}: {ch.pitch} Reach: {ch.reach}{sides}"


def _eligible(state: State, entity: EntityId) -> bool:
    """The character rule (spec §5.5), which applies once the world has contests."""
    return not state.contests or roster.qualifies(state, entity)


def _unqualified(state: State, entity: EntityId) -> StepRefusedError:
    name = state.entities[entity].name
    near = roster.nearest(state, entity)
    tail = f" Nearest who does: {state.entities[near].name}." if near is not None else ""
    return StepRefusedError(
        f"{name} does not meet the character rule: a playable character needs a force, "
        "office or network of tier 2 or more in one of the world's contests." + tail
    )


def roster_lines(state: State) -> list[str]:
    """The roster as every player sees it: public, with who is already taken."""
    return [
        _line(state, ch)
        for ch in sorted(state.roster.values(), key=lambda c: state.entities[c.entity_id].name)
    ]


_PARTICLES = frozenset({"de", "di", "da", "del", "von", "van", "of", "the"})


def _words(name: str) -> list[str]:
    """A name as comparable words: no case, accents, punctuation or particles."""
    return [w for w in _name_key(name).split() if w not in _PARTICLES]


def _initial_fit(a: str, b: str) -> bool:
    return a == b or (len(a) == 1 and b.startswith(a)) or (len(b) == 1 and a.startswith(b))


def _same_name(typed: list[str], listed: list[str]) -> bool:
    """Equal word for word, a lone initial standing for any word it starts, or equal
    once initials are left out ("Thomas J. Jackson" against "Thomas Jackson")."""
    if len(typed) == len(listed) and all(
        _initial_fit(a, b) for a, b in zip(typed, listed, strict=True)
    ):
        return True
    full_t, full_l = [w for w in typed if len(w) > 1], [w for w in listed if len(w) > 1]
    return bool(full_t) and full_t == full_l


def _resolve(state: State, name: str) -> list[Character]:
    """Who on the roster a typed name means: the first step below that finds anyone."""
    chars = list(state.roster.values())
    typed = _words(name)
    wanted = name.strip().casefold()
    steps: list[Callable[[], list[Character]]] = [
        lambda: [c for c in chars if state.entities[c.entity_id].name.casefold() == wanted],
        lambda: [c for c in chars if _same_name(typed, _words(state.entities[c.entity_id].name))],
        lambda: [
            c
            for c in chars
            if len(typed) == 1 and typed[0] in _words(state.entities[c.entity_id].name)
        ],
        lambda: [c for c in chars if any(_same_name(typed, _words(a)) for a in c.aliases)],
    ]
    if not typed:
        return steps[0]()
    for step in steps:
        found = step()
        if found:
            return found
    return []


def _match(state: State, name: str) -> Character | None:
    """The one listed person a typed name means; None if no one, a refusal if several."""
    found = _resolve(state, name)
    if len(found) > 1:
        names = ", ".join(sorted(state.entities[c.entity_id].name for c in found))
        raise StepRefusedError(
            f"{name.strip()!r} could be more than one person: {names}. Type the full name."
        )
    return found[0] if found else None


def _find(state: State, name: str) -> Character:
    ch = _match(state, name)
    if ch is None:
        raise StepRefusedError(f"No one called {name.strip()!r} is on the roster.")
    return ch


def _unclaimed_seat(state: State, seat: SeatId) -> None:
    if seat in state.seats:
        raise StepRefusedError(f"You already play {state.seats[seat].name}.")


def claim(store: EventStore, campaign_id: CampaignId, seat: SeatId, name: str) -> str:
    """Take a character from the roster. Returns the seat's name.

    The event ID is derived from the character, so two players racing for the
    same person cannot both land: the store refuses the second (decision 35).
    """
    state = _open(store, campaign_id)
    _unclaimed_seat(state, seat)
    ch = _find(state, name)
    taken = StepRefusedError(f"{state.entities[ch.entity_id].name} is already taken.")
    if ch.claimed_by is not None:
        raise taken
    if not _eligible(state, ch.entity_id):
        raise _unqualified(state, ch.entity_id)
    claimed = ev.CharacterClaimed(seat_id=seat, entity_id=ch.entity_id)
    try:
        _log(
            store,
            campaign_id,
            [(claimed, PUBLIC, seat)],
            first_id=derived_id(campaign_id, "claim", ch.entity_id),
        )
    except StoreError as exc:
        raise taken from exc
    return state.entities[ch.entity_id].name


SEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"matches": {"type": "array", "items": {"type": "string"}, "maxItems": 5}},
    "required": ["matches"],
}


def search(
    store: EventStore,
    campaign_id: CampaignId,
    seat: SeatId | None,
    description: str,
    transport: Transport,
) -> list[str]:
    """Roster characters that fit what the player wants to play, best first.

    The model sees only the public roster and may only name its people.
    """
    wanted = description.strip()[:400]
    if not wanted:
        raise StepRefusedError("Say what you want to play, e.g. /character search a poet at court.")
    state = _open(store, campaign_id)
    free = {
        ch.entity_id: ch
        for ch in state.roster.values()
        if ch.claimed_by is None and _eligible(state, ch.entity_id)
    }
    listing = [
        {"id": e, "name": state.entities[e].name, "pitch": ch.pitch, "reach": ch.reach}
        for e, ch in sorted(free.items())
    ]
    request = Request(
        call_class="character_search",
        model=AGENT_MODEL,
        system=prompt_text("character_search"),
        prompt="## Roster\n"
        + json.dumps(listing)
        + "\n\n## What the player wants to play\n"
        + wanted,
        schema=SEARCH_SCHEMA,
        max_usd=0.1,
    )
    named = [c.entity_id for c in _resolve(state, wanted) if c.entity_id in free]
    hits: list[EntityId] = []
    for _ in range(2):  # a cheap call; one empty answer is often the model being too strict
        try:
            reply = transport.call(request)
        except TransportError as exc:
            log.warning("character search model call failed; matching words: %s", exc)
            break
        cmp.log_cost(store, campaign_id, "character_search", [reply.cost("character_search")], seat)
        found = reply.output.get("matches", []) if isinstance(reply.output, dict) else []
        hits = [EntityId(x) for x in found if isinstance(x, str) and x in free]
        if hits:
            break
    if not hits:
        hits = _word_matches(state, free, wanted)
        log.info(
            "character search: model found none of %d free, words found %d", len(free), len(hits)
        )
    lines = [_line(state, free[h]) for h in dict.fromkeys([*named, *hits])][:5]
    people = _record_people(store, campaign_id, seat, state, wanted, transport)
    if people:
        lines += [RECORD_HEADER, *(f"{name}: {note}" for name, note in people)]
    hidden = canaries_hidden_from_human(state, (seat,) if seat else ())
    return [line for line in lines if not any(c in line for c in hidden)]


RECORD_HEADER = "From the record, not on the roster (play one with /character pick <name>):"
RECORD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "people": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "maxLength": 60},
                    "note": {"type": "string", "maxLength": 160},
                },
                "required": ["name", "note"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["people"],
    "additionalProperties": False,
}


def _record_people(
    store: EventStore,
    campaign_id: CampaignId,
    seat: SeatId | None,
    state: State,
    wanted: str,
    transport: Transport,
) -> list[tuple[str, str]]:
    """Real people the record places at the start who fit ``wanted``, none already in the world."""
    request = Request(
        call_class="character_record",
        model=SESSION_ZERO_MODEL,
        system=prompt_text("character_record"),
        # Only the public roster goes in: the player's own words share this prompt, so
        # anything listed here could be talked back out. The rest is filtered after.
        prompt=f"## Start\n{_start(store, campaign_id)}\n\n## Already on the roster\n"
        + json.dumps(sorted(state.entities[e].name for e in state.roster))
        + "\n\n## What the player wants to play\n"
        + wanted,
        schema=RECORD_SCHEMA,
        max_usd=0.3,
    )
    try:
        reply = transport.call(request)
    except TransportError as exc:
        log.warning("character record search failed; roster only: %s", exc)
        return []
    cmp.log_cost(store, campaign_id, "character_record", [reply.cost("character_record")], seat)
    raw = reply.output.get("people") if isinstance(reply.output, dict) else None
    if not isinstance(raw, list):
        return []
    seen = {_name_key(e.name) for e in state.entities.values()}
    people: list[tuple[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name, note = item.get("name"), item.get("note")
        if not isinstance(name, str) or not isinstance(note, str):
            continue
        name, note = _one_line(name)[:60], _one_line(note)[:160]
        key = _name_key(name)
        if not key or any(_same_person(key, k) for k in seen):
            continue
        seen.add(key)
        people.append((name, note))
    return people[:5]


def _one_line(text: str) -> str:
    """Model text as one clean line: no newlines or runs of spaces to forge output with."""
    return " ".join(unicodedata.normalize("NFKC", text).split())


def _name_key(name: str) -> str:
    """A name compared loosely: case, accents, spacing and punctuation do not count."""
    flat = unicodedata.normalize("NFKD", _one_line(name).casefold())
    return " ".join(
        re.sub(r"[^\w\s]", " ", "".join(c for c in flat if not unicodedata.combining(c))).split()
    )


def _same_person(a: str, b: str) -> bool:
    """Loose keys that name the same person: equal, or one holds all the other's words
    ("Federico" against "Federico da Montefeltro")."""
    if a == b:
        return True
    wa, wb = set(a.split()), set(b.split())
    return bool(wa and wb) and (wa <= wb or wb <= wa)


_SKIP = frozenset(
    "the and who with for that someone person character play want like into from".split()
)


def _stem(word: str) -> str:
    return word[:6] if len(word) > 6 else word


def _word_matches(state: State, free: dict[EntityId, Character], wanted: str) -> list[EntityId]:
    """The fallback when the model names nobody: roster lines sharing the player's words."""
    words = {_stem(w) for w in re.findall(r"[a-z]+", wanted.casefold())} - _SKIP
    words = {w for w in words if len(w) > 2}
    scored = []
    for e, ch in free.items():
        text = f"{state.entities[e].name} {ch.pitch} {ch.reach}".casefold()
        score = sum(1 for w in words if w in text)
        if score:
            scored.append((-score, state.entities[e].name, e))
    return [e for _, _, e in sorted(scored)]


PICK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "existed": {"type": "boolean"},
        "placed": {"type": "boolean"},
        "note": {"type": "string", "maxLength": 200},
        "name": {"type": "string", "maxLength": 60},
        "pitch": {"type": "string", "maxLength": 200},
        "reach": {"type": "string", "maxLength": 200},
        "composite": {"type": "boolean"},
        "same_as": {"type": "string", "maxLength": 60},
        "means": {
            "type": "array",
            "maxItems": PICK_MEANS_MAX,
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "maxLength": 60},
                    "kind": {"enum": [k.value for k in AssetKind]},
                    "tier": {"type": "integer", "minimum": 0, "maximum": 5},
                },
                "required": ["name", "kind", "tier"],
            },
        },
    },
    "required": ["existed", "placed", "note"],
}


def pick(
    store: EventStore, campaign_id: CampaignId, seat: SeatId, name: str, transport: Transport
) -> str:
    """Play someone the roster leaves out, if the record places them at the start.

    The frontier model checks they existed and could act at that date and
    proposes their means; the engine caps the means and adds them to the world.
    """
    state = _open(store, campaign_id)
    _unclaimed_seat(state, seat)
    wanted = name.strip()[:60]
    if not wanted:
        raise StepRefusedError("Name the person you want to play.")
    listed = _match(state, wanted)
    if listed is not None:
        return claim(store, campaign_id, seat, state.entities[listed.entity_id].name)
    known = sorted(e.name for e in state.entities.values())
    if wanted.casefold() in {k.casefold() for k in known}:
        raise StepRefusedError(f"{wanted} is already in the world but not on the roster.")
    reply = transport.call(
        Request(
            call_class="character_pick",
            model=SESSION_ZERO_MODEL,
            system=prompt_text("character_pick"),
            prompt=f"## Start\n{_start(store, campaign_id)}\n\n## The person\n{wanted}\n\n"
            "## Already in the world\n"
            + json.dumps(known)
            + "\n\n## On the roster\n"
            + json.dumps(sorted(state.entities[c.entity_id].name for c in state.roster.values())),
            schema=PICK_SCHEMA,
            max_usd=0.5,
        )
    )
    cmp.log_cost(store, campaign_id, "character_pick", [reply.cost("character_pick")], seat)
    out = reply.output if isinstance(reply.output, dict) else {}
    same_as = out.get("same_as")
    if isinstance(same_as, str):
        listed = next(
            (
                c
                for c in state.roster.values()
                if state.entities[c.entity_id].name.casefold() == same_as.strip().casefold()
            ),
            None,
        )
        if listed is not None:
            return claim(store, campaign_id, seat, state.entities[listed.entity_id].name)
    if not (out.get("existed") and out.get("placed")):
        note = str(out.get("note", ""))[:200]
        raise StepRefusedError(f"The record does not place {wanted} at the start. {note}".strip())
    person = str(out.get("name") or wanted)[:60]
    if person.casefold() in {k.casefold() for k in known}:
        raise StepRefusedError(f"{person} is already in the world but not on the roster.")
    entity = EntityId(derived_id(campaign_id, "pick", person.casefold()))
    assets = []
    raw_means = out.get("means")
    means: list[Any] = raw_means if isinstance(raw_means, list) else []
    for i, m in enumerate(means[:PICK_MEANS_MAX]):
        try:
            assets.append(
                Asset(
                    asset_id=AssetId(derived_id(entity, "means", i)),
                    kind=AssetKind(m["kind"]),
                    name=str(m["name"])[:60],
                    tier=min(int(m["tier"]), PICK_TIER_MAX),
                    controller=entity,
                    scope=PUBLIC,
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    character = Character(
        entity_id=entity,
        pitch=str(out.get("pitch", ""))[:200],
        reach=str(out.get("reach", ""))[:200],
        composite=bool(out.get("composite", False)),
        means=frozenset(a.asset_id for a in assets),
    )
    items: list[tuple[ev.Payload, Scope, SeatId | None]] = [
        (
            ev.EntityCreated(entity=Entity(entity_id=entity, kind=EntityKind.PERSON, name=person)),
            PUBLIC,
            None,
        ),
        *((ev.AssetCreated(asset=a), PUBLIC, None) for a in assets),
        (ev.CharacterListed(character=character), PUBLIC, None),
    ]
    first = derived_id(campaign_id, "pick-entity", entity)
    if state.contests:
        added = replay(_events(state, campaign_id, items, first_id=first), initial=state)
        if not roster.qualifies(added, entity):
            raise _unqualified(added, entity)
    try:
        _log(store, campaign_id, items, first_id=first)
    except StoreError as exc:
        raise StepRefusedError(f"{person} was just added by someone else.") from exc
    return claim(store, campaign_id, seat, person)


_CONDITION = TypeAdapter(goals.WinCondition)
GOAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "options": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "maxLength": 280},
                    "condition": _CONDITION.json_schema(),
                },
                "required": ["text", "condition"],
            },
        },
        "rewording": {"type": ["string", "null"], "maxLength": 280},
    },
    "required": ["options"],
}

_ROUTE_ASK = {
    "history": (
        "Offer up to five goals, from easy to hard, at least one hard: what this "
        "character really tried for at this date."
    ),
    "could_have": (
        "Offer up to five goals, from easy to hard, at least one hard: aims this "
        "character was placed to try that history did not see."
    ),
    "own": "The player wrote their own goal, below. Turn it into one option if it can be "
    "checked; if it cannot, return no options and a rewording that could be.",
}


def _seat_of(state: State, seat: SeatId) -> None:
    if seat not in state.seats:
        raise StepRefusedError("Pick a character first.")


def _warning(text: str | None) -> str:
    return f" Warning: {text}" if text else ""


def _three(options: list[ev.GoalOption]) -> list[ev.GoalOption]:
    """The event holds three; the player still sees the easiest, a middle and the hardest."""
    if len(options) <= 3:
        return options
    return [options[0], options[len(options) // 2], options[-1]]


def propose_goals(
    store: EventStore,
    campaign_id: CampaignId,
    seat: SeatId,
    route: Route,
    transport: Transport,
    text: str = "",
) -> list[str]:
    """The agent offers goals on one route; each is checked before it is shown.

    Returns lines numbered for ``confirm_goal``, each with its kind (Lasting or
    Undoable) and the balance check's flag, if any.
    """
    state = _open(store, campaign_id)
    _seat_of(state, seat)
    if any(g.seat_id == seat for g in state.goals.values()):
        raise StepRefusedError("Your goal is fixed for the season.")
    if route == "own" and not text.strip():
        raise StepRefusedError("Write the goal you want.")
    view = project(state, seat)
    prompt = (
        f"## Start\n{_start(store, campaign_id)}\n\n## What your character knows\n"
        + view.model_dump_json(exclude={"own_pending"})
        + f"\n\n## Route\n{_ROUTE_ASK[route]}"
        + (f"\n\n## The player's goal\n{text.strip()[:280]}" if route == "own" else "")
    )
    reply = transport.call(
        Request(
            call_class="goal",
            model=AGENT_MODEL,
            system=prompt_text("goal"),
            prompt=prompt,
            schema=GOAL_SCHEMA,
            max_usd=0.2,
        )
    )
    cmp.log_cost(store, campaign_id, "goal", [reply.cost("goal")], seat)
    out = reply.output if isinstance(reply.output, dict) else {}
    options: list[ev.GoalOption] = []
    problems: list[str] = []
    for raw in out.get("options", [])[:5]:
        try:
            condition = _CONDITION.validate_python(raw.get("condition"))
            why = goals.vet(view, state, condition)
        except (ValidationError, AttributeError, ValueError) as exc:
            why = [f"not a condition the engine can check ({type(exc).__name__})"]
        if why:
            problems += why
            continue
        if any(goals.goal_met(state, leaf) for leaf in goals.leaves(condition)):
            problems.append("keeps what you already hold; aim for something you must win")
            continue
        options.append(
            ev.GoalOption(
                text=str(raw.get("text", ""))[:280],
                condition=condition,
                kind=goals.goal_kind(condition),
            )
        )
    if not options:
        rewording = out.get("rewording") or ""
        hint = f" Try: {rewording}" if rewording else ""
        detail = f" ({problems[0]})" if problems else ""
        raise StepRefusedError(f"That cannot be written as a goal the engine checks{detail}.{hint}")
    # Easy to hard (D4). Sorted before logging, so label N is stored option N.
    options.sort(key=lambda o: goals.difficulty(state, seat, o.condition))
    options = _three(options)
    proposed = ev.GoalProposed(seat_id=seat, route=route, options=tuple(options))
    _log(store, campaign_id, [(proposed, private_to(seat), seat)])
    return offer_lines(state, proposed)


def offer_lines(state: State, proposed: ev.GoalProposed) -> list[str]:
    """One offer's lines, labelled for ``confirm_goal``. The web client reads past
    offers back from the log with this, so they read as they did when made."""
    seat, route = proposed.seat_id, proposed.route
    lines = []
    for n, o in enumerate(proposed.options, 1):
        b = goals.balance(state, seat, o.condition)
        note = f" Balance: {b.suggestion}" if b.flag else ""
        d = goals.difficulty(state, seat, o.condition)
        rank = "Easy" if d <= EASY_MAX else "Hard" if d <= HARD_MAX else "Long shot"
        warn = _warning(goals.pressure_warning(state, seat, o.condition))
        lines.append(f"{_ROUTE_LETTER[route]}{n}. {o.text} [{rank}, {o.kind.value}]{note}{warn}")
    return lines


EASY_MAX = 4
"""``goals.difficulty`` at or below this reads Easy: one modest asset or office to take."""
HARD_MAX = 8
"""At or below this reads Hard; above it, Long shot (several, or a rival's best)."""

_ROUTE_LETTER: dict[Route, str] = {"history": "H", "could_have": "C", "own": "O"}
_LETTER_ROUTE: dict[str, Route] = {v: k for k, v in _ROUTE_LETTER.items()}
_LABEL = re.compile(r"([HCO])?(\d+)", re.IGNORECASE)


def _offers(
    store: EventStore, campaign_id: CampaignId, seat: SeatId
) -> dict[Route, ev.GoalProposed]:
    """The latest offer on each route, so each list keeps its own labels."""
    latest: dict[Route, ev.GoalProposed] = {}
    for e in store.read(campaign_id):
        if isinstance(e.payload, ev.GoalProposed) and e.payload.seat_id == seat:
            latest[e.payload.route] = e.payload
    return latest


def confirm_goal(
    store: EventStore, campaign_id: CampaignId, seat: SeatId, choice: str | int
) -> str:
    """Fix one offered goal for the season: its objective clock and ``goal_set``."""
    state = _open(store, campaign_id)
    _seat_of(state, seat)
    if any(g.seat_id == seat for g in state.goals.values()):
        raise StepRefusedError("Your goal is fixed for the season.")
    label = _LABEL.fullmatch(str(choice).strip())
    if label is None:
        raise StepRefusedError("Give the label of the goal you choose, e.g. H1.")
    letter, digits = label.groups()
    offers = _offers(store, campaign_id, seat)
    if letter is not None:
        offered = offers.get(_LETTER_ROUTE[letter.upper()])
    elif len(offers) > 1:
        raise StepRefusedError(
            "You have offers from more than one list. Give the label, e.g. H2 or C1."
        )
    else:
        offered = next(iter(offers.values()), None)
    number = int(digits)
    if offered is None or not 1 <= number <= len(offered.options):
        raise StepRefusedError("Choose one of the goals your agent offered.")
    option = offered.options[number - 1]
    why = goals.vet(project(state, seat), state, option.condition)
    if why:
        raise StepRefusedError(f"That goal no longer holds up: {why[0]}.")
    goal_id = GoalId(derived_id(campaign_id, "goal", seat))
    clock = Clock(
        clock_id=ClockId(derived_id(goal_id, "clock")),
        kind=ClockKind.OBJECTIVE,
        name="Goal",
        owner=seat,
        segments_total=goals.parts(option.condition),
        segments_filled=goals.progress(state, option.condition),
        scope=private_to(seat),
    )
    goal = goals.Goal(
        goal_id=goal_id,
        seat_id=seat,
        text=option.text,
        condition=option.condition,
        kind=option.kind,
        clock_id=clock.clock_id,
        scope=private_to(seat),
    )
    _log(
        store,
        campaign_id,
        [
            (ev.ClockCreated(clock=clock), clock.scope, seat),
            (ev.GoalSet(goal=goal), goal.scope, seat),
        ],
    )
    b = goals.balance(state, seat, option.condition)
    held = (
        "It must still hold at season end."
        if option.kind is goals.GoalKind.UNDOABLE
        else ("Once met, it stays met.")
    )
    flag = " The host has to approve it before play begins." if b.flag else ""
    warn = _warning(goals.pressure_warning(state, seat, option.condition))
    return f"Your goal is fixed: {option.text} ({option.kind.value}). {held}{flag}{warn}"


BRIEF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "record": {"type": "string", "maxLength": 1200},
        "pursuit": {"type": "string", "maxLength": 800},
        "route": {"type": "string", "maxLength": 600},
        "thin": {"type": ["string", "null"], "maxLength": 300},
    },
    "required": ["record", "pursuit", "route"],
}
SUGGESTED = "Suggested route (a suggestion, not a rule):"


def history_brief(
    store: EventStore, campaign_id: CampaignId, seat: SeatId, transport: Transport
) -> str:
    """The tick-0 brief (spec §5 step 5): the record to the start, the goal as history
    saw it, and one labelled suggestion. Logged as the seat's private narration."""
    state = _open(store, campaign_id)
    _seat_of(state, seat)
    if not any(g.seat_id == seat for g in state.goals.values()):
        raise StepRefusedError("Fix your goal first.")
    view = project(state, seat)
    reply = transport.call(
        Request(
            call_class="history_brief",
            model=AGENT_MODEL,
            system=prompt_text("history_brief"),
            prompt=f"## Start\n{_start(store, campaign_id)}\n\n## What your character knows\n"
            + view.model_dump_json(exclude={"own_pending"})
            + "\n\nWrite the brief.",
            schema=BRIEF_SCHEMA,
            max_usd=0.2,
        )
    )
    cmp.log_cost(store, campaign_id, "history_brief", [reply.cost("history_brief")], seat)
    out = reply.output if isinstance(reply.output, dict) else {}
    parts = [
        f"As recorded: {str(out.get('record', '')).strip()}",
        f"The goal in history: {str(out.get('pursuit', '')).strip()}",
        f"{SUGGESTED} {str(out.get('route', '')).strip()}",
    ]
    if out.get("thin"):
        parts.append(f"Where the record is thin: {str(out['thin']).strip()}")
    text = "\n\n".join(parts)
    narration = ev.NarrationWritten(seat_id=seat, text=text)
    _log(store, campaign_id, [(narration, private_to(seat), seat)])
    return text


def design_agent(store: EventStore, campaign_id: CampaignId, seat: SeatId, persona: Persona) -> str:
    """The player's agent, as they design it (spec §10). Narrative only."""
    state = _open(store, campaign_id)
    _seat_of(state, seat)
    if state.seats[seat].agent is not None:
        raise StepRefusedError("Your agent is already designed; it locks when play begins.")
    if not persona.name.strip() or not persona.role.strip():
        raise StepRefusedError("Give your agent a name and a role.")
    persona = Persona(
        name=persona.name.strip()[:60],
        role=persona.role.strip()[:60],
        voice=persona.voice.strip()[:200],
        backstory=persona.backstory.strip()[:400],
        relationship=persona.relationship.strip()[:200],
    )
    agent = EntityId(derived_id(campaign_id, "agent", seat))
    entity = Entity(
        entity_id=agent,
        kind=EntityKind.PERSON,
        name=persona.name,
        parent=state.seats[seat].embodies,
        tags=frozenset({"chief_of_staff"}),
    )
    assigned = ev.AgentAssigned(seat_id=seat, agent=agent, persona=persona)
    _log(
        store,
        campaign_id,
        [(ev.EntityCreated(entity=entity), PUBLIC, seat), (assigned, PUBLIC, seat)],
    )
    return f"{persona.name}, {persona.role}, now serves {state.seats[seat].name}."


def flagged(store: EventStore, campaign_id: CampaignId) -> list[tuple[SeatId, goals.Balance]]:
    """Goals the balance check flagged that the host has not yet let stand. Scores only."""
    state = load_state(store, campaign_id)
    approved = {
        e.payload.goal_id for e in store.read(campaign_id) if isinstance(e.payload, ev.GoalApproved)
    }
    out = []
    for g in sorted(state.goals.values(), key=lambda g: g.seat_id):
        b = goals.balance(state, g.seat_id, g.condition)
        if b.flag and g.goal_id not in approved:
            out.append((g.seat_id, b))
    return out


def approve(store: EventStore, campaign_id: CampaignId, seat: SeatId) -> str:
    """The host lets one flagged goal stand, without seeing it (spec §5)."""
    state = _open(store, campaign_id)
    pending = dict(flagged(store, campaign_id))
    if seat not in pending:
        raise StepRefusedError("Nothing of that seat's is waiting for approval.")
    goal = next(g for g in state.goals.values() if g.seat_id == seat)
    _log(store, campaign_id, [(ev.GoalApproved(goal_id=goal.goal_id), PUBLIC, None)])
    return f"{state.seats[seat].name}'s goal stands."


def begin(store: EventStore, campaign_id: CampaignId) -> str:
    """Close Session Zero once every player has a character, a goal and an agent."""
    state = _open(store, campaign_id)
    humans = [s for s in state.seats.values() if s.controller is Controller.HUMAN]
    if not humans:
        raise StepRefusedError("No one has claimed a character yet.")
    goals_of = {g.seat_id for g in state.goals.values()}
    missing = [
        f"{s.name}: {'goal' if s.seat_id not in goals_of else 'agent'}"
        for s in sorted(humans, key=lambda s: s.name)
        if s.seat_id not in goals_of or s.agent is None
    ]
    if missing:
        raise StepRefusedError("Still to do: " + "; ".join(missing) + ".")
    waiting = flagged(store, campaign_id)
    if waiting:
        raise StepRefusedError(f"{len(waiting)} goal(s) wait for the host's balance approval.")
    _log(store, campaign_id, [(ev.SessionZeroClosed(), PUBLIC, None)])
    return f"Session Zero is closed. {len(humans)} player(s); the first tick is next."


__all__ = [
    "DRAFT_SCHEMA",
    "RECORD_HEADER",
    "RECORD_SCHEMA",
    "ROUTES",
    "SESSION_ZERO_MODEL",
    "SUGGESTED",
    "SessionZeroError",
    "StepRefusedError",
    "approve",
    "begin",
    "claim",
    "confirm_goal",
    "design_agent",
    "draft_world",
    "establish",
    "flagged",
    "history_brief",
    "pick",
    "propose_goals",
    "roster_lines",
    "search",
]
