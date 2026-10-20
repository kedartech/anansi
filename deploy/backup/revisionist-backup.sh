#!/usr/bin/env bash
# Nightly backup: pg_dump -Fc into a local directory, prune by age, then copy
# the new dump off the box. Fails loudly on anything missing.
#
#   REVISIONIST_ADMIN_DSN          postgresql:// URL of the revisionist database, as the admin role
#   REVISIONIST_BACKUP_DEST        off-box target: an rsync destination (user@host:/path)
#                           or rclone:<remote>:<path>
#   REVISIONIST_BACKUP_DIR         local dumps (default ~/.local/share/revisionist/backups)
#   REVISIONIST_BACKUP_KEEP_DAYS   local retention in days (default 14)
set -euo pipefail

: "${REVISIONIST_ADMIN_DSN:?REVISIONIST_ADMIN_DSN is not set}"
: "${REVISIONIST_BACKUP_DEST:?REVISIONIST_BACKUP_DEST is not set: name an rsync or rclone target}"
dir="${REVISIONIST_BACKUP_DIR:-$HOME/.local/share/revisionist/backups}"
keep="${REVISIONIST_BACKUP_KEEP_DAYS:-14}"

mkdir -p "$dir"
chmod 700 "$dir"
out="$dir/revisionist-$(date -u +%Y%m%dT%H%M%SZ).dump"

pg_dump --format=custom --file="$out.partial" --dbname="$REVISIONIST_ADMIN_DSN"
pg_restore --list "$out.partial" >/dev/null # the archive reads back
mv "$out.partial" "$out"
echo "dumped $out ($(stat -c %s "$out") bytes)"

find "$dir" -maxdepth 1 -name 'revisionist-*.dump' -mtime +"$keep" -print -delete

case "$REVISIONIST_BACKUP_DEST" in
rclone:*) rclone copy "$out" "${REVISIONIST_BACKUP_DEST#rclone:}" ;;
*) rsync --archive "$out" "$REVISIONIST_BACKUP_DEST/" ;;
esac
echo "copied to $REVISIONIST_BACKUP_DEST"
