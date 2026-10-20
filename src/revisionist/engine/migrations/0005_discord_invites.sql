-- Single-use invite codes for /join (Layer 9b). Only a hash of each code is
-- stored, so reading this table cannot redeem anything. Operational data,
-- not game state, like the other discord_* tables.

CREATE TABLE discord_invites (
    code_hash   TEXT        PRIMARY KEY,
    campaign_id TEXT        NOT NULL,
    seat_id     TEXT        NOT NULL,
    issued_at   TIMESTAMPTZ NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    used_at     TIMESTAMPTZ,
    used_by     TEXT,
    CHECK ((used_at IS NULL) = (used_by IS NULL))
);

GRANT SELECT, INSERT ON discord_invites TO {app_role};
GRANT UPDATE (used_at, used_by) ON discord_invites TO {app_role};
