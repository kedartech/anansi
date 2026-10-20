-- The campaign's public Discord channel (Layer 9b): statements and press go
-- there. One per campaign; the host sets it. Operational data, not game state.

CREATE TABLE discord_channels (
    campaign_id TEXT        NOT NULL,
    purpose     TEXT        NOT NULL CHECK (purpose IN ('public')),
    channel_id  TEXT        NOT NULL,
    guild_id    TEXT        NOT NULL,
    set_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (campaign_id, purpose)
);

GRANT SELECT, INSERT, UPDATE ON discord_channels TO {app_role};
