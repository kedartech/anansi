"""The Discord operational tables (migration 0003).

Seat bindings, invite codes, the delivery outbox and bug reports are operational state,
not game state, so like the Layer 8 ``schedule`` table they never enter the
event log (design §14.4, §15). ``psycopg`` is the optional ``postgres``
extra; this module is only used on a Postgres store.
"""

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from revisionist.core.ids import CampaignId, SeatId

if TYPE_CHECKING:
    from psycopg import Connection


class LinkError(Exception):
    """A binding the table refuses: the seat or the user is already linked."""


@dataclass(frozen=True)
class Link:
    campaign_id: CampaignId
    seat_id: SeatId
    discord_user_id: str
    guild_id: str
    linked_at: datetime


_LINK_COLUMNS = "campaign_id, seat_id, discord_user_id, guild_id, linked_at"


def _link(row: tuple[Any, ...]) -> Link:
    return Link(CampaignId(row[0]), SeatId(row[1]), str(row[2]), str(row[3]), row[4])


class SeatLinks:
    def __init__(self, conn: "Connection[Any]") -> None:
        self.conn = conn

    def link(self, campaign_id: CampaignId, seat_id: SeatId, user_id: str, guild_id: str) -> None:
        from psycopg import errors

        try:
            self.conn.execute(
                "INSERT INTO discord_seats (campaign_id, seat_id, discord_user_id, guild_id)"
                " VALUES (%s, %s, %s, %s)",
                (campaign_id, seat_id, user_id, guild_id),
            )
        except errors.UniqueViolation as exc:
            if exc.diag.constraint_name == "discord_seats_one_seat_per_user":
                raise LinkError(f"user {user_id} already holds a seat in {campaign_id}") from None
            raise LinkError(f"seat {seat_id} is already linked in {campaign_id}") from None

    def unlink(self, campaign_id: CampaignId, seat_id: SeatId) -> bool:
        cur = self.conn.execute(
            "DELETE FROM discord_seats WHERE campaign_id = %s AND seat_id = %s",
            (campaign_id, seat_id),
        )
        return cur.rowcount > 0

    def all(self) -> list[Link]:
        rows = self.conn.execute(
            f"SELECT {_LINK_COLUMNS} FROM discord_seats ORDER BY linked_at, campaign_id"
        ).fetchall()
        return [_link(r) for r in rows]

    def for_user(self, user_id: str, guild_id: str) -> Link | None:
        """The user's newest binding in this guild."""
        row = self.conn.execute(
            f"SELECT {_LINK_COLUMNS} FROM discord_seats"
            " WHERE discord_user_id = %s AND guild_id = %s"
            " ORDER BY linked_at DESC LIMIT 1",
            (user_id, guild_id),
        ).fetchone()
        return _link(row) if row else None

    def users(self, campaign_id: CampaignId) -> dict[SeatId, str]:
        rows = self.conn.execute(
            "SELECT seat_id, discord_user_id FROM discord_seats WHERE campaign_id = %s",
            (campaign_id,),
        ).fetchall()
        return {SeatId(seat): str(user) for seat, user in rows}


INVITE_HOURS = 72


def code_hash(code: str) -> str:
    """What is stored for a code: dashes, spaces and case do not matter."""
    normal = "".join(c for c in code.upper() if c.isalnum())
    return hashlib.blake2b(normal.encode(), digest_size=16).hexdigest()


def new_code() -> str:
    """160 random bits as eight groups of four base32 characters."""
    raw = base64.b32encode(secrets.token_bytes(20)).decode()
    return "-".join(raw[i : i + 4] for i in range(0, len(raw), 4))


@dataclass(frozen=True)
class Invite:
    campaign_id: CampaignId
    seat_id: SeatId
    issued_at: datetime
    expires_at: datetime
    used_at: datetime | None
    used_by: str | None


