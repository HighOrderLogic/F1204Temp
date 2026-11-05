# Email System

Automated livestock report delivery for the F12_04 dashboard: **Send Now**, recurring **schedules**, and a cron worker that sends due mail with a PDF attachment.

Replaces the R **blastula** + DuckDB schedule worker from the original Shiny app.

---

## What it does

| Mode | Behaviour |
|------|-----------|
| **Send Now** | Build a PDF from the current sidebar filters and email it immediately |
| **Schedule** | Store `daily` / `weekly` / `monthly` / `once` jobs with Sydney send times |
| **Manage** | List (farm-scoped), pause/activate, delete |
| **Cron worker** | `GET/POST /api/email/process-due` sends every active row with `next_send_at <= now` |

UI lives on the **Reports** tab (`frontend/js/pages/reports.js`).  
API lives under `/api/email/*` on the **heavy** Vercel function (`api/index.py`).

---

## Architecture

```text
Reports UI
    │
    ├─ POST /api/email/send-now          → PDF + SMTP/Resend now
    ├─ POST /api/email/schedules         → insert row + compute next_send_at
    ├─ GET  /api/email/schedules         → list (optional ?farm_id=)
    ├─ PATCH /api/email/schedules/{id}   → pause / activate
    └─ DELETE /api/email/schedules/{id}
              │
              ▼
     email_schedules (Postgres / DuckDB)
              │
              ▼  cron every ~5 min (cron-job.org) + daily Vercel Hobby cron
     GET /api/email/process-due?cron_secret=…
              │
              ▼
     DataService → ReportGenerator.generate_pdf → EmailService.send_report_email
```

| Layer | Path |
|-------|------|
| Router | [`backend/app/routers/email_schedules.py`](backend/app/routers/email_schedules.py) |
| Send + schedule math | [`backend/app/services/email_service.py`](backend/app/services/email_service.py) |
| PDF | [`backend/app/services/report_generator.py`](backend/app/services/report_generator.py) |
| Cron auth | [`backend/app/auth/cron.py`](backend/app/auth/cron.py) |
| Schema | [`database/001_schema.sql`](database/001_schema.sql) (`email_schedules`) |
| External cron docs | [`CRONJOB.md`](CRONJOB.md) |

---

## API

