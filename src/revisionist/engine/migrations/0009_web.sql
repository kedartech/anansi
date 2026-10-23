-- Web client accounts and seats (web client MVP). A handle and a password, nothing
-- personal. Passwords are scrypt hashes; recovery codes and session tokens are
-- stored only as hashes, so reading these tables lets no one sign in. Operational
-- data, not game state, like the discord_* tables.

CREATE TABLE web_accounts (
    account_id    TEXT        PRIMARY KEY,
    handle        TEXT        NOT NULL,
    password_hash TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Handles are unique ignoring case.
CREATE UNIQUE INDEX web_accounts_handle ON web_accounts (lower(handle));

CREATE TABLE web_recovery_codes (
    account_id TEXT        NOT NULL REFERENCES web_accounts ON DELETE CASCADE,
    code_hash  TEXT        NOT NULL,
    used_at    TIMESTAMPTZ,
    PRIMARY KEY (account_id, code_hash)
);

CREATE TABLE web_sessions (
    token_hash TEXT        PRIMARY KEY,
    account_id TEXT        NOT NULL REFERENCES web_accounts ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);

-- A seat held from the web, the counterpart of discord_seats. A seat is held from
-- one client only; the link code in each table checks the other.
CREATE TABLE web_seats (
    campaign_id TEXT        NOT NULL,
    seat_id     TEXT        NOT NULL,
    account_id  TEXT        NOT NULL REFERENCES web_accounts ON DELETE CASCADE,
    linked_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT web_seats_one_account_per_seat PRIMARY KEY (campaign_id, seat_id),
    CONSTRAINT web_seats_one_seat_per_account UNIQUE (campaign_id, account_id)
);

GRANT SELECT, INSERT, UPDATE ON web_accounts TO {app_role};
GRANT SELECT, INSERT, UPDATE, DELETE ON web_recovery_codes, web_sessions, web_seats TO {app_role};
