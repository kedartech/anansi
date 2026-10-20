# Running Revisionist on the home server

Runbook for build plan Layer 8 (Postgres, the scheduler, nightly backups and the restore drill), Layer 9a (solo play on Discord, §9) and Layer 9b (a second player, the public channel and sealing, §9.6-9.9). Design and reasons are in [design.md §14](design.md#14-postgres-and-the-scheduler-layer-8-decided) and [§15](design.md#15-the-discord-transport-layer-9a-proposed). Written 2026-10-20. Nothing here has been run on the real server yet.

Everything runs as one ordinary Linux user (not root): the Claude CLI login that live ticks use lives in that user's home, so the units are systemd **user** units.

## 1. Database and roles

Two roles. Create them yourself as a Postgres superuser; the migrations never create roles.

| Role | Used by | Can |
|---|---|---|
| `revisionist_admin` | `revisionist db migrate`, backups, the restore drill | owns the database and its tables; creates scratch databases for the drill |
| `revisionist_app` | every other `revisionist` command, the scheduler and the Discord bot | `SELECT, INSERT` on `events`, `snapshots`, `discord_deliveries` and `discord_bugs`; `SELECT, INSERT, UPDATE` on `schedule`; `SELECT, INSERT, UPDATE, DELETE` on `discord_seats`; nothing else |

```
sudo -u postgres psql
CREATE ROLE revisionist_admin LOGIN CREATEDB;
CREATE ROLE revisionist_app LOGIN;
\password revisionist_admin
\password revisionist_app
CREATE DATABASE revisionist OWNER revisionist_admin;
REVOKE ALL ON DATABASE revisionist FROM PUBLIC;
GRANT CONNECT ON DATABASE revisionist TO revisionist_app;
```

`\password` keeps the passwords out of shell history. `CREATEDB` is only for the restore drill's scratch database.

## 2. Environment

Two files, so the scheduler never holds the admin password. Both `chmod 600`. Percent-encode any special characters in a password inside a URL.

`~/.config/revisionist/revisionist.env` (read by the scheduler and by you when playing):

```
REVISIONIST_DB=postgresql://revisionist_app:<password>@localhost:5432/revisionist
REVISIONIST_BRIEFINGS_DIR=/home/<you>/.local/share/revisionist/briefings
```

`~/.config/revisionist/revisionist-admin.env` (read by the backup unit and the restore drill):

```
REVISIONIST_ADMIN_DSN=postgresql://revisionist_admin:<password>@localhost:5432/revisionist
REVISIONIST_BACKUP_DEST=<rsync target, e.g. user@nas:/backups/revisionist, or rclone:<remote>:<path>>
# optional: REVISIONIST_BACKUP_DIR (default ~/.local/share/revisionist/backups), REVISIONIST_BACKUP_KEEP_DAYS (default 14)
```

| Variable | Meaning |
|---|---|
| `REVISIONIST_DB` | `postgresql://` URL as `revisionist_app`, or a SQLite file path (solo play, no scheduler) |
| `REVISIONIST_ADMIN_DSN` | `postgresql://` URL as `revisionist_admin`, for `revisionist db migrate`, backups and the drill |
| `REVISIONIST_BRIEFINGS_DIR` | where briefings are written; default `./revisionist-briefings` on Postgres |
| `REVISIONIST_BACKUP_DEST` | off-box copy target. The backup fails if it is unset |
| `REVISIONIST_APP_ROLE` | role `revisionist db migrate` grants to; default `revisionist_app` |
| `REVISIONIST_MASTER_KEY_FILE` | path to the 0600 master-key file that seals new campaigns (§9.8); a path, never the key |
| `REVISIONIST_DISCORD_*` | the Discord bot and DM delivery; see §9 |

To load a file into a shell: `set -a; . ~/.config/revisionist/revisionist-admin.env; set +a`.

## 3. Install and migrate

```
git clone <the repo> ~/src/revisionist
cd ~/src/revisionist
uv tool install --editable '.[postgres,discord]'   # puts `revisionist` in ~/.local/bin
set -a; . ~/.config/revisionist/revisionist-admin.env; set +a
revisionist db migrate                                # "applied 0001_init, 0002_schedule, 0003_discord"
```

`--editable` keeps `revisionist new salt-strait` finding the bundled `worlds/`. Rerunning `revisionist db migrate` is safe ("up to date"). After pulling new code, run it again before the next tick.

Moving a SQLite campaign across, if you have one:

```
set -a; . ~/.config/revisionist/revisionist.env; set +a
revisionist db copy --from ~/revisionist.db --to "$REVISIONIST_DB"     # copies, then verifies event for event
revisionist db verify
```

Or start fresh: `revisionist new salt-strait` with `REVISIONIST_DB` set. Tick cadence is the world's `settings.tick_hours` (set `tick_hours: 12` in the YAML for 12-hour ticks).

## 4. The Claude CLI

Live ticks call `claude -p` as this user. Log in once as this user (`claude`), and make sure `claude` is in `~/.local/bin`, `/usr/local/bin`, `/usr/bin` or `/bin`, which is the `PATH` the scheduler unit sets. Edit `Environment=PATH=` in the unit if it lives elsewhere.

## 5. Units

```
mkdir -p ~/.config/systemd/user ~/.local/share/revisionist
cp deploy/systemd/*.service deploy/systemd/*.timer ~/.config/systemd/user/
install -m 755 deploy/backup/revisionist-backup.sh ~/.local/bin/revisionist-backup
install -m 755 deploy/backup/revisionist-restore-drill.sh ~/.local/bin/revisionist-restore-drill
sudo loginctl enable-linger "$USER"            # user units run with nobody logged in
systemctl --user daemon-reload
systemctl --user enable --now revisionist-scheduler.timer revisionist-backup.timer
systemctl --user list-timers
```

- `revisionist-scheduler.timer` fires every 10 minutes (`Persistent=true`, up to 60 s random delay) and starts `revisionist-scheduler.service`, a oneshot running `revisionist scheduler run`. Each run ticks each due campaign at most once.
- `revisionist-backup.timer` fires nightly at 03:30 (up to 15 min random delay) and runs `revisionist-backup`.
- Logs: `journalctl --user -u revisionist-scheduler.service` and `-u revisionist-backup.service`.

## 6. Operating the scheduler

```
revisionist scheduler status                       # next due time, paused?, failures, last status
revisionist scheduler advance [--campaign ID]      # tick on the next poll
revisionist scheduler pause [--campaign ID] [--reason TEXT]
revisionist scheduler resume [--campaign ID]       # also clears the failure count
revisionist scheduler run --offline                # a dry run with the fixture providers
```

A campaign ticks when its due time passes, when you `advance`, or when every human seat has filed an order since the last tick. A new campaign is first due one tick after the scheduler first sees it. Missed ticks (server down) are skipped, not backfilled.

When the unit shows **failed**:

- **Three failures in a row** (model call errors, a crash): the campaign is paused with `3 consecutive failures`. The tick in progress is safe; it resumes where it stopped. Read the journal, fix the cause, `revisionist scheduler resume`.
- **Canary leak** (exit 3): the campaign is paused at once with `canary leak at tick N`. Run `revisionist leaks` and `revisionist why <event>` with `REVISIONIST_DB` set before resuming.

## 7. Backups and the restore drill

The nightly backup writes `revisionist-<UTC timestamp>.dump` (`pg_dump -Fc`) to `REVISIONIST_BACKUP_DIR`, checks the archive reads back, deletes local dumps older than `REVISIONIST_BACKUP_KEEP_DAYS`, then copies the new dump to `REVISIONIST_BACKUP_DEST` with `rsync` (or `rclone copy` for an `rclone:` target). Retention at the destination is the destination's business.

Run the drill after setup, after any Postgres upgrade, and monthly:

```
set -a; . ~/.config/revisionist/revisionist-admin.env; set +a
revisionist-restore-drill
```

It restores the newest local dump into a scratch database, runs `revisionist db verify` against it (replays every campaign from event 0, checks every tick's state hash and the latest snapshot), drops the scratch database, and exits non-zero on any failure. Pull a dump back from the off-box target into `REVISIONIST_BACKUP_DIR` now and then and drill that one too, so the off-box copy is proven, not assumed.

Real restore onto a new server: create the roles and an empty `revisionist` database as in §1, then `pg_restore --exit-on-error --no-owner --dbname="$REVISIONIST_ADMIN_DSN" <dump>` and `REVISIONIST_DB=... revisionist db verify`. Do not run `revisionist db migrate` before the restore; the dump carries `schema_migrations`.

## 8. Still to do on the real server (Josh)

- [ ] The Layer 8 exit run: a solo campaign on `tick_hours: 12` running three days with no manual trigger. `revisionist scheduler status` and `revisionist db verify` at the end.
- [ ] A real restore: the drill above against a dump fetched back from `REVISIONIST_BACKUP_DEST`.
- [ ] Confirm the units start under linger after a reboot (`systemctl --user list-timers`).

## 9. Discord (Layer 9a)

Solo play from Discord: briefings arrive by DM, orders go in by `/order` or a plain DM to the bot. Only the human seat is on Discord; AI factions and the outlet keep writing files to `REVISIONIST_BRIEFINGS_DIR`. Postgres only.

### 9.1 Create the application and bot

1. Create a private Discord server for the campaign (you are its owner).
2. In the Discord Developer Portal, **New Application** (its own application, not shared with any other bot). Under **Bot**: turn **Public Bot** off, turn every **Privileged Gateway Intent** off (Presence, Server Members, Message Content), and **Reset Token** to get the token. It is shown once; it goes straight into the environment file below, never into the repo, a shell history or a chat.
3. Under **OAuth2 → URL Generator**: scopes `bot` and `applications.commands`; bot permissions **View Channels**, **Send Messages**, **Embed Links**, **Read Message History** only. Open the URL and add the bot to the server.
4. Turn on **Developer Mode** in your Discord client (Settings → Advanced) and copy three IDs (right click → Copy ID): the server, your own user, and, if you want a DM fallback, a channel only you and the bot can see.

### 9.2 Environment

Add to `~/.config/revisionist/revisionist.env` (already `chmod 600`):

```
REVISIONIST_DISCORD_TOKEN=<bot token>
REVISIONIST_DISCORD_GUILD_ID=<server ID>
REVISIONIST_DISCORD_HOST_ID=<your user ID>
```

| Variable | Meaning |
|---|---|
| `REVISIONIST_DISCORD_TOKEN` | the bot token. When set on Postgres, `revisionist tick`, `revisionist scheduler run` and `/advance` deliver bound seats' briefings by DM |
| `REVISIONIST_DISCORD_GUILD_ID` | the one server the bot serves; slash commands are synced there |
| `REVISIONIST_DISCORD_HOST_ID` | the user allowed `/advance`, `/pause`, `/resume`; unset means nobody |

If Discord refuses a DM because the player's DMs are closed to the bot, the briefing is **held**: nothing is sent anywhere, the tick closes, and the player reads it with `/dispatch`. There is no fallback channel, because a shared one would show one player's briefing to another (Layer 9b).

With the token in `revisionist.env`, the scheduler (§5) delivers by DM too: it reads the same file.

### 9.3 Migrate, link, run

```
cd ~/src/revisionist && git pull
uv tool install --editable '.[postgres,discord]' --force
set -a; . ~/.config/revisionist/revisionist-admin.env; set +a
revisionist db migrate                               # "applied 0003_discord"
set -a; . ~/.config/revisionist/revisionist.env; set +a
revisionist discord link --user "$REVISIONIST_DISCORD_HOST_ID"   # binds the campaign's human seat to you
revisionist discord seats
cp deploy/systemd/revisionist-discord.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now revisionist-discord.service
journalctl --user -u revisionist-discord.service -f
```

`revisionist discord link` takes `--campaign` (default the newest) and `--seat` (default the human seat; a non-human seat is refused). `revisionist discord unlink` removes a binding. The bot is a long-running unit (`Restart=on-failure`); `revisionist discord run` runs it in the foreground for a look.

### 9.4 First-run smoke check

1. The journal shows the bot logged in, with no traceback.
2. In the server, type `/` : `order`, `briefing`, `dispatch`, `status`, `join`, `advance`, `pause`, `resume`, `bug`, `report`, `key` are listed. They exist only in this server, not in DMs.
3. Run `/status` in any channel of the server. The reply is ephemeral (only you see it) and matches `revisionist status`.
4. DM the bot an order. It replies `filed ...` or with a question, and `revisionist status` shows it pending.
5. `revisionist tick --offline` (or `/advance` for a live tick): your briefing arrives by DM, and no `t<tick>-<your seat>.md` file appears in `REVISIONIST_BRIEFINGS_DIR` (the outlet's still does).

If `/status` says you hold no seat, the link is missing or `REVISIONIST_DISCORD_GUILD_ID` names another server. If the briefing never arrives and `revisionist scheduler status` shows `50007`, or the delivery shows `held`, your DMs from server members are closed: open them for this server, and meanwhile read the briefing with `/dispatch`.

### 9.5 Still to do (Josh)

- [ ] The live smoke check above.
- [ ] The Layer 9a exit run: 10 ticks of the solo campaign played from Discord (briefings by DM, orders by `/order` or DM), then `revisionist leaks` reports 0 and `revisionist db verify` passes.

### 9.6 A second player (Layer 9b)

```
set -a; . ~/.config/revisionist/revisionist-admin.env; set +a
revisionist db migrate                                    # applies 0005_discord_invites
set -a; . ~/.config/revisionist/revisionist.env; set +a
revisionist new salt-strait-duo                           # two human seats: Varda and Ostrel
revisionist discord link --user "$REVISIONIST_DISCORD_HOST_ID" --seat "First Minister of Varda"
revisionist discord invite --seat "Ostrel War Council"    # prints a code once; valid 72 h
```

Send the code to the other player privately, never in a server channel. They join the Discord server by an ordinary server invite, then run `/join <code>` there. The bot binds them to the seat, spends the code and sends them a test DM; if their DMs are closed it says so, and their briefings wait for `/dispatch`. `revisionist discord invites` lists every code issued and whether it was used. A spent, expired or unknown code gets the same refusal. With two human seats, the CLI's `order`, `briefing` and `status` need `--seat`.

### 9.7 The public channel (Layer 9b)

Create one text channel named `#chronicle` and give the bot **View Channel** and **Send Messages** there, nothing more. Then `revisionist db migrate` (applies `0006_discord_channels`) and `revisionist discord channel public <channel ID>`. From the next tick, every player's spin line is posted there once, as `<seat name>: <text>`, and the press outlet's narration is posted there instead of written to a file. Nothing else goes to a channel: briefings stay in DMs, because the server owner can read every channel and private thread. Create no other channels, roles or threads for the campaign; their names and membership would say who is in it with whom.

### 9.8 Sealing campaigns at rest (Layer 9b)

```
uv tool install --editable '.[postgres,discord,seal]' --force
revisionist admin new-master-key ~/.config/revisionist/master.key     # mode 0600, never overwritten
echo "REVISIONIST_MASTER_KEY_FILE=$HOME/.config/revisionist/master.key" >> ~/.config/revisionist/revisionist.env
set -a; . ~/.config/revisionist/revisionist-admin.env; set +a
revisionist db migrate                                    # applies 0007_seal_keys
```

From then on every **new** campaign is sealed: its events and snapshots are AES-GCM ciphertext in Postgres and in every dump, under a campaign key, and each player's own words (orders, their agent's questions, their private briefings, their bug notes) under a key of their own. All the data keys are stored wrapped by the master key, which never enters the database. Campaigns created before stay plaintext; sealing is forward only. The variable holds a path, not the key, and the file must be readable by its owner only. The scheduler and the bot read it from `revisionist.env`; add it to `revisionist-admin.env` too, so the restore drill (§7) can verify a sealed dump.

**Copy the master key off the box now, somewhere other than the backups.** Without it every sealed campaign is unreadable, by you included, and nothing can recover it.

**Reading private records (break glass).** On a sealed campaign, `revisionist why` shows only public records and says how many it hid. `revisionist why <id> --break-glass --reason "<why>"` and `revisionist admin show <event id> --reason "<why>"` show private ones, after first appending a Public `admin_read` with your reason. The players see "Admin read: the host opened N private records" in `#chronicle` after the next tick. If the log cannot be written, nothing is shown. The CLI refuses `order`, `briefing` and `status` for a seat another player holds on Discord.

**Erasing a player's words.** `revisionist admin erase --seat "<seat>" --reason "<why>"` (needs `REVISIONIST_ADMIN_DSN`) destroys that seat's key. Its orders, its agent's questions, its private briefings and its bug notes become unreadable at once in the live database, and in backups once your local retention (`REVISIONIST_BACKUP_KEEP_DAYS`) and the off-box copies' have passed, because old dumps still carry the wrapped key. What their play did to the world stays; replay and `db verify` keep working. Restart the bot afterwards so it drops the key from memory. None of this protects players from the host, who holds the master key and the bot token; it protects the data at rest and makes the host's reads visible.

### 9.9 Still to do for Layer 9b (Josh)

- [ ] Re-record the 4 re-keyed recordings live, on a host where `claude` is logged in: delete `tests/recordings/two-rivers-agent.json` and the two `rekeyed_from` entries in `two-rivers-tick-1.json`, then `REVISIONIST_RECORD=1 uv run pytest tests/test_agent.py tests/test_llm.py -k live`. Review the diff before committing.
- [ ] Generate the master key (§9.8) and copy it off the box, separately from the dumps.
- [ ] `revisionist db migrate` (0005-0007), create `#chronicle` and set it (§9.7).
- [ ] `revisionist new salt-strait-duo`, link your seat, invite the other player (§9.6); they `/join` and confirm the test DM arrives. Try a closed DM: the briefing is `held` and `/dispatch` shows it.
- [ ] Tell the other player the threat model: the host can break glass, and every such read is logged and announced.
- [ ] The Layer 9b exit run: 10 ticks with both players, perceived odds visible, then `revisionist leaks` and `revisionist leaks --humans` report 0, `revisionist db verify` passes with "sealed", and the restore drill passes with the master key.

## 10. Upgrading a host set up under the old name

The project was called Wyrd Protocol through Layer 9a. Everything it installed carried that name: roles `wyrd_app` and `wyrd_admin`, database `wyrd`, `~/.config/wyrd/wyrd.env` and `wyrd-admin.env`, `~/.local/share/wyrd`, the `wyrd` tool, units `wyrd-scheduler`, `wyrd-backup` and `wyrd-discord`, dumps `wyrd-*.dump` and `WYRD_*` variables. Skip this section if none of that exists. Do every step in one sitting: the per-campaign advisory lock key changed with the rename, so an old and a new process must never run side by side.

1. Stop and disable everything old:
   ```
   systemctl --user disable --now wyrd-scheduler.timer wyrd-backup.timer wyrd-discord.service
   rm ~/.config/systemd/user/wyrd-*.service ~/.config/systemd/user/wyrd-*.timer
   rm ~/.local/bin/wyrd-backup ~/.local/bin/wyrd-restore-drill
   ```
2. Rename the database and roles as a Postgres superuser, with nobody connected:
   ```
   ALTER DATABASE wyrd RENAME TO revisionist;
   ALTER ROLE wyrd_app RENAME TO revisionist_app;
   ALTER ROLE wyrd_admin RENAME TO revisionist_admin;
   ```
   Postgres clears an MD5 password when a role is renamed; a SCRAM password survives. If a login fails afterwards, set the password again with `\password`.
3. Move the configuration and data directories, then rename the files and every key in them:
   ```
   mv ~/.config/wyrd ~/.config/revisionist
   mv ~/.config/revisionist/wyrd.env ~/.config/revisionist/revisionist.env
   mv ~/.config/revisionist/wyrd-admin.env ~/.config/revisionist/revisionist-admin.env
   sed -i 's/^WYRD_/REVISIONIST_/; s#/wyrd\b#/revisionist#g' ~/.config/revisionist/*.env
   mv ~/.local/share/wyrd ~/.local/share/revisionist
   ```
   Check by eye that the DSNs now name the `revisionist` database and roles, and that the briefings and backup paths point at `~/.local/share/revisionist`.
4. Swap the tool: `uv tool uninstall wyrd-protocol`, then from the checkout `uv tool install --editable '.[postgres,discord]'`.
5. `set -a; . ~/.config/revisionist/revisionist-admin.env; set +a` and `revisionist db migrate`. It applies `0004_rename`, which renames the trigger function `wyrd_append_only` to `revisionist_append_only`.
6. Install and enable the new units and scripts as in §5 and §9.3.
7. Rename the existing dumps so the restore drill finds them: `cd ~/.local/share/revisionist/backups && for f in wyrd-*.dump; do mv "$f" "revisionist-${f#wyrd-}"; done`. Rename the off-box copies the same way, or leave them and remember the old prefix.
8. Run the restore drill (§7) and `revisionist scheduler status`.
