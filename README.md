# Livestock Dashboard

Python FastAPI backend + HTML/CSS/JS frontend for livestock feedlot performance analytics. Multi-tenant on Supabase PostgreSQL, deployable to **Vercel**.

Dashboard link: https://dashboard-f12-04.vercel.app/

## Features

- **7 dashboard pages**: Summary Stats, Time Series, Prediction, Distributions, Cohorts, Data Management, Reports
- **Shared filter sidebar** with debounced queries, saved views (localStorage), EID privacy for non-admin users
- **JWT auth** with scrypt passwords (admin / owner / user roles)
- **Email scheduling** (daily/weekly/monthly/once) with cron worker
- **Admin CSV upload** with snapshot backup and skip/overwrite duplicates
- **Multi-farm tenancy** (`KF` Killara Feedlot, `WOW` WOW Feedlot Killara); animal data is loaded separately (see Step B)

## Quick Start (Local)

### 0. Prerequisites

Install Python 3.12+ from [python.org](https://www.python.org/downloads/). On Windows, tick **"Add python.exe to PATH"** in the installer, then open a new terminal in the repository root and check:

```bash
python --version        # Windows fallback: py -3 --version; macOS/Linux: python3 --version
python -m uvicorn --version   # prints a version only after step A of section 3 (or run_dev.bat) installs dependencies
```

If `python` opens the Microsoft Store, Python is not installed (or not on `PATH`); do this step first, because the commands in step 1 need it.

### 1. Configure environment

Create `backend/.env` before starting the server (it is read at startup; `run_dev.bat` does not create it). The commands below create it only if it is missing, with a generated `EID_PSEUDONYM_KEY` and `DATABASE_BACKEND=duckdb` for offline development.
For Supabase instead, set `DATABASE_URL` to the pooler URL (port 6543; encode @ in password as %40) and `DATABASE_BACKEND=postgres`.

**Windows (PowerShell):**
```powershell
if (-not (Test-Path backend\.env)) {
  $key = python -c "import secrets; print(secrets.token_hex(32))" 2>$null
  if (-not $key) { $key = py -3 -c "import secrets; print(secrets.token_hex(32))" 2>$null }
  if ($key) { Set-Content -Path backend\.env -Value "EID_PSEUDONYM_KEY=$key","DATABASE_BACKEND=duckdb" }
  else { Write-Host "Python not found. Complete step 0 first." }
}
```

**Windows (Command Prompt, interactive, from repo root):**
```bat
if not exist backend\.env for /f %i in ('python -c "import secrets; print(secrets.token_hex(32))"') do (echo EID_PSEUDONYM_KEY=%i> backend\.env & echo DATABASE_BACKEND=duckdb>> backend\.env)
```

**macOS / Linux:**
```bash
[ -f backend/.env ] || printf "EID_PSEUDONYM_KEY=%s\nDATABASE_BACKEND=duckdb\n" "$(python3 -c 'import secrets; print(secrets.token_hex(32))')" > backend/.env
```

**Required: `EID_PSEUDONYM_KEY`.** The secret used to mask animal EIDs (`ID-XXXXXXXX`) for non-admin users.
Without it the API refuses every request (HTTP 503) and the pages show a banner saying it is missing.
Keep it secret and don't change it once users have seen masked EIDs: a new key gives every animal a new mask.

### 2. Quick Run (Windows 1-Click Launcher)

On Windows, execute the automated launcher directly from the repository root:

```bat
run_dev.bat
```
*`run_dev.bat` automatically locates your Python environment (.venv -> python -> py -3), validates dependencies, initializes DuckDB if needed, and starts Uvicorn on port 8000.*

When the console shows the server is running, open http://localhost:8000/login.html and sign in with an account from [Default credentials](#default-credentials) below (for example `admin` / `admin123`). A fresh clone has no animal data yet; see Step B in section 3 for loading it.

---

### 3. Manual Installation & Execution

#### Step A: Install dependencies
```bash
# From repository root:
pip install -r requirements.txt

# Or from backend directory:
pip install -r backend/requirements.txt
```

#### Step B: Seed offline development database (DuckDB)
```bash
# Initialize schema and default credentials:
python scripts/init_duckdb.py --force
```
This creates the schema, default users, and farms `KF` and `WOW`; `animal_data` starts empty. Load data with one of:
- `python scripts/init_duckdb.py --csv path/to/data.csv` (CSV only; loads farm `KF`)
- `python scripts/admin.py upload --farm <ID> --file <ID>_<name>.csv --api http://localhost:8000 --token <admin JWT>` (CSV only; the filename must start with the farm ID, e.g. `KF_2026-10-20.csv`)
- `scripts/import_data2.py` (PostgreSQL only; imports `Data2.xlsx` as farm `WOW`)

*Note: DuckDB is developer-only for offline local development and barred in production/Vercel environments.*

#### Step C: Start server
Always invoke uvicorn via Python module syntax to avoid system PATH resolution issues:

**Windows (PowerShell / Command Prompt):**
```powershell
# Using Python directly:
cd backend
python -m uvicorn app.main:app --reload --port 8000

# Or using the Windows Python Launcher fallback:
cd backend
py -3 -m uvicorn app.main:app --reload --port 8000
```

**macOS / Linux:**
```bash
cd backend
python3 -m uvicorn app.main:app --reload --port 8000
```

#### Troubleshooting Python on Windows
- If `python` is not recognized or opens the Microsoft Store:
  - Run with the Windows Python Launcher: `py -m uvicorn app.main:app --reload --port 8000`
  - Or reinstall Python 3.12+ from [python.org](https://www.python.org/downloads/) and explicitly check **"Add python.exe to PATH"** during installation.

Open http://localhost:8000/login.html

**Or two processes:**

```bash
# Terminal 1
cd backend
python -m uvicorn app.main:app --reload --port 8000

# Terminal 2
cd frontend
python -m http.server 3000
```

Open http://localhost:3000/login.html

### Default credentials

| Username | Password | Role | Access Scope |
|----------|----------|------|--------------|
| admin | admin123 | Admin | Global access across all farms (KF, WOW), unmasked EIDs, full administrative controls |
| owner | owner123 | Owner | Single-farm tenancy (KF), unmasked raw EIDs, raw/cohort CSV export, hidden admin controls |
| user | user123 | User | Single-farm tenancy (KF), read-only analytics, pseudonymized EIDs (ID-XXXXXXXX), restricted export |

## Deploy to Vercel

Repo: https://github.com/F12-04/Dashboard  

Full guide: [docs/VERCEL_DEPLOYMENT.md](docs/VERCEL_DEPLOYMENT.md)

Quick summary:
1. Complete [Supabase setup](docs/SUPABASE_SETUP.md) and seed data
2. Push this repo to GitHub
3. Import on [vercel.com](https://vercel.com) — **Root Directory = `.`**
4. Add env vars (`DATABASE_URL`, `JWT_SECRET`, `EID_PSEUDONYM_KEY`, `CORS_ORIGINS`, etc.)
5. Deploy → open `https://your-app.vercel.app/login.html`

After first deploy, set:

```
CORS_ORIGINS=https://your-app.vercel.app
```

then redeploy.

## API Health Check

```bash
curl https://your-app.vercel.app/api/health
# {"status":"ok","version":"1.0.0"}
```

## Project Structure

```
backend/app/           FastAPI application
frontend/              HTML/CSS/JS dashboard (primary UI)
api/                   Vercel serverless entry (Mangum)
database/              PostgreSQL schema
scripts/seed.py        Seed users + KF data from Data.csv (PostgreSQL)
scripts/init_duckdb.py Seed dev-only embedded DuckDB database (offline dev)
scripts/forecast_job.py Preview or backfill a farm's weight forecasts (Prediction page)
ml/                   Forecasting code and the trained XGBoost model
```

See [docs/PROJECT_STRUCTURE.md](docs/PROJECT_STRUCTURE.md) for layout details.

## Weight Prediction (MVP)

The **Prediction** page shows a 182-day (6-month) FinalPweight forecast per animal, starting from each
animal's last recorded day. Admins pick real EIDs; other users pick masked IDs, which the server resolves.

- **Model:** recursive XGBoost trained on the 2026 workbook (training script, backtest report and
  experiments live on the `ml` branch). The model is used for as many days as its backtest covered, then
  the animal follows the farm's typical daily gain for its weight (median gain per 50 kg band, never above
  the farm-wide median), so projections slow down at heavy weights. That part is an unvalidated
  projection. One model serves every animal; those with fewer than 14 consecutive recent days are flagged
  as "short history" on the page.
- **Tested range:** `fade_days` in `ml/models/model_meta.json`, set by the training script (`FADE_DAYS`).
  It is 20 for the current model, the furthest the backtest could check on 98 days of training data.
  Forecasts use the model for that many days and store the number with each row, and the page marks those
  days as tested. Nothing in the app needs editing: a model retrained on a longer dataset and backtested
  further ahead carries its own range, which the next refresh picks up.
- **Forecasts refresh on every upload, on the server.** After `POST /api/admin/farms/{farm_id}/upload`
  saves the records, the same request recomputes the farm's forecasts (a few seconds) and stores them in
  the `forecasts` table; the Prediction page only reads that table. The response's `forecasts` field
  says how it went. If the refresh fails, the upload still stands; retry with
  `POST /api/admin/farms/{farm_id}/forecasts/refresh`, which is also how to apply a newly deployed model.
  Send `forecast=false` with the upload to skip the refresh.
- **No xgboost on the server.** xgboost needs scipy, and the two would not fit in Vercel's 250 MB function
  limit, so the API runs the trained trees with numpy (`ml/tree_model.py`, from `ml/models/xgb_weight.json`).
  The tests check that this gives the same predictions as xgboost.
- The first refresh creates the `forecasts` table (the `DATABASE_URL` user needs permission to create
  tables; Supabase's default `postgres` user has it).

```bash
python scripts/admin.py upload --farm KF --file KF_2026-10-20.csv --api https://your-app.vercel.app --token <admin JWT>
python scripts/admin.py forecast --farm KF --api https://your-app.vercel.app --token <admin JWT>   # refresh only
python scripts/admin.py users list --token <admin JWT>
python scripts/admin.py farms snapshots --farm KF --token <admin JWT>
python scripts/forecast_job.py --farm KF --dry-run  # preview against the database backend/.env selects
```

The same upload and forecast actions are available in the dashboard's **Admin** tab (see [Admin Panel](#admin-panel)).

To try it locally without touching Supabase, keep `DATABASE_BACKEND=duckdb` in `backend/.env`, run
`python -m uvicorn app.main:app --port 8000` from `backend`, and upload to `--api http://localhost:8000`.
Uploads and refreshes then go through that one server, so DuckDB's one-writer limit does not get in the way.

After retraining (`ml` branch), export the model for the server as well, then redeploy and refresh:
`python -c "import xgboost as x; b = x.Booster(); b.load_model('ml/models/xgb_weight.ubj'); b.save_model('ml/models/xgb_weight.json')"`.

## Admin Panel

The **Admin** tab (admins only) covers everything `scripts/admin.py` does, in the browser:

- **Farm Status** — record count, snapshot backups, last upload time (`GET /api/admin/farms/{farm_id}/status`).
- **Upload History** — the per-farm audit trail of CSV uploads: rows inserted/skipped/overwritten, duplicate
  mode, uploader (`GET /api/admin/farms/{farm_id}/uploads`).
- **Users** — list all users, manage status, assign farms, and create accounts
  (`GET`/`POST /api/admin/users`, `PUT /api/admin/users/{id}/status`, `PUT /api/admin/users/{id}/farms`). CLI parity is available via `scripts/admin.py users`.
- **Farm Snapshots & Rollback** — view automated backup snapshots and revert to previous database states (`GET /api/admin/farms/{farm_id}/snapshots`, `POST /api/admin/farms/{farm_id}/revert`). CLI parity is available via `scripts/admin.py farms`.
- **Variables & Dynamic Filters** — inspect discovered columns, calculate distinct counts with NULL weighting, and toggle dropdown filters and production measures (`GET`/`PUT /api/admin/farms/{farm_id}/columns`, `POST /api/admin/farms/{farm_id}/columns/inspect`). Enforces mandatory weight measure guard (`weight`, `pweight`, `finalpweight`).
  The saved choices apply to every analytical request for that farm (via `FilterState`): any enabled numeric
  column can be selected as a measure, disabled measures fall back to the farm's default, disabled built-in
  filters (sex/treatment/breed/mob) are hidden and ignored, and other enabled categorical columns appear as
  extra sidebar filters (`extra_filters`) that narrow the data but don't split charts into comparison groups.
  Farms with no saved configuration keep the original seven measures and four filters.
  Auto-detected suggestions turn on only the original seven measures the farm has data for (plus `weight` or
  `pweight` when `finalpweight` has no data); other numeric columns are left for an admin to opt into.
- **Extra upload columns** — CSV/XLSX columns outside the standard set are kept: each new one is added to
  `animal_data` (and to snapshot storage, so reverts keep it) as numeric if every value is a number, otherwise
  text. Empty columns are skipped, and a file adding more than 50 new columns is rejected. The upload response
  lists `columns_added` and `columns_ignored`. On PostgreSQL the `DATABASE_URL` user needs permission to
  `ALTER TABLE animal_data`.
- **Upload CSV** — same request as the CLI `upload` command: choose duplicate handling (skip/overwrite)
  and whether forecasts refresh afterwards (they do by default).
- **Forecasts** — the CLI `forecast` command: recompute the farm's Prediction-page forecasts, e.g. to
  retry a failed refresh or after deploying a retrained model.

The CLI stays the right tool for scripting, CI, and working against a deployed instance without logging
in; the panel and the CLI call the same endpoints.

## Tests

```bash
# Run all tests (from backend directory)
cd backend
python -m pytest

# Run fast in-memory unit tests
python -m pytest -m unit

# Run HTTP API route contract tests
python -m pytest -m api
```

## Runbook

- **Change passwords**: `PUT /api/auth/password` or re-run seed
- **Upload CSV**: `POST /api/admin/farms/KF/upload` (or the Admin tab)
- **Create user**: `POST /api/admin/users` (or `scripts/admin.py users create`)
- **Revert farm snapshot**: `POST /api/admin/farms/KF/revert` (or `scripts/admin.py farms revert`)
- **Inspect dynamic columns**: `POST /api/admin/farms/KF/columns/inspect` (or the Admin tab)
- **Email dry run**: `EMAIL_DRY_RUN=true`
- Run the suites: `python -m pytest -m unit`, `python -m pytest -m api`, or `python -m pytest -m integration` (see Tests above)
