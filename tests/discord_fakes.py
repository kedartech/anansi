"""A fake Discord REST API for the Layer 9a tests. Nothing here touches the network."""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from wyrd.engine.discord.rest import API, CANNOT_DM


@dataclass
class Sent:
    channel_id: str
    content: str
    nonce: str
    message_id: str


@dataclass
class FakeDiscord:
    """Implements ``rest.Http``. Honours ``enforce_nonce`` the way Discord documents it."""

    closed_dms: set[str] = field(default_factory=set)
    """Users whose DMs refuse the bot (code 50007)."""
    rate_limits: int = 0
    """How many of the next calls answer 429."""
    fail_with: tuple[int, int] | None = None
    """Answer every call with this (status, code)."""
    requests: list[tuple[str, str, dict[str, str], dict[str, Any]]] = field(default_factory=list)
    messages: list[Sent] = field(default_factory=list)
    channels: dict[str, str] = field(default_factory=dict)
    """DM channel ID per user."""

    def request(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        payload = json.loads(body or b"{}")
        self.requests.append((method, url, dict(headers), payload))
        if self.rate_limits:
            self.rate_limits -= 1
            return 429, json.dumps({"retry_after": 0.25, "global": False}).encode()
        if self.fail_with:
            status, code = self.fail_with
            return status, json.dumps({"code": code, "message": "nope"}).encode()
        route = url.removeprefix(API)
        if route == "/users/@me/channels":
            user = payload["recipient_id"]
            channel = self.channels.setdefault(user, f"dm-{user}")
            return 200, json.dumps({"id": channel, "type": 1}).encode()
        channel = route.removeprefix("/channels/").removesuffix("/messages")
        for user, dm in self.channels.items():
            if dm == channel and user in self.closed_dms:
                return 403, json.dumps(
                    {"code": CANNOT_DM, "message": "Cannot send messages to this user"}
                ).encode()
        assert payload["enforce_nonce"] is True and len(payload["nonce"]) <= 25
        assert len(payload["content"]) <= 2000
        for m in self.messages:
            if m.channel_id == channel and m.nonce == payload["nonce"]:
                return 200, json.dumps({"id": m.message_id}).encode()  # deduplicated
        sent = Sent(channel, payload["content"], payload["nonce"], str(1000 + len(self.messages)))
        self.messages.append(sent)
        return 200, json.dumps({"id": sent.message_id}).encode()

    def posts(self) -> list[dict[str, Any]]:
        """Every message payload sent, deduplicated or not."""
        return [p for method, url, _, p in self.requests if url.endswith("/messages")]

    def text(self) -> str:
        return "\n".join(m.content for m in self.messages)
