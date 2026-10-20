"""Claude adapters for the resolver and narrator (build plan Layer 5).

Transport is the Claude Code CLI (``claude -p``) on the host's subscription, so
no API key lives in this repo or its environment. The CLI returns one JSON
result with the structured output, the model, token counts and a list-price
cost; those land in ``CallCost`` on the stage marker. A ``RecordedTransport``
replays stored request/response pairs keyed by a hash of the request, so CI
never calls a model; ``REVISIONIST_RECORD=1`` refreshes a recording from a live call.

Only the resolver sees ``State`` (truth). The narrator's input type is ``View``.
"""

import hashlib
import json
import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pydantic import TypeAdapter, ValidationError

from revisionist.core import delta as d
from revisionist.core.events import CallCost
from revisionist.core.ids import DeltaId, IntentId, derived_id
from revisionist.core.intent import Intent
from revisionist.core.referee import Roll, rate
from revisionist.core.state import State, View
from revisionist.engine.tick import Narration, Proposal, Resolution

PROMPTS = Path(__file__).parent / "prompts"
RESOLVER_MODEL = "claude-sonnet-5-5"
NARRATOR_MODEL = "claude-haiku-5-5"


# --- transport -----------------------------------------------------------------


class TransportError(RuntimeError):
    pass


@dataclass(frozen=True)
class Request:
    call_class: str
    model: str
    system: str
    prompt: str
    schema: dict[str, Any]
    max_usd: float = 1.0

    def key(self) -> str:
        h = hashlib.blake2b(digest_size=12)
        for part in (self.model, self.system, self.prompt, json.dumps(self.schema, sort_keys=True)):
            h.update(part.encode())
            h.update(b"\0")
        return h.hexdigest()


@dataclass(frozen=True)
class Reply:
    output: Any
    model: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    usd: float
    """List price as reported by the CLI; on a subscription the real charge is zero."""

    def cost(self, call_class: str) -> CallCost:
        return CallCost(
            call_class=call_class,
            model=self.model,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cached_tokens=self.cached_tokens,
            usd=self.usd,
        )


class Transport(Protocol):
    def call(self, request: Request) -> Reply: ...


class ClaudeCli:
    """``claude -p`` with every setting source, tool and hook stripped.

    ``--bare`` would be the obvious flag but it also drops the subscription
    login, so the stripping is done piecewise.

    The child gets no ``REVISIONIST_*`` variable (DSNs, the bot token, key
    paths), and errors carry no model output or prompt text: they end up in
    logs and in ``schedule.last_status``, which the host reads, and the host
    may be a player (Layer 9b).
    """

    def __init__(self, binary: str = "claude", timeout_s: float = 300) -> None:
        self.binary = binary
        self.timeout_s = timeout_s

    def call(self, request: Request) -> Reply:
        argv = [
            self.binary,
            "-p",
            request.prompt,
            "--model",
            request.model,
            "--system-prompt",
            request.system,
            "--json-schema",
            json.dumps(request.schema),
            "--output-format",
            "json",
            "--setting-sources",
            "",
            "--tools",
            "",
            "--permission-mode",
            "dontAsk",
            "--no-session-persistence",
            "--max-budget-usd",
            f"{request.max_usd:.2f}",
        ]
        env = {k: v for k, v in os.environ.items() if not k.startswith("REVISIONIST_")}
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=self.timeout_s, check=False, env=env
            )
        except subprocess.TimeoutExpired:
            # Not chained: TimeoutExpired's own message quotes the argv, prompt included.
            raise TransportError(f"claude cli timed out after {self.timeout_s:.0f}s") from None
        except OSError as exc:
            raise TransportError(f"claude cli: {type(exc).__name__}: {exc.strerror}") from None
        try:
            result = json.loads(proc.stdout) if proc.stdout.lstrip().startswith("{") else {}
        except json.JSONDecodeError:
            result = {}
        if proc.returncode != 0:
            raise TransportError(f"claude cli exit {proc.returncode} ({_subtype(result)})")
        if result.get("is_error") or result.get("structured_output") is None:
            raise TransportError(f"claude cli error ({_subtype(result)})")
        usage = result.get("usage", {})
        models = list(result.get("modelUsage", {}))
        return Reply(
            output=result["structured_output"],
            model=models[0] if models else request.model,
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            cached_tokens=int(usage.get("cache_read_input_tokens", 0))
            + int(usage.get("cache_creation_input_tokens", 0)),
            usd=float(result.get("total_cost_usd", 0.0)),
        )


