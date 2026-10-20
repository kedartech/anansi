"""Briefings by Discord DM over the REST API, stdlib only.

The scheduler is a one-shot process with no gateway connection, so delivery
uses plain HTTPS: open the DM with ``POST /users/@me/channels``, send with
``POST /channels/{id}/messages``. The HTTP call sits behind ``Http`` so tests
use a fake and nothing touches the network.

Idempotency has two layers. The real one is the ``discord_deliveries`` table:
a part already recorded there is never resent. The second covers a crash
between Discord's reply and that insert: every send carries a nonce derived
from the key and part with ``enforce_nonce``, so Discord drops a resend.
Discord caps the nonce at 25 characters and checks it only "in the past few
minutes", so this covers a quick retry, not a resend hours later.
"""

import base64
import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from wyrd import __version__
from wyrd.core.ids import SeatId

API = "https://discord.com/api/v10"
PART_LIMIT = 1900
"""Under Discord's 2000-character ``content`` limit, with room to spare."""
MAX_ATTEMPTS = 3
CANNOT_DM = 50007
"""Discord's "Cannot send messages to this user" (DMs closed, or no shared server)."""
CHANNEL_LABEL = "discord"
"""What ``BriefingDelivered.channel`` says. Message IDs stay in ``discord_deliveries``."""


class DiscordError(Exception):
    """A failed Discord call. Its text names the route and Discord's reply, never the token."""

    def __init__(self, route: str, status: int, code: int = 0, message: str = "") -> None:
        detail = f"{code}: {message}" if code else message
        super().__init__(f"discord {route}: HTTP {status}" + (f" ({detail})" if detail else ""))
        self.route = route
        self.status = status
        self.code = code


class Http(Protocol):
    def request(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        """Return status and body for any HTTP status; raise ``OSError`` on a network failure."""
        ...


class UrllibHttp:
    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout

    def request(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        req = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return int(resp.status), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()


@dataclass
class DiscordRest:
    token: str = field(repr=False)
    http: Http = field(default_factory=UrllibHttp, repr=False)
    sleep: Callable[[float], None] = field(default=time.sleep, repr=False)

    def call(self, method: str, route: str, payload: dict[str, Any]) -> dict[str, Any]:
        """One API call; on 429 waits ``retry_after`` and tries again, up to 3 attempts."""
        headers = {
            "Authorization": f"Bot {self.token}",
            "Content-Type": "application/json",
            "User-Agent": f"DiscordBot (https://github.com/kedartech/anansi, {__version__})",
        }
        body = json.dumps(payload).encode()
        status, reply = 0, b""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            failure = ""
            try:
                status, reply = self.http.request(method, API + route, headers, body)
            except OSError as exc:
                failure = type(exc).__name__
            if failure:
                # Raised outside the handler so the OSError, whose text can carry
                # the request, is not kept as context.
                raise DiscordError(route, 0, 0, f"network: {failure}")
            if status != 429 or attempt == MAX_ATTEMPTS:
                break
            self.sleep(float(_json(reply).get("retry_after", 1.0)))
        data = _json(reply)
        if not 200 <= status < 300:
            code = data.get("code")
            raise DiscordError(
                route, status, code if isinstance(code, int) else 0, str(data.get("message", ""))
            )
        return data

    def open_dm(self, user_id: str) -> str:
        return str(self.call("POST", "/users/@me/channels", {"recipient_id": user_id})["id"])

    def send(self, channel_id: str, content: str, nonce: str) -> str:
        reply = self.call(
            "POST",
            f"/channels/{channel_id}/messages",
            {
                "content": content,
                "nonce": nonce,
                "enforce_nonce": True,
                "allowed_mentions": {"parse": []},
            },
        )
        return str(reply["id"])


def _json(body: bytes) -> dict[str, Any]:
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def nonce_for(idempotency_key: str, part: int) -> str:
    digest = hashlib.blake2b(f"{idempotency_key}:{part}".encode()).digest()
    return base64.b32encode(digest).decode()[:25]


def split_message(text: str, limit: int = PART_LIMIT) -> list[str]:
    """Parts of at most ``limit`` characters that concatenate back to ``text``.

    Cut on paragraph boundaries, then lines, then anywhere. Separators stay on
    the end of the piece before them, so nothing is lost.
    """
    parts: list[str] = []
    current = ""
    for piece in _pieces(text, limit):
        if current and len(current) + len(piece) > limit:
            parts.append(current)
            current = ""
        current += piece
    if current:
        parts.append(current)
    return parts


def _pieces(text: str, limit: int) -> list[str]:
    out: list[str] = []
    for para in re.split(r"(?<=\n\n)", text):
        if len(para) <= limit:
            out.append(para)
            continue
        for line in re.split(r"(?<=\n)", para):
            out += [line[i : i + limit] for i in range(0, len(line), limit)]
    return [p for p in out if p]


class Outbox(Protocol):
    def sent(self, idempotency_key: str) -> set[int]: ...

    def record(self, idempotency_key: str, part: int, channel_id: str, message_id: str) -> None: ...


@dataclass
class MemoryOutbox:
    rows: dict[tuple[str, int], tuple[str, str]] = field(default_factory=dict)

    def sent(self, idempotency_key: str) -> set[int]:
        return {part for key, part in self.rows if key == idempotency_key}

    def record(self, idempotency_key: str, part: int, channel_id: str, message_id: str) -> None:
        self.rows.setdefault((idempotency_key, part), (channel_id, message_id))


@dataclass
class DiscordDeliverer:
    """The Layer 4 ``Deliverer`` for seats bound to a Discord user: their briefing by DM.

    If Discord refuses the DM (code 50007) the part goes to ``fallback_channel``,
    which must be private to the player; without one the error propagates, so
    the tick stays resumable and the scheduler counts a failure.
    """

    rest: DiscordRest
    outbox: Outbox
    users: Mapping[SeatId, str]
    fallback_channel: str | None = None
    _dm: dict[str, str] = field(default_factory=dict, repr=False)

    def deliver(self, seat_id: SeatId, text: str, idempotency_key: str) -> str:
        user_id = self.users[seat_id]
        done = self.outbox.sent(idempotency_key)
        for part, content in enumerate(split_message(text)):
            if part in done or not content.strip():
                continue
            nonce = nonce_for(idempotency_key, part)
            channel_id, message_id = self._send(user_id, content, nonce)
            self.outbox.record(idempotency_key, part, channel_id, message_id)
        return CHANNEL_LABEL

    def _send(self, user_id: str, content: str, nonce: str) -> tuple[str, str]:
        try:
            if user_id not in self._dm:
                self._dm[user_id] = self.rest.open_dm(user_id)
            channel_id = self._dm[user_id]
            return channel_id, self.rest.send(channel_id, content, nonce)
        except DiscordError as exc:
            if exc.code != CANNOT_DM or not self.fallback_channel:
                raise
        return self.fallback_channel, self.rest.send(self.fallback_channel, content, nonce)


__all__ = [
    "API",
    "CANNOT_DM",
    "CHANNEL_LABEL",
    "PART_LIMIT",
    "DiscordDeliverer",
    "DiscordError",
    "DiscordRest",
    "Http",
    "MemoryOutbox",
    "Outbox",
    "UrllibHttp",
    "nonce_for",
    "split_message",
]
