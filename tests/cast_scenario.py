"""The generated-cast scenario the cast recording was built from: a pitch for October
1962 read into one start point, a cast of four, then each card built in a fixed order
(Kennedy and Khrushchev picked, Castro and McNamara built in the background).

The replies below were written by hand for tests; no model wrote them.
"""

from typing import Any

from revisionist.core.ids import CampaignId, EntityId
from revisionist.engine import build
from revisionist.engine import campaign as cmp
from revisionist.engine.cast import draft_cast
from revisionist.engine.llm import Request, Transport
from revisionist.engine.pitch import read_pitch
from revisionist.engine.store import EventStore, load_state

PITCH = "The Cuban missile crisis, from the first photographs"
PICKED = ("John F. Kennedy", "Nikita Khrushchev")
KENNEDY_VOICE = "Cool and ironic in private, measured and grave in public."
KENNEDY_INTEREST = "a settlement that does not look like a retreat"

PITCH_REPLY: dict[str, Any] = {
    "reading": "The thirteen days of October 1962, from the U-2 photographs to the settlement.",
    "start_points": [
        {
            "label": "Washington, 16 October 1962",
            "start_date": "1962-10-16",
            "contests": ["The missiles in Cuba"],
            "powers": ["United States", "Soviet Union", "Cuba"],
            "focus": "The President is shown the photographs of missile sites in Cuba.",
            "schedule": ["22 October: the President announces a quarantine of Cuba"],
        }
    ],
    "named": [],
    "refusal": "",
}

CAST_REPLY: dict[str, Any] = {
    "name": "October 1962",
    "entities": [
        {"key": "usa", "kind": "polity", "name": "United States"},
        {"key": "ussr", "kind": "polity", "name": "Soviet Union"},
        {"key": "cuba", "kind": "polity", "name": "Cuba"},
        {
            "key": "excomm",
            "kind": "org",
            "name": "Executive Committee of the National Security Council",
            "parent": "usa",
        },
        {"key": "kennedy", "kind": "person", "name": "John F. Kennedy", "parent": "usa"},
        {"key": "khrushchev", "kind": "person", "name": "Nikita Khrushchev", "parent": "ussr"},
        {"key": "castro", "kind": "person", "name": "Fidel Castro", "parent": "cuba"},
        {"key": "mcnamara", "kind": "person", "name": "Robert McNamara", "parent": "excomm"},
    ],
    "roster": [
        {
            "entity": "kennedy",
            "pitch": "President of the United States, shown the photographs this morning.",
            "reach": "The armed forces, the Executive Committee and the nation's ear.",
            "composite": False,
            "aliases": ["JFK", "Kennedy"],
        },
        {
            "entity": "khrushchev",
            "pitch": "First Secretary, who sent the missiles and must decide what to do now.",
            "reach": "The Presidium, the ships at sea and the letters to Washington.",
            "composite": False,
            "aliases": ["Khrushchev"],
        },
        {
            "entity": "castro",
            "pitch": "Prime Minister of Cuba, expecting an invasion.",
            "reach": "The Cuban army and militia, and the radio.",
            "composite": False,
            "aliases": ["Castro"],
        },
        {
            "entity": "mcnamara",
            "pitch": "Secretary of Defense, the President's manager of force.",
            "reach": "The Pentagon and the Navy's orders at sea.",
            "composite": False,
            "aliases": ["McNamara"],
        },
    ],
}


def _decision(person: str, days: int) -> dict[str, Any]:
    return {
        "person": person,
        "kind": "decision",
        "days": days,
        "assumed": True,
        "allowed": ["pass", "stall", "amend", "refuse"],
    }


def _clerical(person: str) -> dict[str, Any]:
    return {"person": person, "kind": "clerical", "days": 1, "assumed": True}


def _text_lever(name: str, pipeline: str, resolver: str, label: str) -> dict[str, Any]:
    return {
        "name": name,
        "pipeline": pipeline,
        "resolver": resolver,
        "label": label,
        "summary": f"{label}, in your own words.",
        "params": [{"name": "message", "kind": "text", "label": "What you will say"}],
    }


