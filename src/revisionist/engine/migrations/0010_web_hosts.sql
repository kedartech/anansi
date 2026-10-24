-- The account that started a game from the web: it invites the other players,
-- begins the game and advances it. A game started from Discord or the CLI has none.
CREATE TABLE web_hosts (
    campaign_id TEXT        PRIMARY KEY,
    account_id  TEXT        NOT NULL REFERENCES web_accounts ON DELETE CASCADE,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

GRANT SELECT, INSERT, DELETE ON web_hosts TO {app_role};
