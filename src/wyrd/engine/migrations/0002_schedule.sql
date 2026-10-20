-- Scheduler bookkeeping. Operational state, not game state: it never enters
-- the event log, and it is the one table the app role may UPDATE.

CREATE TABLE schedule (
    campaign_id          TEXT        PRIMARY KEY,
    next_due_at          TIMESTAMPTZ NOT NULL,
    advance_requested    BOOLEAN     NOT NULL DEFAULT FALSE,
    paused               BOOLEAN     NOT NULL DEFAULT FALSE,
    paused_reason        TEXT        NOT NULL DEFAULT '',
    consecutive_failures INTEGER     NOT NULL DEFAULT 0,
    last_started_at      TIMESTAMPTZ,
    last_finished_at     TIMESTAMPTZ,
    last_status          TEXT        NOT NULL DEFAULT ''
);

GRANT SELECT, INSERT, UPDATE ON schedule TO {app_role};
