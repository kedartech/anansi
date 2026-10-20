-- The event log and snapshots. Append-only twice over: the app role is granted
-- SELECT and INSERT only, and triggers refuse UPDATE, DELETE and TRUNCATE for
-- every role, the table owner included.
-- body is TEXT, not JSONB: JSONB rejects \u0000, which a player can type.

CREATE TABLE events (
    campaign_id TEXT    NOT NULL,
    seq         BIGINT  NOT NULL CHECK (seq >= 0),
    event_id    TEXT    NOT NULL UNIQUE,
    tick        INTEGER NOT NULL,
    kind        TEXT    NOT NULL,
    body        TEXT    NOT NULL,
    PRIMARY KEY (campaign_id, seq)
);

CREATE TABLE snapshots (
    campaign_id TEXT   NOT NULL,
    seq         BIGINT NOT NULL,
    body        TEXT   NOT NULL,
    PRIMARY KEY (campaign_id, seq)
);

CREATE FUNCTION wyrd_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END;
$$;

CREATE TRIGGER events_append_only BEFORE UPDATE OR DELETE ON events
    FOR EACH ROW EXECUTE FUNCTION wyrd_append_only();
CREATE TRIGGER events_no_truncate BEFORE TRUNCATE ON events
    FOR EACH STATEMENT EXECUTE FUNCTION wyrd_append_only();
CREATE TRIGGER snapshots_append_only BEFORE UPDATE OR DELETE ON snapshots
    FOR EACH ROW EXECUTE FUNCTION wyrd_append_only();
CREATE TRIGGER snapshots_no_truncate BEFORE TRUNCATE ON snapshots
    FOR EACH STATEMENT EXECUTE FUNCTION wyrd_append_only();

GRANT USAGE ON SCHEMA public TO {app_role};
GRANT SELECT, INSERT ON events, snapshots TO {app_role};