All user routes require a JWT (`Authorization: Bearer …`) and farm access.

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/email/schedules` | List schedules. Omit `farm_id` → all farms the user can access; with `?farm_id=` → that farm only |
| `POST` | `/api/email/schedules` | Create schedule (captures current `report_filters` + charts) |
| `PATCH` | `/api/email/schedules/{id}` | `{ "is_active": true\|false }` — reactivation recomputes `next_send_at` |
| `DELETE` | `/api/email/schedules/{id}` | Delete |
| `POST` | `/api/email/send-now` | Immediate send |
| `GET`/`POST` | `/api/email/process-due` | Cron worker (secret required) |

### Send Now body

```json
{
  "farm_id": "KF",
  "recipient_email": "manager@farm.com",
  "email_subject": "Automated Livestock Report",
  "email_body": "Please find your livestock performance report attached.",
  "report_filters": { "...": "FilterState fields from getFilters()" },
  "report_charts": ["Summary Statistics", "Time Series", "Distribution"],
  "report_format": "PDF"
}
```

Success: `{ "detail": "Email sent", "dry_run": false }`  
Dry-run: `{ "detail": "Email dry-run only (not sent). …", "dry_run": true }`

### Create schedule body

```json
{
  "farm_id": "KF",
  "schedule_name": "Weekly Summary",
  "recipient_email": "manager@farm.com",
  "frequency": "weekly",
  "send_time": "09:00",
  "day_of_week": 1,
  "day_of_month": null,
  "send_date": null,
  "email_subject": "Automated Livestock Report",
  "email_body": "",
  "report_filters": { },
  "report_charts": ["Distribution", "Summary Statistics"],
  "report_format": "PDF"
}
```

- `frequency`: `daily` \| `weekly` \| `monthly` \| `once`
- `send_time`: `HH:MM` or `HH:MM:SS` (Sydney wall clock)
- `day_of_week`: 1 = Monday … 7 = Sunday (weekly)
- `day_of_month`: 1–28 used in practice (monthly; code clamps to ≤ 28)
- `send_date`: ISO date for `once`

---

## Database: `email_schedules`

| Column | Notes |
|--------|--------|
| `farm_id` | FK → `farms` |
| `recipient_email` | Required |
| `schedule_name` | Display name |
| `frequency` | `daily` / `weekly` / `monthly` / `once` |
| `send_time` | `TIME`, default `09:00:00` |
| `send_date` | For `once` |
| `day_of_week` | 1–7 for weekly |
| `day_of_month` | For monthly |
| `next_send_at` | Precomputed `TIMESTAMPTZ` — cron selects `is_active AND next_send_at <= now` |
| `is_active` | Pause without deleting |
| `last_sent` | Last successful send |
| `email_subject` / `email_body` | Overrides |
| `report_filters` | JSONB snapshot of sidebar filters at create time |
| `report_charts` | Text array of section names |
| `report_format` | `PDF` (default; HTML variants exist in schema) |
| `created_by` | Username |

Indexes: `(is_active, next_send_at)`, `(farm_id)`.

---

## Scheduling (`next_send_at`)

Timezone: **Australia/Sydney** (`ZoneInfo("Australia/Sydney")` in `compute_next_send`).

| Frequency | Rule |
|-----------|------|
| `daily` | Today at `send_time`, or tomorrow if that time has passed |
| `weekly` | Next matching ISO weekday at `send_time` |
| `monthly` | Day-of-month (clamped 1–28) this month, or next month if past |
| `once` | `send_date` + `send_time`; after a successful send the row is **deactivated** |

**Delivery ≠ exact minute.** The worker only runs when cron hits `/api/email/process-due`.  
On Vercel Hobby the built-in cron is **once daily** (`0 23 * * *` UTC ≈ **10:00 Sydney** AEDT). For nearer-to-schedule delivery, use cron-job.org every **5 minutes** (see below and [`CRONJOB.md`](CRONJOB.md)).

---

## Email content

### Subject

Default: `Automated Livestock Report - {farm_name}`  
(Send Now may append the farm name in the UI; schedules use the stored subject.)

### HTML body

Built by `EmailService.build_email_body`:

- Greeting + farm name
- Optional custom message
- **Executive KPI table** (mean / median / std / min / max / count per selected measure)
- Bullet list of applied filters (year, month, day, demographics, measures)

### Attachment

One PDF: `report.pdf`, generated by `ReportGenerator.generate_pdf` from the stored (or current) filters and chart section list.

Supported chart section labels (Reports UI checkboxes):

- `Summary Statistics`
- `Time Series`
- `Distribution`
- `Cohorts`

---

## Providers & environment variables

Set these in `backend/.env` locally and in **Vercel → Project Settings → Environment Variables** for production. Loaded via `app/config.py`; sending logic is in `EmailService` (`email_service.py`).

### Shared email settings

| Variable | Role |
|----------|------|
| `EMAIL_PROVIDER` | Which transport to use: `smtp` (project default) or `resend` |
| `EMAIL_FROM` | From address on the message (`From:`). For SMTP this should usually match the mailbox you authenticate as (e.g. the Gmail address). Falls back to `SMTP_USER` if unset when sending via SMTP |
| `EMAIL_DRY_RUN` | `true` → API reports success but **does not** connect to SMTP/Resend (`dry_run: true` in the response). Use `false` in production to actually deliver mail |
| `CRON_SECRET` | Shared secret for `/api/email/process-due` (and other cron routes). Not used for Send Now |

### SMTP (`EMAIL_PROVIDER=smtp`)

Used by `_send_via_smtp` with Python `smtplib` (STARTTLS login + send).

| Variable | Default | Role |
|----------|---------|------|
| `SMTP_HOST` | `smtp.gmail.com` | SMTP server hostname |
| `SMTP_PORT` | `587` | SMTP port (587 for STARTTLS) |
| `SMTP_USER` | _(empty)_ | Login username (full email for Gmail). **Required** to send; missing → `"SMTP_USER / SMTP_PASSWORD are not configured"` |
| `SMTP_PASSWORD` | _(empty)_ | Login password or **app password** (Gmail with 2FA). **Required** to send |
| `SMTP_USE_TLS` | `true` | If true, call `starttls()` after connect before `login` |

Example (Gmail app password):

```env
EMAIL_PROVIDER=smtp
EMAIL_FROM=f12.04.livestockupdates@gmail.com
EMAIL_DRY_RUN=false
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=f12.04.livestockupdates@gmail.com
SMTP_PASSWORD=your-app-password
SMTP_USE_TLS=true
```

### Resend (`EMAIL_PROVIDER=resend`)

| Variable | Role |
|----------|------|
| `RESEND_API_KEY` | API key for Resend. **Required** when provider is `resend`; missing → `"RESEND_API_KEY is not configured"` |
| `EMAIL_FROM` | Must be a verified sender/domain in the Resend project |

### Production checklist

1. `EMAIL_DRY_RUN=false`
2. For SMTP: `SMTP_USER` + `SMTP_PASSWORD` set and able to authenticate
3. `EMAIL_FROM` matches the sending identity
4. `CRON_SECRET` matches the cron-job.org URL query param for process-due

---

## Cron worker

### Auth (any one)

- `?cron_secret=YOUR_CRON_SECRET`
- `Authorization: Bearer YOUR_CRON_SECRET` (Vercel Cron when `CRON_SECRET` is set)
- `X-Cron-Secret: YOUR_CRON_SECRET`
- `x-vercel-cron: 1` (accepted in non-production only)

### Algorithm (`EmailService.process_due_emails`)

1. Load active schedules with `next_send_at <= now` (Sydney), join `farms` for name  
2. For each row: rebuild filters → filter data as admin → PDF → HTML body → send  
3. On success: set `last_sent`; for `once` deactivate; else set new `next_send_at`  
4. Return `{ "sent": N, "failed": M }`

### Production cron setup

| Source | Schedule | URL |
|--------|----------|-----|
| **cron-job.org** (recommended) | Every **5 minutes** | `https://dashboard-f12-04.vercel.app/api/email/process-due?cron_secret=YOUR_CRON_SECRET` |
| Vercel Hobby (`vercel.json`) | Daily `0 23 * * *` UTC | `/api/email/process-due` |

