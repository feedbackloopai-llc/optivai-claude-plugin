# Open Brain — Backup and Restore Runbook

Your Open Brain is institutional knowledge: every decision, learned pattern, person note, and derived belief lives in the Neon PostgreSQL database. This document covers belt-and-suspenders backup beyond Neon's built-in Point-in-Time Recovery (PITR), plus the restore path when you need it.

## Why have your own backups when Neon has PITR?

Neon retains PITR for the duration of your plan's window (typically 7 days on free/pro tiers, longer on higher tiers). That covers the common "I just made a mistake" case. Your own dump-and-store gives you:

- **Coverage past the PITR window** (months, years if you want)
- **Independence from Neon itself** — survives the rare regional incident or project deletion
- **Time-machine semantics** — keep a weekly snapshot for any rolling N weeks; diff between weeks for audit
- **Air-gap option** — copy dumps to S3, an external disk, anywhere truly separate from your active infra

If your Open Brain is at all a load-bearing memory store for your work (and once it is, it really is), this is non-optional.

## Install — one-time prerequisites

You need `pg_dump`. On macOS this is part of `libpq`; on Linux it's typically `postgresql-client`.

### macOS

```bash
brew install libpq
```

`libpq` is keg-only because it conflicts with full PostgreSQL. **Do not** `brew link --force libpq`. Use the absolute path: `/opt/homebrew/opt/libpq/bin/pg_dump` (or `/usr/local/opt/libpq/bin/pg_dump` on Intel Macs).

### Linux

```bash
# Debian/Ubuntu
sudo apt install postgresql-client

# RHEL/Fedora
sudo dnf install postgresql
```

`pg_dump` lands on PATH directly — no special path needed.

### Verify the install

```bash
pg_dump --version          # Linux
/opt/homebrew/opt/libpq/bin/pg_dump --version   # macOS
```

A version of 16, 17, or 18 is fine. Newer pg_dump can always dump older Neon servers (forward-compatible).

## Manual backup — one-liner

```bash
mkdir -p ~/Backups/brain && \
  pg_dump "$DATABASE_URL" | gzip > ~/Backups/brain/brain-$(date +%Y%m%d-%H%M%S).sql.gz
```

(On macOS replace `pg_dump` with `/opt/homebrew/opt/libpq/bin/pg_dump`.)

The dump captures every schema and table in your Open Brain DB:

- `brain.thoughts` — primary atom store (the main asset)
- `brain.thought_versions` — version history (the RB primitive)
- `brain.promotions` — Hebbian weight audit
- `brain.replay_log` — chronological brain operations
- `brain.forget_audit` — VF_ε forget audit
- `brain.knowledge_graph_edges` + `brain.knowledge_graph_nodes` — symbolic graph
- Any supporting schemas (`landing.*`, `neon_auth.*`, etc.)

Plus all DDL, indexes, pgvector embeddings (as bytea), constraints, and ownership.

## Automatic weekly backup

### macOS — use launchd, not cron

**Important:** cron on macOS has been deprecated since Catalina in favor of launchd. Modern macOS requires granting Full Disk Access to `/usr/sbin/cron` via System Settings → Privacy & Security before cron can fire at all. Skip that headache and use a LaunchAgent.

Create `~/Library/LaunchAgents/com.<your-username>.brain-backup.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.YOUR_USERNAME.brain-backup</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>-c</string>
        <string>mkdir -p "$HOME/Backups/brain" &amp;&amp; /opt/homebrew/opt/libpq/bin/pg_dump "$DATABASE_URL" | gzip &gt; "$HOME/Backups/brain/brain-$(date +%Y%m%d).sql.gz" &amp;&amp; find "$HOME/Backups/brain" -name 'brain-*.sql.gz' -mtime +60 -delete</string>
    </array>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Weekday</key>
        <integer>0</integer>
        <key>Hour</key>
        <integer>3</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>

    <key>EnvironmentVariables</key>
    <dict>
        <key>DATABASE_URL</key>
        <string>YOUR_NEON_DSN_HERE</string>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>

    <key>StandardOutPath</key>
    <string>/Users/YOUR_USERNAME/Backups/brain/.launchd-out.log</string>

    <key>StandardErrorPath</key>
    <string>/Users/YOUR_USERNAME/Backups/brain/.launchd-err.log</string>

    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
```

Then bootstrap and verify:

