#!/usr/bin/env bash
# Restore drill: load the newest dump into a scratch database, replay every
# campaign from event 0 with `wyrd db verify`, drop the scratch database.
# Exits non-zero on any failure.
#
#   WYRD_ADMIN_DSN    postgresql://user:pass@host:port/db URL, as the admin role
#   WYRD_BACKUP_DIR   where the dumps are (default ~/.local/share/wyrd/backups)
#   WYRD_BIN          the wyrd command (default: wyrd on PATH)
set -euo pipefail

: "${WYRD_ADMIN_DSN:?WYRD_ADMIN_DSN is not set}"
dir="${WYRD_BACKUP_DIR:-$HOME/.local/share/wyrd/backups}"
wyrd="${WYRD_BIN:-wyrd}"

dump="$(find "$dir" -maxdepth 1 -name 'wyrd-*.dump' -printf '%f\n' 2>/dev/null | sort | tail -n 1)"
if [[ -z "$dump" ]]; then
	echo "no dumps in $dir" >&2
	exit 1
fi

scratch="wyrd_restore_drill_$(date -u +%Y%m%d%H%M%S)"
# The same URL with the database swapped for the scratch one.
base="${WYRD_ADMIN_DSN%%\?*}"
query="${WYRD_ADMIN_DSN#"$base"}"
scratch_dsn="${base%/*}/$scratch$query"

cleanup() {
	psql --quiet --no-psqlrc --dbname="$WYRD_ADMIN_DSN" \
		--command="DROP DATABASE IF EXISTS $scratch WITH (FORCE)" >/dev/null
}
psql --quiet --no-psqlrc --set=ON_ERROR_STOP=1 --dbname="$WYRD_ADMIN_DSN" \
	--command="CREATE DATABASE $scratch" >/dev/null
trap cleanup EXIT

pg_restore --exit-on-error --no-owner --dbname="$scratch_dsn" "$dir/$dump"
echo "restored $dump into $scratch"
WYRD_DB="$scratch_dsn" "$wyrd" db verify
echo "restore drill passed"