def _subtype(result: dict[str, Any]) -> str:
    """The CLI's error class (``error_max_turns`` and the like), never its text."""
    subtype = str(result.get("subtype") or "unknown")
    return subtype if subtype.replace("_", "").isalnum() and len(subtype) <= 40 else "unknown"


class MissingRecordingError(TransportError):
    pass


class RecordedTransport:
    """Replays ``{key: reply}`` from a JSON file. Records through ``live`` when asked.

    Recording is opt-in by ``REVISIONIST_RECORD=1`` so a CI run with a stale fixture
    fails with the request key instead of quietly calling a model.
    """

    def __init__(self, path: Path, live: Transport | None = None) -> None:
        self.path = path
        self.live = live
        self.record = os.environ.get("REVISIONIST_RECORD") == "1" and live is not None
        self.entries: dict[str, dict[str, Any]] = (
            json.loads(path.read_text()) if path.exists() else {}
        )
        self.hits = 0
        self.recorded = 0

    def call(self, request: Request) -> Reply:
        key = request.key()
        if key in self.entries:
            self.hits += 1
            return Reply(**self.entries[key]["reply"])
        if not self.record or self.live is None:
            msg = f"no recording {key} for {request.call_class} in {self.path}"
            raise MissingRecordingError(msg)
        reply = self.live.call(request)
        self.entries[key] = {
            "call_class": request.call_class,
            "model": request.model,
            "prompt_head": request.prompt[:120],
            "reply": reply.__dict__,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.entries, indent=1, sort_keys=True) + "\n")
        self.recorded += 1
        return reply


# --- prompts -------------------------------------------------------------------


