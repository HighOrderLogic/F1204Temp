# Environment variables (client handoff)

Where values live, what each variable does, and **what you need to replace** when taking over the project.

Values are read from `backend/.env` locally and from **Vercel → Project Settings → Environment Variables** in production. Never commit real secrets to git.

---

## What you already have

You have access to the project mailbox (**`f12.04.livestockupdates@gmail.com`**). That inbox holds (or will hold) the **Supabase** project invite / credentials email. Use those details to fill the Supabase block below — you do **not** need a new Supabase project unless you choose to create one.

Also use that mailbox for:

| Use | Env vars |
|-----|----------|
| SMTP sending (Reports → Send Now / schedules) | `EMAIL_FROM`, `SMTP_USER`, `SMTP_PASSWORD` |
| Google account / app password for SMTP | `SMTP_PASSWORD` (Gmail **App Password**, not the normal login password) |

---

## Where to set variables

| Environment | Location |
|-------------|----------|
| Local dev | `backend/.env` (create/edit; gitignored) |
| Production (Vercel) | Project → **Settings** → **Environment Variables** → apply to Production (and Preview if needed) → **Redeploy** |
| Cron URLs | [cron-job.org](https://cron-job.org) — paste the same `CRON_SECRET` into the query string (see [`CRONJOB.md`](CRONJOB.md)) |

After changing Vercel env vars, trigger a new deployment or the running app will keep old values.

### After a database password reset

Resetting the password in Supabase → **Project Settings → Database** does not update the app. Only the password segment of `DATABASE_URL` changes. Leave the user (`postgres.<project-ref>`), host, port **6543**, and database name as they are. This password has no `@`, so it does not need `%40`.

| Where | What to change |
|-------|----------------|
| `backend/.env` | Replace the password in `DATABASE_URL`. This file is gitignored. It does not affect the live site. |
| Vercel → **Settings → Environment Variables** | Same new `DATABASE_URL` for **Production** and **Preview**. Then **Redeploy**. A saved variable does nothing until the next deployment. |

Do not change these for a database password reset: `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `JWT_SECRET`, `EID_PSEUDONYM_KEY`, `CRON_SECRET`, SMTP settings, or the cron-job.org URLs. Those are not the database password.

The pooler can reject the new password for a short time right after the reset. If it still says `password authentication failed` after a few minutes, the string in `DATABASE_URL` does not match the password saved in the Supabase dashboard.

---

## What you need to replace

Mark each item when done.

### From the Supabase email / dashboard (required for production DB)

| Variable | Replace with | Where to find it |
|----------|--------------|------------------|
| `DATABASE_URL` | Supabase **connection pooler** URI (port **6543**, user often `postgres.<project-ref>`) | Supabase → **Project Settings → Database → Connection string** (URI, *Transaction* pooler). If the DB password contains `@`, encode it as `%40` |
| `SUPABASE_URL` | `https://<project-ref>.supabase.co` | Supabase → **Project Settings → API → Project URL** |
| `SUPABASE_ANON_KEY` | `anon` `public` key (JWT) | Supabase → **Project Settings → API → Project API keys** |
| `SUPABASE_SERVICE_ROLE_KEY` | `service_role` key (JWT) — **server only, never expose to the browser** | Same API keys page |

Optional but recommended for clarity:

| Variable | Replace with |
|----------|--------------|
| `DATABASE_BACKEND` | `postgres` on Vercel / when using Supabase (local offline can use `duckdb`) |

### Generate yourself (do not reuse demo/dev values)

| Variable | Replace with | How |
|----------|--------------|-----|
| `JWT_SECRET` | New random secret (≥ 32 chars) | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `EID_PSEUDONYM_KEY` | New random secret (required in production) | Same command. **Changing this later changes every masked animal ID** users see |
| `CRON_SECRET` | New random secret | Same command. Then update **all** cron-job.org URLs that include `?cron_secret=` |

### Email (mailbox you already have)

| Variable | Replace with |
|----------|--------------|
| `EMAIL_FROM` | `f12.04.livestockupdates@gmail.com` (or keep as-is if that is the sender) |
| `SMTP_USER` | Same mailbox address |
| `SMTP_PASSWORD` | Gmail **App Password** for that account (Google Account → Security → App passwords) |
| `EMAIL_DRY_RUN` | `false` on Vercel when you want real delivery; `true` only for safe testing |
| `EMAIL_PROVIDER` | Keep `smtp` unless you switch to Resend |

### Deployment / app settings

| Variable | Replace with |
|----------|--------------|
| `CORS_ORIGINS` | Your real dashboard origin(s), comma-separated — e.g. `https://dashboard-f12-04.vercel.app` (add `http://localhost:3000` for local frontend if needed) |
| `DEFAULT_FARM_ID` | Keep `KF` unless your primary farm id differs |
| `DEFAULT_TIMEZONE` | Keep `Australia/Sydney` unless the farm timezone changes |
| `LOGOS_LOCAL_PATH` | Keep `frontend/assets/logos` unless you move logo files |

### Usually leave alone

| Variable | Notes |
|----------|--------|
| `JWT_EXPIRY_HOURS` | Default `24` is fine |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USE_TLS` | Keep Gmail defaults (`smtp.gmail.com`, `587`, `true`) unless you change provider |
| `APP_ENV` | Optional; Vercel sets `VERCEL` / `VERCEL_ENV` automatically |
| `RESEND_API_KEY` | Only if `EMAIL_PROVIDER=resend` |

---

## Full reference

### Database / Supabase

| Variable | Required | Role |
|----------|----------|------|
| `DATABASE_URL` | Prod: **yes** | Postgres connection string used by the API (`psycopg2`). Prefer Supabase **pooler** port **6543** on Vercel |
| `DATABASE_BACKEND` | Recommended | `postgres` \| `duckdb` \| `auto`. Use `postgres` with Supabase; `duckdb` for offline local only |
| `DUCKDB_PATH` | No | Path to local DuckDB file when `DATABASE_BACKEND=duckdb` (default `data/dev.duckdb`) |
| `SUPABASE_URL` | Yes if using Supabase features / handoff | Project URL |
| `SUPABASE_ANON_KEY` | As needed by tooling | Public anon key |
| `SUPABASE_SERVICE_ROLE_KEY` | Server-side only | Elevated key; never ship to frontend |

### Auth / privacy

| Variable | Required | Role |
|----------|----------|------|
| `JWT_SECRET` | **Yes** (prod) | Signs login access tokens. Compromise = forged sessions |
| `JWT_EXPIRY_HOURS` | No | Token lifetime in hours (default `24`) |
| `EID_PSEUDONYM_KEY` | **Yes** (prod) | HMAC secret that masks EIDs for non-admins (`ID-XXXXXXXX`). Missing/legacy key → API returns 503 |

### Email (SMTP)

| Variable | Required | Role |
|----------|----------|------|
| `EMAIL_PROVIDER` | No | `smtp` (default) or `resend` |
| `EMAIL_FROM` | Yes to send | Message `From:` address |
| `EMAIL_DRY_RUN` | No | `true` = pretend send; `false` = real send |
| `SMTP_HOST` | For SMTP | Server host (default `smtp.gmail.com`) |
| `SMTP_PORT` | For SMTP | Port (default `587`) |
| `SMTP_USER` | For SMTP | Login username (full Gmail address) |
| `SMTP_PASSWORD` | For SMTP | App password / SMTP password |
| `SMTP_USE_TLS` | For SMTP | `true` → STARTTLS before login |
| `RESEND_API_KEY` | If Resend | Resend API key |

Details: [`EMAIL_SYSTEM.md`](EMAIL_SYSTEM.md).

### Cron / ops

| Variable | Required | Role |
|----------|----------|------|
| `CRON_SECRET` | **Yes** (prod) | Auth for `/api/health/db`, `/api/email/process-due`, `/api/anomalies/warm` |

Job URLs and schedules: [`CRONJOB.md`](CRONJOB.md).

### App defaults

| Variable | Required | Role |
|----------|----------|------|
| `CORS_ORIGINS` | Prod: **yes** | Allowed browser origins (comma-separated) |
| `DEFAULT_TIMEZONE` | No | Used for schedule semantics (default `Australia/Sydney`) |
| `DEFAULT_FARM_ID` | No | Fallback farm id (default `KF`) |
| `LOGOS_LOCAL_PATH` | No | On-disk logo folder for local/dev branding |
| `APP_ENV` | No | `development` \| `test` \| `production` (Vercel also sets platform env) |

---

## Template (`backend/.env`)

Copy and replace every `REPLACE_…` / placeholder. Do not paste production secrets into chat or git.

```env
# --- Supabase (from the email / Supabase dashboard) ---
DATABASE_BACKEND=postgres
DATABASE_URL=postgresql://postgres.YOUR_PROJECT_REF:YOUR_DB_PASSWORD@aws-0-ap-southeast-2.pooler.supabase.com:6543/postgres
SUPABASE_URL=https://YOUR_PROJECT_REF.supabase.co
SUPABASE_ANON_KEY=YOUR_SUPABASE_ANON_KEY
SUPABASE_SERVICE_ROLE_KEY=YOUR_SUPABASE_SERVICE_ROLE_KEY

# --- Auth (generate new values) ---
JWT_SECRET=REPLACE_WITH_secrets_token_hex_32
JWT_EXPIRY_HOURS=24
EID_PSEUDONYM_KEY=REPLACE_WITH_secrets_token_hex_32

# --- Email (mailbox you already have) ---
EMAIL_PROVIDER=smtp
EMAIL_FROM=f12.04.livestockupdates@gmail.com
EMAIL_DRY_RUN=false
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=f12.04.livestockupdates@gmail.com
SMTP_PASSWORD=REPLACE_WITH_GMAIL_APP_PASSWORD
SMTP_USE_TLS=true

# --- Cron (generate new; update cron-job.org URLs) ---
CRON_SECRET=REPLACE_WITH_secrets_token_hex_32

# --- App ---
CORS_ORIGINS=https://YOUR-VERCEL-APP.vercel.app,http://localhost:3000
DEFAULT_TIMEZONE=Australia/Sydney
DEFAULT_FARM_ID=KF
LOGOS_LOCAL_PATH=frontend/assets/logos
```

Generate secrets:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## Quick verification

1. **DB:** open the dashboard, log in, confirm farms/charts load  
2. **Email:** Reports → Direct Email → Send Now with `EMAIL_DRY_RUN=false`  
3. **Cron:** `GET https://YOUR-APP.vercel.app/api/health/db?cron_secret=…` → `{"status":"ok","database":"up"}`  
4. **EID mask:** log in as a non-admin user and confirm animal ids look like `ID-…` (not raw EIDs)

If login or data fails after swapping Supabase, re-check `DATABASE_URL` (pooler port, password encoding) and that the schema/seed was applied on that project (see [`SUPABASE_SETUP.md`](SUPABASE_SETUP.md)).
