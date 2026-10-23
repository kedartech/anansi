"""Web accounts, sessions, recovery codes and web-held seats (migration 0009).

Passwords are scrypt hashes. Recovery codes and session tokens are high-entropy
random strings, so a fast hash is enough to store them: reading these tables lets
no one sign in. Nothing here asks for or stores anything personal.
"""

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from revisionist.core.ids import CampaignId, SeatId
from revisionist.engine.discord.tables import LinkError, code_hash
from revisionist.engine.ids import new_id

if TYPE_CHECKING:
    from psycopg import Connection

HANDLE = re.compile(r"[A-Za-z0-9_.-]{3,32}")
PASSWORD_MIN = 12
PASSWORD_MAX = 256
CODES = 10
SESSION_DAYS = 30
WEB_PREFIX = "web:"
"""How a web account appears where Discord user IDs do: invites' ``used_by``, the
leak scan's people. Discord IDs are digits, so the two can never collide."""

_N, _R, _P = 2**14, 8, 1


class AccountError(ValueError):
    """A refusal with a message safe to show the person who asked."""


def person(account_id: str) -> str:
    return WEB_PREFIX + account_id


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    b64 = base64.b64encode
    return f"scrypt${_N}${_R}${_P}${b64(salt).decode()}${b64(digest).decode()}"


def check_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    got = hashlib.scrypt(
        password.encode(),
        salt=base64.b64decode(salt),
        n=int(n),
        r=int(r),
        p=int(p),
        dklen=32,
    )
    return hmac.compare_digest(got, base64.b64decode(digest))


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_recovery_code() -> str:
    """50 random bits as two groups of five base32 characters, like ``7KQ2M-PX4RW``."""
    raw = base64.b32encode(secrets.token_bytes(10)).decode()[:10]
    return f"{raw[:5]}-{raw[5:]}"


def _check_password_rule(password: str) -> None:
    if len(password) < PASSWORD_MIN:
        raise AccountError(f"Use at least {PASSWORD_MIN} characters. A short sentence works well.")
    if len(password) > PASSWORD_MAX:
        raise AccountError(f"Use at most {PASSWORD_MAX} characters.")


@dataclass(frozen=True)
class Account:
    account_id: str
    handle: str


class Accounts:
    def __init__(self, conn: "Connection[Any]") -> None:
        self.conn = conn

    def create(self, handle: str, password: str) -> tuple[Account, list[str]]:
        """A new account and its ten recovery codes, shown once."""
        from psycopg import errors

        handle = handle.strip()
        if not HANDLE.fullmatch(handle):
            raise AccountError("A handle is 3 to 32 letters, digits, dots, dashes or underscores.")
        _check_password_rule(password)
        account = Account(new_id(), handle)
        try:
            with self.conn.transaction():
                self.conn.execute(
                    "INSERT INTO web_accounts (account_id, handle, password_hash)"
                    " VALUES (%s, %s, %s)",
                    (account.account_id, handle, hash_password(password)),
                )
                codes = self._new_codes(account.account_id)
        except errors.UniqueViolation:
            raise AccountError("That handle is taken.") from None
        return account, codes

    def _new_codes(self, account_id: str) -> list[str]:
        self.conn.execute("DELETE FROM web_recovery_codes WHERE account_id = %s", (account_id,))
        codes = [new_recovery_code() for _ in range(CODES)]
        for code in codes:
            self.conn.execute(
                "INSERT INTO web_recovery_codes (account_id, code_hash) VALUES (%s, %s)",
                (account_id, code_hash(code)),
            )
        return codes

    def _by_handle(self, handle: str) -> tuple[Account, str] | None:
        row = self.conn.execute(
            "SELECT account_id, handle, password_hash FROM web_accounts"
            " WHERE lower(handle) = lower(%s)",
            (handle.strip(),),
        ).fetchone()
        return (Account(str(row[0]), str(row[1])), str(row[2])) if row else None

    def get(self, account_id: str) -> Account | None:
        row = self.conn.execute(
            "SELECT account_id, handle FROM web_accounts WHERE account_id = %s", (account_id,)
        ).fetchone()
        return Account(str(row[0]), str(row[1])) if row else None

    def sign_in(self, handle: str, password: str) -> Account | None:
        found = self._by_handle(handle)
        if found is None:
            # Same work either way, so timing does not say whether a handle exists.
            check_password(password, hash_password("not-a-real-password"))
            return None
        account, stored = found
        return account if check_password(password, stored) else None

    def recover(self, handle: str, code: str, password: str) -> tuple[Account, list[str]] | None:
        """Spend one recovery code: a new password, fresh codes, every session ended."""
        _check_password_rule(password)
        found = self._by_handle(handle)
        if found is None:
            return None
        account, _ = found
        with self.conn.transaction():
            row = self.conn.execute(
                "UPDATE web_recovery_codes SET used_at = now()"
                " WHERE account_id = %s AND code_hash = %s AND used_at IS NULL RETURNING 1",
                (account.account_id, code_hash(code)),
            ).fetchone()
            if row is None:
                return None
            self.conn.execute(
                "UPDATE web_accounts SET password_hash = %s WHERE account_id = %s",
                (hash_password(password), account.account_id),
            )
            self.conn.execute(
                "DELETE FROM web_sessions WHERE account_id = %s", (account.account_id,)
            )
            codes = self._new_codes(account.account_id)
        return account, codes

    def codes_left(self, account_id: str) -> int:
        row = self.conn.execute(
            "SELECT count(*) FROM web_recovery_codes WHERE account_id = %s AND used_at IS NULL",
            (account_id,),
        ).fetchone()
        return int(row[0]) if row else 0

    # --- sessions -----------------------------------------------------------

    def open_session(self, account_id: str, now: datetime) -> str:
        token = secrets.token_urlsafe(32)
        self.conn.execute(
            "INSERT INTO web_sessions (token_hash, account_id, created_at, expires_at)"
            " VALUES (%s, %s, %s, %s)",
            (_token_hash(token), account_id, now, now + timedelta(days=SESSION_DAYS)),
        )
        return token

    def session(self, token: str, now: datetime) -> Account | None:
        row = self.conn.execute(
            "SELECT a.account_id, a.handle FROM web_sessions s"
            " JOIN web_accounts a USING (account_id)"
            " WHERE s.token_hash = %s AND s.expires_at > %s",
            (_token_hash(token), now),
        ).fetchone()
        return Account(str(row[0]), str(row[1])) if row else None

    def close_session(self, token: str) -> None:
        self.conn.execute("DELETE FROM web_sessions WHERE token_hash = %s", (_token_hash(token),))

    def close_all(self, account_id: str) -> None:
        self.conn.execute("DELETE FROM web_sessions WHERE account_id = %s", (account_id,))