def prompt_text(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text()


def prompt_version(name: str) -> str:
    """Short content hash; lands in ``Versions.prompt`` on every envelope."""
    return hashlib.blake2b(prompt_text(name).encode(), digest_size=4).hexdigest()


def prompt_versions() -> str:
    return "resolver=" + prompt_version("resolver") + ",narrator=" + prompt_version("narrator")


# --- resolver ----------------------------------------------------------------


_DELTA_LIST = TypeAdapter(list[d.Delta])


def _strip(node: Any, keywords: frozenset[str] = frozenset({"discriminator"})) -> Any:
    """Drop OpenAPI-only keywords the CLI's strict JSON Schema validator rejects."""
    if isinstance(node, dict):
        return {k: _strip(v, keywords) for k, v in node.items() if k not in keywords}
    if isinstance(node, list):
        return [_strip(x, keywords) for x in node]
    return node


def _delta_schema_without_ids() -> dict[str, Any]:
    """The Delta union's JSON Schema with ``delta_id`` removed: the engine mints those."""
    schema: dict[str, Any] = _strip(_DELTA_LIST.json_schema())
    for definition in schema.get("$defs", {}).values():
        props = definition.get("properties", {})
        props.pop("delta_id", None)
        if "required" in definition:
            definition["required"] = [r for r in definition["required"] if r != "delta_id"]
    return schema


def resolver_schema() -> dict[str, Any]:
    deltas = _delta_schema_without_ids()
    return {
        "type": "object",
        "properties": {
            "proposals": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "intent_id": {"type": "string"},
                        "deltas": {"type": "array", "items": deltas["items"]},
                    },
                    "required": ["intent_id", "deltas"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["proposals"],
        "additionalProperties": False,
        "$defs": deltas.get("$defs", {}),
    }


@dataclass
class ClaudeResolver:
    """Truth in, typed deltas out. One retry on a reply that fails to validate."""

    transport: Transport
    model: str = RESOLVER_MODEL
    max_usd: float = 1.0
    dropped: list[tuple[IntentId | None, str]] = field(default_factory=list)
    """(intent, error) for proposals the model could not produce validly after a retry."""

    def propose(
        self, state: State, intents: Sequence[Intent], rolls: Mapping[IntentId, Roll]
    ) -> Resolution:
        if not intents:
            return Resolution(proposals=())
        system = prompt_text("resolver")
        prompt = _resolver_prompt(state, intents, rolls)
        schema = resolver_schema()
        costs: list[CallCost] = []
        proposals: list[Proposal] = []
        errors = ""
        for attempt in range(2):
            req = Request(
                call_class="resolver",
                model=self.model,
                system=system,
                prompt=prompt if attempt == 0 else prompt + _RETRY_SUFFIX + errors,
                schema=schema,
                max_usd=self.max_usd,
            )
            reply = self.transport.call(req)
            costs.append(reply.cost("resolver"))
            proposals, errors = _parse_proposals(state, intents, reply.output)
            if not errors:
                break
        if errors:
            self.dropped.append((None, errors[:400]))
        return Resolution(proposals=tuple(proposals), costs=tuple(costs))


_RETRY_SUFFIX = "\n\nYour previous reply did not validate. Fix these and resend everything:\n"


def _parse_proposals(
    state: State, intents: Sequence[Intent], output: Any
) -> tuple[list[Proposal], str]:
    """Validate each proposal against the Delta union; keep the good, report the bad."""
    known = {i.intent_id for i in intents}
    good: list[Proposal] = []
    errors: list[str] = []
    for item in output.get("proposals", []) if isinstance(output, dict) else []:
        raw_id = item.get("intent_id") if isinstance(item, dict) else None
        if not isinstance(raw_id, str) or IntentId(raw_id) not in known:
            errors.append(f"unknown intent_id {raw_id!r}")
            continue
        intent_id = IntentId(raw_id)
        with_ids = [
            {**x, "delta_id": DeltaId(derived_id(state.campaign_id, state.tick, intent_id, n))}
            for n, x in enumerate(item.get("deltas", []))
            if isinstance(x, dict)
        ]
        try:
            deltas = _DELTA_LIST.validate_python(with_ids)
        except ValidationError as exc:
            errors.append(f"{intent_id}: {exc.error_count()} errors: {str(exc)[:300]}")
            continue
        good.append(Proposal(intent_id=intent_id, deltas=tuple(deltas)))
    return good, "\n".join(errors)


def _resolver_prompt(
    state: State, intents: Sequence[Intent], rolls: Mapping[IntentId, Roll]
) -> str:
    orders = [
        {
            "intent": i.model_dump(mode="json"),
            "outcome": rolls[i.intent_id].outcome.value,
            "band": rate(state, i).value,
            "exposed": rolls[i.intent_id].exposed,
        }
        for i in intents
    ]
    return (
        "## World state (truth; tick "
        + str(state.tick)
        + ")\n"
        # Goals stay out: no model weighs whether, or how, a goal is met (spec §15).
        + state.model_dump_json(exclude={"pending", "frozen", "last_seq", "goals"})
        + "\n\n## Orders resolved this tick, with their rolled outcomes\n"
        + json.dumps(orders)
        + "\n\nReturn one proposal per order, in the same order."
    )


# --- narrator ----------------------------------------------------------------


NARRATOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}


@dataclass
class ClaudeNarrator:
    """A ``View`` in, prose for that seat out. Never sees ``State``."""

    transport: Transport
    model: str = NARRATOR_MODEL
    max_usd: float = 0.25

    def narrate(self, view: View, deltas: Sequence[d.Delta]) -> Narration:
        req = Request(
            call_class="narrator",
            model=self.model,
            system=prompt_text("narrator"),
            prompt=_narrator_prompt(view, deltas),
            schema=NARRATOR_SCHEMA,
            max_usd=self.max_usd,
        )
        reply = self.transport.call(req)
        text = reply.output.get("text", "") if isinstance(reply.output, dict) else ""
        return Narration(text=text.strip(), cost=reply.cost("narrator"))


def _narrator_prompt(view: View, deltas: Sequence[d.Delta]) -> str:
    seat = view.me
    return (
        f"## Reader: {seat.name} (seat {view.viewer}), tick {view.tick}\n\n"
        "## What this seat knows\n"
        + view.model_dump_json(exclude={"own_pending"})
        + "\n\n## What changed this tick, as this seat can see it\n"
        + json.dumps([x.model_dump(mode="json", exclude={"delta_id"}) for x in deltas])
        + "\n\nWrite the briefing."
    )


__all__ = [
    "NARRATOR_MODEL",
    "RESOLVER_MODEL",
    "ClaudeCli",
    "ClaudeNarrator",
    "ClaudeResolver",
    "MissingRecordingError",
    "RecordedTransport",
    "Reply",
    "Request",
    "Transport",
    "TransportError",
    "prompt_version",
    "prompt_versions",
    "resolver_schema",
]