BUILD_BY_NAME: dict[str, dict[str, Any]] = {
    "John F. Kennedy": {
        "entities": [
            {"key": "rusk", "kind": "person", "name": "Dean Rusk", "parent": "united_states"},
            {
                "key": "anderson",
                "kind": "person",
                "name": "George Anderson",
                "parent": "united_states",
            },
        ],
        "quantities": [
            {
                "key": "sites_seen",
                "owner": "cuba",
                "name": "Missile sites photographed in Cuba",
                "unit": "sites",
                "gap": True,
            }
        ],
        "feeds": [
            {
                "key": "u2_reports",
                "name": "Photographic intelligence from Cuba",
                "author": "robert_mcnamara",
                "kind": "report",
                "channel": "courier",
                "classification": "most_secret",
                "every_days": 1,
                "latency_days": 1,
                "assumed": True,
                "gap": True,
                "reports": ["sites_seen"],
            }
        ],
        "pipelines": [
            {
                "key": "quarantine_order",
                "name": "A quarantine order",
                "gap": True,
                "steps": [_decision("robert_mcnamara", 1), _decision("anderson", 1)],
            },
            {
                "key": "address",
                "name": "An address to the nation",
                "gap": True,
                "steps": [_clerical("rusk")],
            },
        ],
        "cards": [
            {
                "person": "john_f_kennedy",
                "office": "President of the United States",
                "principals": ["united_states"],
                "powers": ["Commander in Chief of the Army and Navy"],
                "levers": [
                    {
                        "name": "quarantine",
                        "pipeline": "quarantine_order",
                        "resolver": "shipping",
                        "label": "Order a naval quarantine",
                        "summary": "Have the Navy stop ships bound for Cuba at a line you set.",
                        "params": [
                            {
                                "name": "line",
                                "kind": "enum",
                                "choices": ["close_in", "far_out"],
                                "label": "Where to draw the line",
                                "choice_labels": ["Close to Cuba", "Far out at sea"],
                            }
                        ],
                    },
                    _text_lever("speak", "address", "opinion", "Speak to the nation"),
                ],
                "contacts": [
                    {"person": "robert_mcnamara", "channel": "in_person"},
                    {"person": "rusk", "channel": "in_person"},
                    {"person": "nikita_khrushchev", "channel": "cable"},
                ],
                "feeds": ["u2_reports"],
                "pipelines": ["quarantine_order", "address"],
                "horizon_days": 7,
                "voice": KENNEDY_VOICE,
                "loyalty": 3,
                "interests": [KENNEDY_INTEREST],
            }
        ],
    },
    "Nikita Khrushchev": {
        "entities": [
            {
                "key": "gromyko",
                "kind": "person",
                "name": "Andrei Gromyko",
                "parent": "soviet_union",
            },
            {
                "key": "malinovsky",
                "kind": "person",
                "name": "Rodion Malinovsky",
                "parent": "soviet_union",
            },
        ],
        "quantities": [
            {
                "key": "ships_bound",
                "owner": "soviet_union",
                "name": "Soviet ships bound for Cuba",
                "unit": "ships",
                "gap": True,
            }
        ],
        "feeds": [
            {
                "key": "defence_reports",
                "name": "Reports from the Defence Ministry",
                "author": "malinovsky",
                "kind": "report",
                "channel": "courier",
                "classification": "secret",
                "every_days": 2,
                "latency_days": 1,
                "assumed": True,
                "gap": True,
                "reports": ["ships_bound"],
            }
        ],
        "pipelines": [
            {
                "key": "ship_orders",
                "name": "Orders to the ships",
                "gap": True,
                "steps": [_decision("malinovsky", 1)],
            },
            {
                "key": "statement",
                "name": "A public statement",
                "gap": True,
                "steps": [_clerical("gromyko")],
            },
        ],
        "cards": [
            {
                "person": "nikita_khrushchev",
                "office": "First Secretary of the Communist Party",
                "principals": ["soviet_union"],
                "powers": ["Chairman of the Council of Ministers"],
                "levers": [
                    {
                        "name": "ships",
                        "pipeline": "ship_orders",
                        "resolver": "shipping",
                        "label": "Order the ships",
                        "summary": "Tell the ships bound for Cuba whether to hold course.",
                        "params": [
                            {
                                "name": "course",
                                "kind": "enum",
                                "choices": ["hold_course", "turn_back"],
                                "label": "Their course",
                                "choice_labels": ["Hold course", "Turn back"],
                            }
                        ],
                    },
                    _text_lever("speak", "statement", "opinion", "Make a public statement"),
                ],
                "contacts": [
                    {"person": "malinovsky", "channel": "in_person"},
                    {"person": "gromyko", "channel": "in_person"},
                    {"person": "john_f_kennedy", "channel": "cable"},
                ],
                "feeds": ["defence_reports"],
                "pipelines": ["ship_orders", "statement"],
                "horizon_days": 7,
                "voice": "Blunt, earthy, quick to anger and quick to bargain.",
                "loyalty": 2,
                "interests": ["the missiles kept or paid for"],
            }
        ],
    },
    "Fidel Castro": {
        "entities": [
            {"key": "raul", "kind": "person", "name": "Raúl Castro", "parent": "cuba"},
        ],
        "quantities": [
            {
                "key": "militia",
                "owner": "cuba",
                "name": "Cuban militia under arms",
                "unit": "men",
                "gap": True,
            }
        ],
        "feeds": [
            {
                "key": "army_reports",
                "name": "Reports from the Armed Forces Ministry",
                "author": "raul",
                "kind": "report",
                "channel": "courier",
                "classification": "secret",
                "every_days": 1,
                "latency_days": 0,
                "assumed": True,
                "gap": True,
                "reports": ["militia"],
            }
        ],
        "pipelines": [
            {
                "key": "mobilise",
                "name": "A mobilisation order",
                "gap": True,
                "steps": [_decision("raul", 1)],
            },
            {
                "key": "broadcast",
                "name": "A broadcast",
                "gap": True,
                "steps": [_clerical("raul")],
            },
        ],
        "cards": [
            {
                "person": "fidel_castro",
                "office": "Prime Minister of Cuba",
                "principals": ["cuba"],
                "levers": [
                    {
                        "name": "mobilise",
                        "pipeline": "mobilise",
                        "resolver": "production",
                        "label": "Mobilise the militia",
                        "summary": "Call up the militia in thousands.",
                        "params": [
                            {
                                "name": "thousands",
                                "kind": "int",
                                "lo": 0,
                                "hi": 300,
                                "label": "Thousands of men",
                            }
                        ],
                    },
                    _text_lever("speak", "broadcast", "opinion", "Speak on the radio"),
                ],
                "contacts": [
                    {"person": "raul", "channel": "in_person"},
                    {"person": "nikita_khrushchev", "channel": "cable"},
                ],
                "feeds": ["army_reports"],
                "pipelines": ["mobilise", "broadcast"],
                "horizon_days": 7,
                "voice": "Long, fierce and defiant.",
                "loyalty": 1,
            }
        ],
    },
    "Robert McNamara": {
        "entities": [
            {
                "key": "taylor",
                "kind": "person",
                "name": "Maxwell Taylor",
                "parent": "united_states",
            },
        ],
        "quantities": [
            {
                "key": "ready_sorties",
                "owner": "united_states",
                "name": "Air strike sorties ready",
                "unit": "sorties",
                "gap": True,
            }
        ],
        "feeds": [
            {
                "key": "chiefs_reports",
                "name": "The Joint Chiefs' readiness reports",
                "author": "taylor",
                "kind": "report",
                "channel": "courier",
                "classification": "secret",
                "every_days": 1,
                "latency_days": 0,
                "assumed": True,
                "gap": True,
                "reports": ["ready_sorties"],
            }
        ],
        "pipelines": [
            {
                "key": "readiness",
                "name": "A readiness order",
                "gap": True,
                "steps": [_decision("taylor", 1)],
            },
            {
                "key": "briefing",
                "name": "A press briefing",
                "gap": True,
                "steps": [_clerical("taylor")],
            },
        ],
        "cards": [
            {
                "person": "robert_mcnamara",
                "office": "Secretary of Defense",
                "principals": ["john_f_kennedy"],
                "levers": [
                    {
                        "name": "readiness",
                        "pipeline": "readiness",
                        "resolver": "production",
                        "label": "Set the forces' readiness",
                        "summary": "Raise or lower the readiness of the forces.",
                        "params": [
                            {
                                "name": "level",
                                "kind": "enum",
                                "choices": ["raised", "lowered"],
                                "label": "Readiness",
                                "choice_labels": ["Raised", "Lowered"],
                            }
                        ],
                    },
                    _text_lever("brief", "briefing", "opinion", "Brief the press"),
                ],
                "contacts": [
                    {"person": "taylor", "channel": "in_person"},
                    {"person": "john_f_kennedy", "channel": "in_person"},
                ],
                "feeds": ["chiefs_reports"],
                "pipelines": ["readiness", "briefing"],
                "horizon_days": 7,
                "voice": "Brisk, numerical, impatient with soldiers' talk.",
                "loyalty": 4,
            }
        ],
    },
}


def reply(request: Request) -> Any:
    if request.call_class == "pitch":
        return PITCH_REPLY
    if request.call_class == "cast":
        return CAST_REPLY
    head = request.prompt.split("## The person\n", 1)[1]
    name = head.split("\n", 1)[0].split(": ", 1)[1]
    return BUILD_BY_NAME[name]


def person(store: EventStore, campaign_id: CampaignId, name: str) -> EntityId:
    state = load_state(store, campaign_id)
    return next(e for e, x in state.entities.items() if x.name == name)


def opened(store: EventStore, transport: Transport) -> CampaignId:
    """The pitch read, start point 1 taken and the cast compiled. No cards yet."""
    card, _ = read_pitch(PITCH, transport)
    world, _ = draft_cast(card, card.start_points[0], transport)
    return cmp.new_campaign(store, world)


def scenario(store: EventStore, transport: Transport) -> CampaignId:
    """Every call in the recording, in its order."""
    campaign_id = opened(store, transport)
    for name in PICKED:
        build.build_figure(store, campaign_id, person(store, campaign_id, name), transport)
    build.build_rest(store, campaign_id, transport)
    return campaign_id
