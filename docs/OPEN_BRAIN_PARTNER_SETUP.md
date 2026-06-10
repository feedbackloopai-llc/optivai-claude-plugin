# Open Brain — Partner Setup Guide

The plugin's semantic memory ("Open Brain") needs a Postgres database with the `pgvector` extension. This guide covers three free options. Pick one — the rest of the install is identical.

## TL;DR

```bash
git clone <repo> ~/dev/optivai-claude-plugin
cd ~/dev/optivai-claude-plugin
# 1. Set up a pgvector-capable Postgres (see options below)
export DATABASE_URL="postgresql://user:pass@host/db?sslmode=require"
export ANTHROPIC_API_KEY="sk-ant-..."   # for memory metadata extraction
# 2. Install
bash scripts/install.sh
# 3. Initialize the schema
python3 scripts/open_brain.py --init
# 4. Smoke test
python3 scripts/open_brain.py --capture "hello brain"
python3 scripts/open_brain.py --search "hello"
```

If those last two commands return your thought, you're done.

---

## Why pgvector?

The plugin stores 768-dim embeddings produced by `sentence-transformers/all-mpnet-base-v2` and queries them via pgvector's `<=>` cosine distance operator. Swapping in Pinecone/Weaviate/Qdrant would require a code port — pgvector is baked into `open_brain.py` and the schema in `sql/BRAIN_SCHEMA.sql`. Stick with a pgvector-capable Postgres.

---

## Option A — Neon (recommended)

Serverless Postgres with pgvector pre-installed. Free tier is more than enough.

**Limits (free):** 0.5 GB storage, 191 compute hrs/month, auto-suspend at idle (~500 ms cold-start). No credit card.

**Setup (about 3 minutes):**

1. Sign up at https://neon.tech (GitHub login works)
2. Create project → name "OpenBrain", any region close to you, Postgres 17
3. Dashboard → copy the **direct** connection string (NOT the `-pooler` one — pgvector breaks on pooled connections)
4. Add to `~/.zshrc`:
   ```bash
   export DATABASE_URL="postgresql://USER:PASSWORD@ep-xxx.us-east-1.aws.neon.tech/neondb?sslmode=require"
   ```
5. `source ~/.zshrc`

After signup, the `neonctl` CLI can automate project/branch/role creation if you want to scale this across multiple machines. Install with `npm i -g neonctl && neonctl auth`. Signup itself is not scriptable (see "Can we automate Neon signup?" at the bottom).

**Troubleshooting `type vector does not exist`:** you grabbed the pooler endpoint. Re-copy the direct one.

---

## Option B — Supabase

Hosted Postgres with pgvector available via extension. Free tier comparable to Neon.

**Limits (free):** 500 MB database, 2 GB egress/month, project pauses after 1 week of inactivity (resumes on demand).

**Setup:**

1. Sign up at https://supabase.com (GitHub login works)
2. New project → name, region, generate a strong DB password (save it)
3. Project dashboard → **Database** → **Extensions** → enable `vector`
4. **Project Settings** → **Database** → copy the **Connection string** (URI mode, "Use connection pooling" OFF)
5. Add to `~/.zshrc`:
   ```bash
   export DATABASE_URL="postgresql://postgres.xxx:PASSWORD@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
   ```
6. `source ~/.zshrc`

**Note:** Supabase's "transaction pooler" (port 6543) also breaks pgvector. Use the **session pooler** (port 5432) or direct connection.

---

## Option C — Local Docker Postgres + pgvector

Zero cloud dependency. Good if you don't want a SaaS account or need to work offline. Trade-off: data lives only on your machine, you manage backups.

**Requires:** Docker Desktop (https://docs.docker.com/desktop/).

**Setup:**

```bash
docker run -d \
  --name openbrain-pg \
  --restart unless-stopped \
  -e POSTGRES_PASSWORD=changeme \
  -e POSTGRES_DB=openbrain \
  -p 5432:5432 \
  -v openbrain-data:/var/lib/postgresql/data \
  pgvector/pgvector:pg17
```

Then add to `~/.zshrc`:
```bash
export DATABASE_URL="postgresql://postgres:changeme@localhost:5432/openbrain"
```

`source ~/.zshrc` and you're done. The `pgvector/pgvector` image ships with the extension pre-built — `open_brain.py --init` will run `CREATE EXTENSION vector` on first run.

**Backups:** `docker exec openbrain-pg pg_dump -U postgres openbrain > brain-$(date +%F).sql`

---

## Common Steps (all options)

After `DATABASE_URL` is exported:

```bash
# Install Python deps (you may need --break-system-packages on macOS)
pip install psycopg2-binary pgvector sentence-transformers anthropic

# Run the plugin installer
bash scripts/install.sh

# Initialize the schema
python3 scripts/open_brain.py --init

# Verify
python3 scripts/open_brain.py --capture "Initial setup test"
python3 scripts/open_brain.py --search "setup"
python3 scripts/open_brain.py --stats
```

`scripts/install.sh` will:
- Copy hooks to `~/.claude/hooks/`
- Copy commands, agents, skills to `~/.claude/`
- Write `~/.claude/hooks/auto-logger-config.json` with your `DATABASE_URL`
- Install the `beads` CLI (`pip install -e .`)
- Optionally install the `pg_sync` launchd daemon on macOS

---

## What Costs Money

| Component | Cost |
|---|---|
| Postgres host (Neon/Supabase free, or local Docker) | $0 |
| Embeddings — local CPU, `all-mpnet-base-v2` | $0 |
| Anthropic API for metadata extraction on capture | ~$0.01–0.02 / month at normal usage |
| **Total** | **~$0.01–0.02 / month** |

The Anthropic call on capture is small (Haiku 4.5, one short JSON extraction per thought). If you don't set `ANTHROPIC_API_KEY`, capture still works — you just get default metadata instead of LLM-classified topics/people/action items.

---

## Can We Automate Neon Signup?

Short answer: no, and you don't want to.

- Neon's signup is browser-OAuth (GitHub/Google) or email-verification. There's no `POST /signup` endpoint exposed, and they have Cloudflare bot management in front of it.
- The `neonctl` CLI assumes you already have an account — `neonctl auth` opens a browser to log in. Everything *after* signup (create project, create branch, create role, get connection string) is fully scriptable.
- Trying to scrape/automate the signup flow with headless browsers would (a) violate Neon's ToS, (b) get the account flagged, and (c) require maintaining captcha bypasses.

**Realistic onboarding playbook for a new partner:**
1. They spend 60 seconds in a browser at neon.tech signing up.
2. They run `npm i -g neonctl && neonctl auth` once.
3. From there, a script can do everything: `neonctl projects create --name OpenBrain --region-id aws-us-east-1`, fetch the connection URI, write it to their shell profile, run `install.sh`.

If we want a true one-command setup, **Option C (local Docker)** is the answer — no signup involved.