class Invites:
    """``discord_invites`` (migration 0005): single-use codes that bind a user to a seat."""

    def __init__(self, conn: "Connection[Any]") -> None:
        self.conn = conn

    def issue(
        self, campaign_id: CampaignId, seat_id: SeatId, now: datetime, hours: int = INVITE_HOURS
    ) -> str:
        code = new_code()
        self.conn.execute(
            "INSERT INTO discord_invites (code_hash, campaign_id, seat_id, issued_at, expires_at)"
            " VALUES (%s, %s, %s, %s, %s)",
            (code_hash(code), campaign_id, seat_id, now, now + timedelta(hours=hours)),
        )
        return code

    def redeem(
        self, code: str, user_id: str, guild_id: str, now: datetime
    ) -> tuple[CampaignId, SeatId] | None:
        """Bind the user to the code's seat and spend the code, or neither.

        ``None`` for an unknown, spent or expired code. A ``LinkError`` rolls the
        whole thing back, so a refused link does not burn the code.
        """
        with self.conn.transaction():
            row = self.conn.execute(
                "SELECT campaign_id, seat_id FROM discord_invites"
                " WHERE code_hash = %s AND used_at IS NULL AND expires_at > %s"
                " AND NOT EXISTS (SELECT 1 FROM schedule s"
                "  WHERE s.campaign_id = discord_invites.campaign_id AND s.retired_at IS NOT NULL)"
                " FOR UPDATE OF discord_invites",
                (code_hash(code), now),
            ).fetchone()
            if row is None:
                return None
            campaign_id, seat_id = CampaignId(row[0]), SeatId(row[1])
            SeatLinks(self.conn).link(campaign_id, seat_id, user_id, guild_id)
            self.conn.execute(
                "UPDATE discord_invites SET used_at = %s, used_by = %s WHERE code_hash = %s",
                (now, user_id, code_hash(code)),
            )
        return campaign_id, seat_id

    def all(self, campaign_id: CampaignId) -> list[Invite]:
        rows = self.conn.execute(
            "SELECT campaign_id, seat_id, issued_at, expires_at, used_at, used_by"
            " FROM discord_invites WHERE campaign_id = %s ORDER BY issued_at",
            (campaign_id,),
        ).fetchall()
        return [Invite(CampaignId(r[0]), SeatId(r[1]), *r[2:]) for r in rows]


class Channels:
    """``discord_channels`` (migration 0006): the campaign's public channel."""

    def __init__(self, conn: "Connection[Any]") -> None:
        self.conn = conn

    def set_public(self, campaign_id: CampaignId, channel_id: str, guild_id: str) -> None:
        self.conn.execute(
            "INSERT INTO discord_channels (campaign_id, purpose, channel_id, guild_id)"
            " VALUES (%s, 'public', %s, %s) ON CONFLICT (campaign_id, purpose)"
            " DO UPDATE SET channel_id = EXCLUDED.channel_id, guild_id = EXCLUDED.guild_id,"
            " set_at = now()",
            (campaign_id, channel_id, guild_id),
        )

    def public(self, campaign_id: CampaignId) -> str | None:
        row = self.conn.execute(
            "SELECT channel_id FROM discord_channels WHERE campaign_id = %s AND purpose = 'public'",
            (campaign_id,),
        ).fetchone()
        return str(row[0]) if row else None


class PgOutbox:
    """``discord_deliveries``: one row per part Discord accepted."""

    def __init__(self, conn: "Connection[Any]") -> None:
        self.conn = conn

    def sent(self, idempotency_key: str) -> set[int]:
        rows = self.conn.execute(
            "SELECT part FROM discord_deliveries WHERE idempotency_key = %s", (idempotency_key,)
        ).fetchall()
        return {int(r[0]) for r in rows}

    def record(self, idempotency_key: str, part: int, channel_id: str, message_id: str) -> None:
        self.conn.execute(
            "INSERT INTO discord_deliveries (idempotency_key, part, channel_id, message_id)"
            " VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (idempotency_key, part, channel_id, message_id),
        )


def file_bug(
    conn: "Connection[Any]",
    campaign_id: CampaignId,
    seat_id: SeatId,
    seq: int,
    state_hash: str,
    notes: str,
) -> int:
    row = conn.execute(
        "INSERT INTO discord_bugs (campaign_id, seat_id, seq, state_hash, notes)"
        " VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (campaign_id, seat_id, seq, state_hash, notes),
    ).fetchone()
    assert row is not None
    return int(row[0])


__all__ = [
    "INVITE_HOURS",
    "Channels",
    "Invite",
    "Invites",
    "Link",
    "LinkError",
    "PgOutbox",
    "SeatLinks",
    "code_hash",
    "file_bug",
    "new_code",
]