```bash
# Lint the plist syntax
plutil -lint ~/Library/LaunchAgents/com.YOUR_USERNAME.brain-backup.plist

# Load into your user session
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.YOUR_USERNAME.brain-backup.plist

# Verify it loaded (shows state, schedule, environment)
launchctl print gui/$(id -u)/com.YOUR_USERNAME.brain-backup

# Test fire RIGHT NOW (don't wait until Sunday)
launchctl kickstart -p gui/$(id -u)/com.YOUR_USERNAME.brain-backup

# Confirm it produced a file
ls -lah ~/Backups/brain/
```

The schedule above fires every Sunday at 03:00 local time. If your Mac is asleep at fire time, launchd will defer until the next wake. Survives reboots because LaunchAgents in `~/Library/LaunchAgents/` auto-load at login.

To disable, unload, or reload:

```bash
launchctl bootout gui/$(id -u)/com.YOUR_USERNAME.brain-backup
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.YOUR_USERNAME.brain-backup.plist
```

### Linux — cron works fine

```bash
crontab -e
```

Add (substitute your DSN):

```
DATABASE_URL=postgresql://YOUR_USER:YOUR_PASS@your-host.neon.tech/neondb?sslmode=require
0 3 * * 0 mkdir -p ~/Backups/brain && pg_dump "$DATABASE_URL" | gzip > ~/Backups/brain/brain-$(date +\%Y\%m\%d).sql.gz && find ~/Backups/brain -name 'brain-*.sql.gz' -mtime +60 -delete
```

Verify:

```bash
crontab -l
sudo systemctl status cron     # confirm daemon is running
```

The `find ... -mtime +60 -delete` clause rolls off backups older than 60 days. Adjust to your retention preference.

## Verify a backup is healthy

```bash
# macOS — use gzcat (zcat on macOS only handles old .Z compress format, NOT .gz)
gzcat ~/Backups/brain/brain-YYYYMMDD-HHMMSS.sql.gz | head -40

# Linux — zcat handles .gz directly
zcat ~/Backups/brain/brain-YYYYMMDD-HHMMSS.sql.gz | head -40
```

You should see:

- `-- PostgreSQL database dump`
- `-- Dumped from database version ...`
- A `CREATE SCHEMA brain;` block
- `CREATE TABLE brain.thoughts (`

Sanity-check the atom count between the dump and your live DB:

```bash
# Atoms in the dump
gzcat ~/Backups/brain/brain-LATEST.sql.gz | grep -cE '^brain-[0-9]+-[a-f0-9]+'

# Atoms live (in Python)
python3 -c "
import sys; sys.path.insert(0, '$HOME/.claude/hooks')
import open_brain
c = open_brain._connect(); cur = c.cursor()
cur.execute('SELECT COUNT(*) FROM brain.thoughts')
print(cur.fetchone()[0])
"
```

They should match (allowing for atoms written since the dump). Confirm the dump ends with `-- PostgreSQL database dump complete` as the last meaningful line — that's pg_dump's success trailer.

## Restore — read this section before using

**The restore command is destructive if pointed at your live brain.** Always restore to a fresh DB or a Neon branch first, validate, then promote.

### Safe path: restore to a Neon branch

1. In the Neon dashboard, create a new branch from your current main branch. This is a copy-on-write fork — fast, free up to your branch limit, isolated from the live brain.
2. Copy the branch's connection string.
3. Apply your backup to the branch:

   ```bash
   gzcat ~/Backups/brain/brain-YYYYMMDD-HHMMSS.sql.gz | psql "NEW_BRANCH_DSN"
   ```
4. Connect to the branch with `psql "NEW_BRANCH_DSN"` and query a few atoms to confirm they look right.
5. If you want the branch to BECOME your live brain (e.g., recovering from a corruption), promote it in the Neon dashboard.
6. Don't forget to update your `DATABASE_URL` env var and any LaunchAgent / cron entries pointing to the old DSN.

### Local-DB path: restore to a fresh local PostgreSQL

If you want to inspect a backup without touching Neon at all:

```bash
# Start a fresh local PG (e.g., via Docker)
docker run -d --name brain-restore -e POSTGRES_PASSWORD=temp -p 15432:5432 pgvector/pgvector:pg17

# Wait for it to come up
sleep 5

# Install the pgvector extension is already done in that image. Now restore:
gzcat ~/Backups/brain/brain-YYYYMMDD-HHMMSS.sql.gz | \
  psql "postgresql://postgres:temp@localhost:15432/postgres"

# Connect and query
psql "postgresql://postgres:temp@localhost:15432/postgres"
\dn               # list schemas — should see 'brain'
SELECT COUNT(*) FROM brain.thoughts;
```

