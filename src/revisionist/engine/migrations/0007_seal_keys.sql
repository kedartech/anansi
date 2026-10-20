-- Data keys for sealed campaigns (Layer 9b): one per campaign, one per seat
-- that has spoken, each wrapped (AES-GCM) under the host's master key, which
-- never enters the database. Destroying a key is erasure: wrapped goes NULL.
-- The app role can read and add keys but never destroy one; only the admin can.

CREATE TABLE seal_keys (
    key_id           TEXT        PRIMARY KEY,
    campaign_id      TEXT        NOT NULL,
    holder           TEXT        NOT NULL,
    wrapped          BYTEA,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    destroyed_at     TIMESTAMPTZ,
    destroyed_reason TEXT,
    CHECK ((wrapped IS NULL) = (destroyed_at IS NOT NULL))
);

-- One live key per holder; a seat that speaks again after erasure gets a new one.
CREATE UNIQUE INDEX seal_keys_one_live ON seal_keys (campaign_id, holder)
    WHERE destroyed_at IS NULL;

GRANT SELECT, INSERT ON seal_keys TO {app_role};
