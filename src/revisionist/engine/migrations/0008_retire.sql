-- A retired campaign (playtest issue 6): set aside for good, with no season
-- reveal. It never ticks again and does not count as the live game. Like the
-- rest of schedule, operational state, not game state.

ALTER TABLE schedule ADD COLUMN retired_at TIMESTAMPTZ;