@dataclass(frozen=True)
class WebSeat:
    campaign_id: CampaignId
    seat_id: SeatId
    account_id: str
    linked_at: datetime


class WebSeats:
    """``web_seats``: the web's counterpart of ``discord_seats``. A seat is held from
    one client only, so each table's ``link`` checks the other."""

    def __init__(self, conn: "Connection[Any]") -> None:
        self.conn = conn

    def link(self, campaign_id: CampaignId, seat_id: SeatId, account_id: str) -> None:
        from psycopg import errors

        if self.conn.execute(
            "SELECT 1 FROM discord_seats WHERE campaign_id = %s AND seat_id = %s",
            (campaign_id, seat_id),
        ).fetchone():
            raise LinkError(f"seat {seat_id} is already linked in {campaign_id}")
        try:
            self.conn.execute(
                "INSERT INTO web_seats (campaign_id, seat_id, account_id) VALUES (%s, %s, %s)",
                (campaign_id, seat_id, account_id),
            )
        except errors.UniqueViolation as exc:
            if exc.diag.constraint_name == "web_seats_one_seat_per_account":
                raise LinkError(f"you already hold a seat in {campaign_id}") from None
            raise LinkError(f"seat {seat_id} is already linked in {campaign_id}") from None

    def unlink(self, campaign_id: CampaignId, seat_id: SeatId) -> bool:
        cur = self.conn.execute(
            "DELETE FROM web_seats WHERE campaign_id = %s AND seat_id = %s",
            (campaign_id, seat_id),
        )
        return cur.rowcount > 0

    def unlink_campaign(self, campaign_id: CampaignId) -> int:
        cur = self.conn.execute("DELETE FROM web_seats WHERE campaign_id = %s", (campaign_id,))
        return cur.rowcount

    def for_account(self, account_id: str) -> list[WebSeat]:
        rows = self.conn.execute(
            "SELECT campaign_id, seat_id, account_id, linked_at FROM web_seats"
            " WHERE account_id = %s ORDER BY linked_at DESC",
            (account_id,),
        ).fetchall()
        return [WebSeat(CampaignId(r[0]), SeatId(r[1]), str(r[2]), r[3]) for r in rows]

    def get(self, account_id: str, campaign_id: CampaignId) -> WebSeat | None:
        return next((s for s in self.for_account(account_id) if s.campaign_id == campaign_id), None)

    def accounts(self, campaign_id: CampaignId) -> dict[SeatId, str]:
        """Seat to account, for one campaign."""
        rows = self.conn.execute(
            "SELECT seat_id, account_id FROM web_seats WHERE campaign_id = %s", (campaign_id,)
        ).fetchall()
        return {SeatId(seat): str(account) for seat, account in rows}


__all__ = [
    "CODES",
    "PASSWORD_MIN",
    "WEB_PREFIX",
    "Account",
    "AccountError",
    "Accounts",
    "WebSeat",
    "WebSeats",
    "check_password",
    "hash_password",
    "new_recovery_code",
    "person",
]
