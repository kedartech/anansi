-- The project was renamed from Wyrd Protocol to Revisionist. 0001 is history
-- and stays as it ran; this brings the trigger function's name along. The
-- triggers reference the function by OID, so they keep working.

ALTER FUNCTION wyrd_append_only() RENAME TO revisionist_append_only;