The container is throwaway. `docker rm -f brain-restore` when done.

### What you cannot do

Don't run `psql "$DATABASE_URL" < backup.sql.gz` directly. That would attempt to apply DDL over your live schema and would fail noisily on conflicts (which is good — it stops you), but the partial state it leaves behind is hard to clean. Always restore to a separate target.

## What gets backed up — exact list

A `pg_dump` of the Open Brain DB captures:

- Every `brain.*` table (atoms, versions, promotions, replay log, forget audit, knowledge graph)
- Every supporting schema your Neon project uses (`landing`, `neon_auth`, etc.)
- All indexes including pgvector HNSW/IVF indexes
- pgvector embeddings as binary
- All FK constraints, CHECK constraints, ownership
- All sequences with their current values
- Schema-level DDL — including the `CREATE SCHEMA` and `CREATE EXTENSION pgvector` calls

What's NOT in the dump:

- Roles / users (`pg_dump --schema-only --no-owner` already handles this for portability)
- Server-side config (Neon manages this)
- Background WAL or replication state

For a Neon-side fingerprint of "what version of Postgres dumped this," check the header of the gzipped file:

```bash
gzcat ~/Backups/brain/brain-LATEST.sql.gz | head -10 | grep "Dumped from"
```

## Operational tips

- **Test your restore at least once a year.** A backup you've never restored is half a backup. Restore to a Neon branch, query a few atoms, throw the branch away.
- **Keep an off-host copy.** If your laptop dies and your only backups are on it, you have no backups. Sync `~/Backups/brain/` to S3 or another disk weekly:
  ```bash
  aws s3 sync ~/Backups/brain s3://your-bucket/brain-backups/ --exclude '.launchd-*'
  ```
- **Don't grow the retention window unbounded.** 60 days × ~11MB/week = ~95MB. A year × ~11MB/week = ~570MB. Fine for local disk, less fine if you sync everything to a metered cloud. Adjust `-mtime +60` in the cron/launchd command as needed.
- **Monitor backup success monthly.** Add `ls -lah ~/Backups/brain/` to your monthly Mac-housekeeping routine. If the most recent file is older than a week, something is wrong.
- **Watch for DB schema drift.** When the Open Brain schema changes (new column, new index), the next backup will quietly include the change. When you eventually restore, the restored DB will have the new schema. This is correct behavior, not a problem — but worth knowing so a restore doesn't surprise you.

## Troubleshooting

**LaunchAgent (macOS) doesn't fire on Sunday.**
Check it's loaded: `launchctl print gui/$(id -u)/com.YOUR_USERNAME.brain-backup`. The `state = ` line should show `waiting`. If `not loaded`, re-bootstrap. If `crashed`, check `~/Backups/brain/.launchd-err.log`.

**Cron (Linux) doesn't fire on Sunday.**
Check the daemon: `sudo systemctl status cron`. Check your crontab: `crontab -l`. Check syslog: `journalctl -u cron --since "1 week ago"`.

**pg_dump fails with "permission denied for schema brain".**
Your DSN points to a role without read permission. Use the `neondb_owner` role's DSN (the one your `open_brain.py --init` was run as).

**pg_dump fails with version mismatch.**
A `pg_dump` older than the server version may refuse. Newer pg_dump always works. If you somehow got a 14 client trying to dump a 17 server, upgrade your `libpq` / `postgresql-client`.

**Backup file is suspiciously small (under a MB).**
Either pg_dump failed mid-stream (check `~/Backups/brain/.launchd-err.log` or the cron mail), or the file was inspected mid-write. Wait for the writing process to finish, then re-check the file size.

**"zcat: can't stat: ... (.gz.Z): No such file or directory" on macOS.**
macOS `zcat` only handles `.Z`. Use `gzcat` (or `gunzip -c`) for `.gz` files.

## Related docs

- `NEON_SETUP_GUIDE.md` — initial Open Brain installation against Neon
- `OPEN_BRAIN_PARTNER_SETUP.md` — partner-facing onboarding
- `MS_EPS_PRIMER.md` — what's stored in the brain (atoms, MS_ε primitives, NAL, Hebbian)
