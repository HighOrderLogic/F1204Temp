# cron-job.org jobs (F12_04 Dashboard)

External crons for [dashboard-f12-04.vercel.app](https://dashboard-f12-04.vercel.app).  
Configure at [cron-job.org](https://cron-job.org). Use **HTTPS** (not `http://`) or you will get a **308** redirect that cron-job.org does not follow.

Replace `YOUR_CRON_SECRET` with the `CRON_SECRET` value from Vercel env / `backend/.env`. Do not commit the real secret.

| # | Job | Schedule | Method | Auth |
|---|-----|----------|--------|------|
| 1 | Supabase awake | **Once per day** | `GET` | `?cron_secret=` |
| 2 | Dashboard awake | **Every 5 minutes** | `GET` | none |
| 3 | Email sending | **Every 5 minutes** | `GET` | `?cron_secret=` |
| 4 | Anomaly cache | **Every 5 minutes** | `GET` | `?cron_secret=` |

---

## 1. Supabase awake

Keeps the free Supabase project from pausing after ~7 days with no DB traffic. Hits Postgres with `SELECT 1`.

- **URL:** `https://dashboard-f12-04.vercel.app/api/health/db?cron_secret=YOUR_CRON_SECRET`
- **Method:** `GET`
- **Schedule:** once per day (e.g. 02:00 UTC)
- **Success:** `{"status":"ok","database":"up"}`
- **Wrong secret:** `401`

---

## 2. Dashboard awake

Pings the **light** Vercel function (`api/light`) so login / filters stay warmer between demos.

- **URL:** `https://dashboard-f12-04.vercel.app/api/health`
- **Method:** `GET`
- **Schedule:** every 5 minutes
- **Success:** `{"status":"ok","version":"…","tier":"light"}`
- **Note:** No secret. Does **not** touch Postgres (see job 1 for that).

---

## 3. Email sending

Processes due report email schedules. Needed because Vercel Hobby only allows one built-in cron per day; this job runs more often for nearer-to-schedule delivery.

- **URL:** `https://dashboard-f12-04.vercel.app/api/email/process-due?cron_secret=YOUR_CRON_SECRET`
- **Method:** `GET` (also accepts `POST`)
- **Schedule:** every 5 minutes
- **Auth:** `?cron_secret=` or `Authorization: Bearer YOUR_CRON_SECRET` or `X-Cron-Secret`
- **Wrong secret:** `401`

---

## 4. Anomaly cache

Warms the heavy anomaly stack, preloads farm dataframes, and **writes durable analyze snapshots** so the Anomalies tab can serve from `/api/anomalies/snapshot` instead of recomputing for 3–5s on every cold instance.

- **URL:** `https://dashboard-f12-04.vercel.app/api/anomalies/warm?cron_secret=YOUR_CRON_SECRET&preload=1&compute=1`
- **Method:** `GET` (also accepts `POST`)
- **Schedule:** every 5 minutes
- **Query flags:**
  - `preload=1` — load KF / WOW dataframes into memory on that instance
  - `compute=1` — recompute default Overall/All snapshots into `anomaly_snapshots` (required for fast tab loads)
- **Success (shape):** `{"status":"ok","warm":"anomalies","farm_ids":["KF","WOW"],"preloaded_rows":{…},"snapshots":[…]}`
- **Wrong secret:** `401`
- **Note:** First run with `compute=1` can take ~20–30s while scoring both farms; later runs refresh the same snapshots.

---

## Checklist

1. All URLs use `https://dashboard-f12-04.vercel.app/…` (not `http://`, not `f12-04.vercel.app`).
2. `CRON_SECRET` in Vercel matches the query param used in jobs 1, 3, and 4.
3. After changing anomaly code, confirm job 4 returns `snapshots` with `"ok": true` for each farm.
4. Optional: Vercel still has a daily cron on `/api/email/process-due` in `vercel.json`; job 3 is the frequent path.

## Related code

| Job | Route |
|-----|--------|
| Supabase awake | `backend/app/routers/keepalive.py` → `/api/health/db` |
| Dashboard awake | `api/light/index.py` → `/api/health` |
| Email sending | `backend/app/routers/email_schedules.py` → `/api/email/process-due` |
| Anomaly cache | `backend/app/routers/anomalies.py` → `/api/anomalies/warm` (+ snapshots in `anomaly_snapshot.py`) |
