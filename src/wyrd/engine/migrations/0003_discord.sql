-- Discord operational tables (Layer 9a). Not game state, so not in the event
-- log, like schedule. Discord IDs are snowflakes kept as TEXT.

CREATE TABLE discord_seats (
    campaign_id     TEXT        NOT NULL,
    seat_id         TEXT        NOT NULL,
    discord_user_id TEXT        NOT NULL,
    guild_id        TEXT        NOT NULL,
    linked_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT discord_seats_one_user_per_seat PRIMARY KEY (campaign_id, seat_id),
    CONSTRAINT discord_seats_one_seat_per_user UNIQUE (campaign_id, discord_user_id)
);

-- The outbox: a part recorded here is never sent again.
CREATE TABLE discord_deliveries (
    idempotency_key TEXT        NOT NULL,
    part            INTEGER     NOT NULL CHECK (part >= 0),
    channel_id      TEXT        NOT NULL,
    message_id      TEXT        NOT NULL,
    sent_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (idempotency_key, part)
);

CREATE TABLE discord_bugs (
    id          BIGSERIAL   PRIMARY KEY,
    campaign_id TEXT        NOT NULL,
    seat_id     TEXT        NOT NULL,
    seq         BIGINT      NOT NULL,
    state_hash  TEXT        NOT NULL,
    notes       TEXT        NOT NULL,
    filed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

GRANT SELECT, INSERT ON discord_bugs, discord_deliveries TO {app_role};
GRANT SELECT, INSERT, UPDATE, DELETE ON discord_seats TO {app_role};
-- INSERT into a BIGSERIAL column needs the sequence too.
GRANT USAGE ON SEQUENCE discord_bugs_id_seq TO {app_role};