Use **HTTPS**. `http://` returns **308** and cron-job.org will not follow it.

Full job list (Supabase awake, dashboard awake, email, anomaly cache): [`CRONJOB.md`](CRONJOB.md).

---

## Reports UI flows

### Send Now

1. Toggle **Direct Email** on the Reports card  
2. Enter recipient / subject / message; charts come from the pill toggles  
3. Sidebar filters → `report_filters`  
4. `POST /api/email/send-now`  
5. Toast success, dry-run warning, or error  

### Create schedule

1. Name, recipient, frequency, Sydney time, day-of-week / day-of-month as needed  
2. Snapshot of `getFilters()` stored as `report_filters`  
3. Default charts: Distribution + Summary Statistics  
4. Table shows name, farm, recipient, frequency, send time, next send (Sydney), active/pause/delete  

Schedules are **farm-scoped** in the DB; the list endpoint without `farm_id` shows every farm the user can access (useful for admins/owners).

---

## Errors (user-facing)

| Case | Message / behaviour |
|------|---------------------|
| Missing / invalid email | `Enter a valid recipient email` (400) |
| Bad filters | `Invalid report filters: …` (400) |
| PDF build failure | `Failed to build report PDF: …` (500) |
| SMTP / Resend failure | Provider error string (500) |
| Dry run | 200 with `dry_run: true` and guidance to set `EMAIL_DRY_RUN=false` |
| Cron bad secret | `401 Invalid cron secret` |

Never return SMTP passwords or API keys in responses. Failures in the cron loop increment `failed` and continue to the next schedule.

---

## Local / test tips

```bash
# Dry-run (default in many .env setups)
EMAIL_DRY_RUN=true

# Force a worker run (local)
curl -X GET "http://localhost:8000/api/email/process-due?cron_secret=$CRON_SECRET"
```

Unit coverage: `backend/tests/unit/reporting/test_email_service.py` (and related reporting tests).

---

## Ops notes

1. Scheduled mail uses filters **frozen at create time**, not the live sidebar.  
2. Cron runs as **admin** for data access (full EIDs in the PDF path).  
3. If `report_filters.year` is missing on a due row, the worker falls back to year `2023` (legacy KF seed). Prefer always saving a year from the UI.  
4. Hobby cold starts can delay a due send until the next successful cron invocation — prefer the 5-minute external job over relying only on the daily Vercel cron.
