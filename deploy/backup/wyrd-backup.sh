#!/usr/bin/env bash
# Nightly backup: pg_dump -Fc into a local directory, prune by age, then copy
# the new dump off the box. Fails loudly on anything missing.
#
#   WYRD_ADMIN_DSN          postgresql:// URL of the wyrd database, as the admin role
#   WYRD_BACKUP_DEST        off-box target: an rsync destination (user@host:/path)
#                           or rclone:<remote>:<path>
#   WYRD_BACKUP_DIR         local dumps (default ~/.local/share/wyrd/backups)
#   WYRD_BACKUP_KEEP_DAYS   local retention in days (default 14)
set -euo pipefail

: "${WYRD_ADMIN_DSN:?WYRD_ADMIN_DSN is not set}"
: "${WYRD_BACKUP_DEST:?WYRD_BACKUP_DEST is not set: name an rsync or rclone target}"
dir="${WYRD_BACKUP_DIR:-$HOME/.local/share/wyrd/backups}"
keep="${WYRD_BACKUP_KEEP_DAYS:-14}"

mkdir -p "$dir"
chmod 700 "$dir"
out="$dir/wyrd-$(date -u +%Y%m%dT%H%M%SZ).dump"

pg_dump --format=custom --file="$out.partial" --dbname="$WYRD_ADMIN_DSN"
pg_restore --list "$out.partial" >/dev/null # the archive reads back
mv "$out.partial" "$out"
echo "dumped $out ($(stat -c %s "$out") bytes)"

find "$dir" -maxdepth 1 -name 'wyrd-*.dump' -mtime +"$keep" -print -delete

case "$WYRD_BACKUP_DEST" in
rclone:*) rclone copy "$out" "${WYRD_BACKUP_DEST#rclone:}" ;;
*) rsync --archive "$out" "$WYRD_BACKUP_DEST/" ;;
esac
echo "copied to $WYRD_BACKUP_DEST"
