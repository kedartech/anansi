"""The Discord operational tables (migration 0003).

Seat bindings, the delivery outbox and bug reports are operational state,
not game state, so like the Layer 8 ``schedule`` table they never enter the
event log (design §14.4, §15). ``psycopg`` is the optional ``postgres``
extra; this module is only used on a Postgres store.
"""

from dataclasses import dataclass
from datetime import datetime
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
        (campaign_id, seat_id, seq, state_hash, notes[:4000]),
    ).fetchone()
    assert row is not None
    return int(row[0])


__all__ = ["Link", "LinkError", "PgOutbox", "SeatLinks", "file_bug"]
