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
import secrets
import stat
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from revisionist.core import events as ev
from revisionist.core.events import Event
from revisionist.core.ids import CampaignId, SeatId
from revisionist.core.sealing import seal_holder
from revisionist.core.state import State
from revisionist.core.visibility import PUBLIC
from revisionist.engine.store import StoreError

if TYPE_CHECKING:
    from psycopg import Connection

KEY_BYTES = 32
NONCE_BYTES = 12


class Keyring(Protocol):
    def create_campaign_key(self, campaign_id: CampaignId) -> str:
        """Start sealing a campaign. Returns the new key's ID."""
        ...

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

    def begin(self, campaign_id: CampaignId) -> None:
        if not self.sealed(campaign_id):
            self.keyring.create_campaign_key(campaign_id)

    def seal_text(self, campaign_id: CampaignId, seat_id: SeatId, text: str) -> str:
        """A seat's words kept outside the log (bug notes), sealed with its key."""
        if not self.sealed(campaign_id):
            return text
        key_id, key = self.keyring.seat_key(campaign_id, seat_id)
        data = encrypt(key, text.encode(), f"{campaign_id}|text|{key_id}".encode())
        return f"sealed:{key_id}:" + base64.b64encode(data).decode()

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


# --- keys in Postgres, wrapped by the master key (migration 0007) -------------------

MASTER_KEY_ENV = "REVISIONIST_MASTER_KEY_FILE"
CREDENTIAL = "revisionist-master-key"
"""The name to give it under systemd's ``$CREDENTIALS_DIRECTORY``, if used."""


def master_key_path() -> Path | None:
    """``$REVISIONIST_MASTER_KEY_FILE``, else a systemd credential, else ``None``.

    The variable holds a path, never the key: the key stays in a file only its
    owner can read.
    """
    if named := os.environ.get(MASTER_KEY_ENV):
        return Path(named)
    if creds := os.environ.get("CREDENTIALS_DIRECTORY"):
        path = Path(creds) / CREDENTIAL
        return path if path.is_file() else None
    return None


def load_master_key(path: Path) -> bytes:
    """32 random bytes, raw or base64. Refused if anyone but the owner can read it."""
    if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise StoreError(f"{path} is readable by others: chmod 600 it")
    raw = path.read_bytes()
    if len(raw) == KEY_BYTES:
        return raw
    try:
        decoded = base64.b64decode(raw.strip(), validate=True)
    except ValueError:
        decoded = b""
    if len(decoded) != KEY_BYTES:
        raise StoreError(f"{path} does not hold a {KEY_BYTES}-byte key")
    return decoded


def new_master_key(path: Path) -> None:
    """Write a fresh master key, base64, mode 0600. Never overwrites."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(base64.b64encode(secrets.token_bytes(KEY_BYTES)).decode() + "\n")


class PgKeyring:
    """``seal_keys``, unwrapped with the master key and cached for the process."""

    def __init__(self, conn: "Connection[Any]", master: bytes) -> None:
        self.conn = conn
        self.master = master
        self._live: dict[tuple[CampaignId, str], tuple[str, bytes] | None] = {}
        self._keys: dict[str, bytes | None] = {}

    def _aad(self, key_id: str, campaign_id: CampaignId, holder: str) -> bytes:
        return f"{key_id}|{campaign_id}|{holder}".encode()

    def _create(self, campaign_id: CampaignId, holder: str) -> tuple[str, bytes]:
        from psycopg import errors

        key_id = secrets.token_hex(16)
        key = secrets.token_bytes(KEY_BYTES)
        wrapped = encrypt(self.master, key, self._aad(key_id, campaign_id, holder))
        try:
            self.conn.execute(
                "INSERT INTO seal_keys (key_id, campaign_id, holder, wrapped)"
                " VALUES (%s, %s, %s, %s)",
                (key_id, campaign_id, holder, wrapped),
            )
        except errors.UniqueViolation:  # another process made it first: use theirs
            self._live.pop((campaign_id, holder), None)
            found = self._find(campaign_id, holder)
            assert found is not None
            return found
        self._live[(campaign_id, holder)] = (key_id, key)
        self._keys[key_id] = key
        return key_id, key

    def _find(self, campaign_id: CampaignId, holder: str) -> tuple[str, bytes] | None:
        if (campaign_id, holder) not in self._live:
            row = self.conn.execute(
                "SELECT key_id, wrapped FROM seal_keys"
                " WHERE campaign_id = %s AND holder = %s AND destroyed_at IS NULL",
                (campaign_id, holder),
            ).fetchone()
            found = None
            if row is not None:
                key_id = str(row[0])
                found = (
                    key_id,
                    decrypt(self.master, bytes(row[1]), self._aad(key_id, campaign_id, holder)),
                )
                self._keys[key_id] = found[1]
            self._live[(campaign_id, holder)] = found
        return self._live[(campaign_id, holder)]

    def create_campaign_key(self, campaign_id: CampaignId) -> str:
        return self._create(campaign_id, "campaign")[0]

    def campaign_key(self, campaign_id: CampaignId) -> tuple[str, bytes] | None:
        return self._find(campaign_id, "campaign")

    def seat_key(self, campaign_id: CampaignId, seat_id: SeatId) -> tuple[str, bytes]:
        return self._find(campaign_id, seat_id) or self._create(campaign_id, seat_id)

    def key(self, key_id: str) -> bytes | None:
        if key_id not in self._keys:
            row = self.conn.execute(
                "SELECT campaign_id, holder, wrapped FROM seal_keys"
                " WHERE key_id = %s AND destroyed_at IS NULL",
                (key_id,),
            ).fetchone()
            self._keys[key_id] = (
                decrypt(self.master, bytes(row[2]), self._aad(key_id, CampaignId(row[0]), row[1]))
                if row
                else None
            )
        return self._keys[key_id]


def destroy_key(conn: "Connection[Any]", key_id: str, reason: str) -> bool:
    """Erasure: drop the wrapped key. Needs the admin connection; the app role cannot."""
    cur = conn.execute(
        "UPDATE seal_keys SET wrapped = NULL, destroyed_at = now(), destroyed_reason = %s"
        " WHERE key_id = %s AND destroyed_at IS NULL",
        (reason[:200], key_id),
    )
    return cur.rowcount == 1


__all__ = [
    "CREDENTIAL",
    "KEY_BYTES",
    "MASTER_KEY_ENV",
    "Keyring",
    "KeyringSealer",
    "MemoryKeyring",
    "PgKeyring",
    "decrypt",
    "destroy_key",
    "encrypt",
    "is_sealed_body",
    "load_master_key",
    "master_key_path",
    "new_master_key",
]
