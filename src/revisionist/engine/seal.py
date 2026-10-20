"""Encryption at rest, two-tier (build plan Layer 9b; design §16).

A sealed campaign has one campaign key and one key per seat that has spoken.
``core.sealing.seal_holder`` picks the key for each event; this module does the
AES-256-GCM. An event is sealed whole: at rest it is a stub (``Sealed`` payload,
no actor, Public scope) whose ``payload_enc`` is a 12-byte nonce followed by the
ciphertext of the event's JSON, bound to its campaign, seq, ID and key ID. So at
rest even the kind and the scope are hidden; only seq, tick, ID and versions
stay readable. Snapshots are sealed under the campaign key the same way.

Stores call a ``Sealer`` on append and read (``engine/store.py``), so the tick,
the fold and every reader keep working on plaintext. Sealing is per campaign
and forward only: a campaign with no campaign key stays plaintext. Data keys
are wrapped by a host master key (``PgKeyring``, migration 0007).
"""

import base64
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from revisionist.core import events as ev
from revisionist.core.events import Event
from revisionist.core.ids import CampaignId, SeatId
from revisionist.core.sealing import seal_holder
from revisionist.core.state import State
from revisionist.core.visibility import PUBLIC
from revisionist.engine.store import StoreError

KEY_BYTES = 32
NONCE_BYTES = 12


class Keyring(Protocol):
    def campaign_key(self, campaign_id: CampaignId) -> tuple[str, bytes] | None:
        """``(key ID, key)``, or ``None`` when the campaign is not sealed."""
        ...

    def seat_key(self, campaign_id: CampaignId, seat_id: SeatId) -> tuple[str, bytes]:
        """The seat's key, created on first use."""
        ...

    def key(self, key_id: str) -> bytes | None:
        """Any live key by ID; ``None`` when it was destroyed or never existed."""
        ...


def encrypt(key: bytes, plain: bytes, aad: bytes) -> bytes:
    nonce = os.urandom(NONCE_BYTES)
    return nonce + AESGCM(key).encrypt(nonce, plain, aad)


def decrypt(key: bytes, sealed: bytes, aad: bytes) -> bytes:
    try:
        return AESGCM(key).decrypt(sealed[:NONCE_BYTES], sealed[NONCE_BYTES:], aad)
    except InvalidTag:
        raise StoreError("a sealed record does not authenticate: tampered or wrong key") from None


def _event_aad(event: Event, key_id: str) -> bytes:
    return f"{event.campaign_id}|{event.seq}|{event.event_id}|{key_id}".encode()


def _snapshot_aad(campaign_id: CampaignId, seq: int, key_id: str) -> bytes:
    return f"{campaign_id}|snapshot|{seq}|{key_id}".encode()


@dataclass
class KeyringSealer:
    """The ``Sealer`` stores use: seals events and snapshots of sealed campaigns."""

    keyring: Keyring

    def sealed(self, campaign_id: CampaignId) -> bool:
        return self.keyring.campaign_key(campaign_id) is not None

    def seal(self, events: Sequence[Event]) -> list[Event]:
        out = []
        for e in events:
            campaign = self.keyring.campaign_key(e.campaign_id)
            if campaign is None:
                out.append(e)
                continue
            seat = seal_holder(e)
            key_id, key = self.keyring.seat_key(e.campaign_id, seat) if seat else campaign
            stub = e.model_copy(
                update={
                    "actor": None,
                    "scope": PUBLIC,
                    "payload": ev.Sealed(key_id=key_id, holder="seat" if seat else "campaign"),
                    "payload_enc": encrypt(
                        key, e.model_dump_json().encode(), _event_aad(e, key_id)
                    ),
                }
            )
            out.append(stub)
        return out

    def unseal(self, event: Event) -> Event:
        p = event.payload
        if not isinstance(p, ev.Sealed):
            if self.sealed(event.campaign_id):
                raise StoreError(f"event {event.seq} of a sealed campaign is plaintext")
            return event
        key = self.keyring.key(p.key_id)
        if key is None or event.payload_enc is None:
            return event  # erased (a seat key) or lost (the campaign key: the fold refuses it)
        restored = Event.model_validate_json(
            decrypt(key, event.payload_enc, _event_aad(event, p.key_id))
        )
        if (restored.campaign_id, restored.seq, restored.event_id) != (
            event.campaign_id,
            event.seq,
            event.event_id,
        ):
            raise StoreError(f"sealed event {event.seq} does not match its envelope")
        return restored

    def seal_state(self, state: State) -> str:
        campaign = self.keyring.campaign_key(state.campaign_id)
        if campaign is None:
            return state.model_dump_json()
        key_id, key = campaign
        data = encrypt(
            key,
            state.model_dump_json().encode(),
            _snapshot_aad(state.campaign_id, state.last_seq, key_id),
        )
        return json.dumps({"sealed": {"key_id": key_id, "data": base64.b64encode(data).decode()}})

    def unseal_state(self, campaign_id: CampaignId, seq: int, body: str) -> State:
        if not is_sealed_body(body):
            if self.sealed(campaign_id):
                raise StoreError(f"the snapshot at {seq} of a sealed campaign is plaintext")
            return State.model_validate_json(body)
        wrapper = json.loads(body)["sealed"]
        key = self.keyring.key(wrapper["key_id"])
        if key is None:
            raise StoreError(f"the campaign key for {campaign_id} is gone")
        plain = decrypt(
            key,
            base64.b64decode(wrapper["data"]),
            _snapshot_aad(campaign_id, seq, wrapper["key_id"]),
        )
        return State.model_validate_json(plain)


def is_sealed_body(body: str) -> bool:
    return body.startswith('{"sealed":')


@dataclass
class MemoryKeyring:
    """Keys in a dict, for tests. ``destroy`` is erasure."""

    keys: dict[str, bytes] = field(default_factory=dict)
    campaigns: dict[CampaignId, str] = field(default_factory=dict)
    seats: dict[tuple[CampaignId, SeatId], str] = field(default_factory=dict)

    def create_campaign_key(self, campaign_id: CampaignId) -> str:
        key_id = f"c-{campaign_id}"
        self.keys[key_id] = os.urandom(KEY_BYTES)
        self.campaigns[campaign_id] = key_id
        return key_id

    def campaign_key(self, campaign_id: CampaignId) -> tuple[str, bytes] | None:
        key_id = self.campaigns.get(campaign_id)
        key = self.keys.get(key_id) if key_id else None
        return (key_id, key) if key_id and key else None

    def seat_key(self, campaign_id: CampaignId, seat_id: SeatId) -> tuple[str, bytes]:
        key_id = self.seats.setdefault((campaign_id, seat_id), f"s-{campaign_id}-{seat_id}")
        if key_id not in self.keys:
            self.keys[key_id] = os.urandom(KEY_BYTES)
        return key_id, self.keys[key_id]

    def key(self, key_id: str) -> bytes | None:
        return self.keys.get(key_id)

    def destroy(self, key_id: str) -> None:
        self.keys.pop(key_id, None)


__all__ = [
    "KEY_BYTES",
    "Keyring",
    "KeyringSealer",
    "MemoryKeyring",
    "decrypt",
    "encrypt",
    "is_sealed_body",
]
