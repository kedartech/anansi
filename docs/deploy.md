# Running Wyrd Protocol on the home server

Runbook for build plan Layer 8: Postgres, the scheduler, nightly backups and the restore drill. Design and reasons are in [design.md §14](design.md#14-postgres-and-the-scheduler-layer-8-proposed). Written 2026-10-20. Nothing here has been run on the real server yet.

Everything runs as one ordinary Linux user (not root): the Claude CLI login that live ticks use lives in that user's home, so the units are systemd **user** units.

## 1. Database and roles

Two roles. Create them yourself as a Postgres superuser; the migrations never create roles.

| Role | Used by | Can |
|---|---|---|
| `wyrd_admin` | `wyrd db migrate`, backups, the restore drill | owns the database and its tables; creates scratch databases for the drill |
| `wyrd_app` | every other `wyrd` command and the scheduler | `SELECT, INSERT` on `events` and `snapshots`; `SELECT, INSERT, UPDATE` on `schedule`; nothing else |

```
sudo -u postgres psql
CREATE ROLE wyrd_admin LOGIN CREATEDB;
CREATE ROLE wyrd_app LOGIN;
\password wyrd_admin
\password wyrd_app
CREATE DATABASE wyrd OWNER wyrd_admin;
REVOKE ALL ON DATABASE wyrd FROM PUBLIC;
GRANT CONNECT ON DATABASE wyrd TO wyrd_app;
```

`\password` keeps the passwords out of shell history. `CREATEDB` is only for the restore drill's scratch database.

## 2. Environment

Two files, so the scheduler never holds the admin password. Both `chmod 600`. Percent-encode any special characters in a password inside a URL.

`~/.config/wyrd/wyrd.env` (read by the scheduler and by you when playing):

```
WYRD_DB=postgresql://wyrd_app:<password>@localhost:5432/wyrd
WYRD_BRIEFINGS_DIR=/home/<you>/.local/share/wyrd/briefings
```

`~/.config/wyrd/wyrd-admin.env` (read by the backup unit and the restore drill):

```
WYRD_ADMIN_DSN=postgresql://wyrd_admin:<password>@localhost:5432/wyrd
WYRD_BACKUP_DEST=<rsync target, e.g. user@nas:/backups/wyrd, or rclone:<remote>:<path>>
# optional: WYRD_BACKUP_DIR (default ~/.local/share/wyrd/backups), WYRD_BACKUP_KEEP_DAYS (default 14)
```

| Variable | Meaning |
|---|---|
| `WYRD_DB` | `postgresql://` URL as `wyrd_app`, or a SQLite file path (solo play, no scheduler) |
| `WYRD_ADMIN_DSN` | `postgresql://` URL as `wyrd_admin`, for `wyrd db migrate`, backups and the drill |
| `WYRD_BRIEFINGS_DIR` | where briefings are written; default `./wyrd-briefings` on Postgres |
| `WYRD_BACKUP_DEST` | off-box copy target. The backup fails if it is unset |
| `WYRD_APP_ROLE` | role `wyrd db migrate` grants to; default `wyrd_app` |

To load a file into a shell: `set -a; . ~/.config/wyrd/wyrd-admin.env; set +a`.

## 3. Install and migrate

```
git clone <the repo> ~/src/wyrd-protocol
cd ~/src/wyrd-protocol
uv tool install --editable '.[postgres]'      # puts `wyrd` in ~/.local/bin
set -a; . ~/.config/wyrd/wyrd-admin.env; set +a
wyrd db migrate                                # "applied 0001_init, 0002_schedule"
```

`--editable` keeps `wyrd new salt-strait` finding the bundled `worlds/`. Rerunning `wyrd db migrate` is safe ("up to date"). After pulling new code, run it again before the next tick.

Moving a SQLite campaign across, if you have one:

```
set -a; . ~/.config/wyrd/wyrd.env; set +a
wyrd db copy --from ~/wyrd.db --to "$WYRD_DB"     # copies, then verifies event for event
wyrd db verify
```

Or start fresh: `wyrd new salt-strait` with `WYRD_DB` set. Tick cadence is the world's `settings.tick_hours` (set `tick_hours: 12` in the YAML for 12-hour ticks).

## 4. The Claude CLI

Live ticks call `claude -p` as this user. Log in once as this user (`claude`), and make sure `claude` is in `~/.local/bin`, `/usr/local/bin`, `/usr/bin` or `/bin`, which is the `PATH` the scheduler unit sets. Edit `Environment=PATH=` in the unit if it lives elsewhere.

## 5. Units

```
mkdir -p ~/.config/systemd/user ~/.local/share/wyrd
cp deploy/systemd/*.service deploy/systemd/*.timer ~/.config/systemd/user/
install -m 755 deploy/backup/wyrd-backup.sh ~/.local/bin/wyrd-backup
install -m 755 deploy/backup/wyrd-restore-drill.sh ~/.local/bin/wyrd-restore-drill
sudo loginctl enable-linger "$USER"            # user units run with nobody logged in
systemctl --user daemon-reload
systemctl --user enable --now wyrd-scheduler.timer wyrd-backup.timer
systemctl --user list-timers
```

- `wyrd-scheduler.timer` fires every 10 minutes (`Persistent=true`, up to 60 s random delay) and starts `wyrd-scheduler.service`, a oneshot running `wyrd scheduler run`. Each run ticks each due campaign at most once.
- `wyrd-backup.timer` fires nightly at 03:30 (up to 15 min random delay) and runs `wyrd-backup`.
- Logs: `journalctl --user -u wyrd-scheduler.service` and `-u wyrd-backup.service`.

## 6. Operating the scheduler

```
wyrd scheduler status                       # next due time, paused?, failures, last status
wyrd scheduler advance [--campaign ID]      # tick on the next poll
wyrd scheduler pause [--campaign ID] [--reason TEXT]
wyrd scheduler resume [--campaign ID]       # also clears the failure count
wyrd scheduler run --offline                # a dry run with the fixture providers
```

A campaign ticks when its due time passes, when you `advance`, or when every human seat has filed an order since the last tick. A new campaign is first due one tick after the scheduler first sees it. Missed ticks (server down) are skipped, not backfilled.

When the unit shows **failed**:

- **Three failures in a row** (model call errors, a crash): the campaign is paused with `3 consecutive failures`. The tick in progress is safe; it resumes where it stopped. Read the journal, fix the cause, `wyrd scheduler resume`.
- **Canary leak** (exit 3): the campaign is paused at once with `canary leak at tick N`. Run `wyrd leaks` and `wyrd why <event>` with `WYRD_DB` set before resuming.

## 7. Backups and the restore drill

The nightly backup writes `wyrd-<UTC timestamp>.dump` (`pg_dump -Fc`) to `WYRD_BACKUP_DIR`, checks the archive reads back, deletes local dumps older than `WYRD_BACKUP_KEEP_DAYS`, then copies the new dump to `WYRD_BACKUP_DEST` with `rsync` (or `rclone copy` for an `rclone:` target). Retention at the destination is the destination's business.

Run the drill after setup, after any Postgres upgrade, and monthly:

```
set -a; . ~/.config/wyrd/wyrd-admin.env; set +a
wyrd-restore-drill
```

It restores the newest local dump into a scratch database, runs `wyrd db verify` against it (replays every campaign from event 0, checks every tick's state hash and the latest snapshot), drops the scratch database, and exits non-zero on any failure. Pull a dump back from the off-box target into `WYRD_BACKUP_DIR` now and then and drill that one too, so the off-box copy is proven, not assumed.

Real restore onto a new server: create the roles and an empty `wyrd` database as in §1, then `pg_restore --exit-on-error --no-owner --dbname="$WYRD_ADMIN_DSN" <dump>` and `WYRD_DB=... wyrd db verify`. Do not run `wyrd db migrate` before the restore; the dump carries `schema_migrations`.

## 8. Still to do on the real server (Josh)

- [ ] The Layer 8 exit run: a solo campaign on `tick_hours: 12` running three days with no manual trigger. `wyrd scheduler status` and `wyrd db verify` at the end.
- [ ] A real restore: the drill above against a dump fetched back from `WYRD_BACKUP_DEST`.
- [ ] Confirm the units start under linger after a reboot (`systemctl --user list-timers`).
