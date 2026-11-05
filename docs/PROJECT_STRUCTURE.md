# Project structure

Map of the livestock dashboard for someone opening this repository for the first time. It describes the tree on university `main`, where a change belongs, and the differences between a laptop and Vercel.

The product is a multi-farm feedlot dashboard. The browser is static HTML, CSS, and JavaScript. The API is FastAPI. Farm records live in Postgres (Supabase in production, DuckDB or local Postgres for development). Weight forecasts use a trained XGBoost model that the server runs with NumPy, because the XGBoost package does not fit in the Vercel function size limit.

---

## How a request moves

**On your laptop** there is one process. From `backend/`:

```bash
python3 -m uvicorn app.main:app --reload --port 8000
```

`backend/app/main.py` registers every router and, when `VERCEL` is not set, serves `frontend/` itself. Open `http://localhost:8000/login.html`. A second static server on another port also works: `dashboard.html` points `window.API_URL` at port 8000 when the page is not already on 8000.

**On Vercel** the same Python package is split into two functions so login and the first charts stay small. `vercel.json` chooses the function:

| Incoming path | Function | Why |
|---|---|---|
| `/api/health`, `/api/health/db` | `api/light/index.py` | No pandas. Health and the database ping. |
| `/api/auth/*`, `/api/filters/*`, `/api/summary/*` | light | Login and the first dashboard paint. |
| `/api/charts/timeseries`, `timeseries-grain`, `distribution` | light | Chart reads that stay on SQL. |
| `/api/anomalies/snapshot` | light | Read a stored anomaly snapshot. |
| `/api/anomalies/warm` and every other `/api/*` | `api/index.py` | Pandas, reports, email, admin, uploads. |
| `/`, `/login.html`, `/dashboard.html`, `/css`, `/js`, `/assets` | static `frontend/` | The API does not serve files on Vercel. |

`api/light/index.py` and `api/index.py` only add `backend/` to `sys.path` and import `app.*`. Do not put business logic in `api/`. Do not use `-r` includes in `api/requirements.txt` or `api/light/requirements.txt`. The Vercel Python builder does not follow them. Those two files list dependencies inline. The light file omits pandas, matplotlib, and reportlab.

`api/index.py` loads routers lazily: charts and summary first, then farms and data, then cohorts, anomalies, reports, email, and admin. A cold heavy request pays that import cost. A login request never should.

Region is `syd1` (`vercel.json`). Vercel Cron hits `GET /api/email/process-due` once a day (`0 23 * * *` UTC). More frequent email, anomaly warm, and database keepalive jobs are external. See `CRONJOB.md`.

---

## Repository tree

```
COMP3888/                         project root. Run git, seed, and the admin CLI from here.
├── api/
│   ├── index.py                  heavy Vercel entry
│   ├── requirements.txt          heavy function dependencies (inlined)
│   └── light/
│       ├── index.py              light Vercel entry
│       └── requirements.txt      slim dependencies (inlined)
├── backend/
│   ├── .env                      secrets. Gitignored. The app reads this file.
│   ├── requirements.txt          local and test dependencies
│   ├── pytest.ini                markers: unit, api, integration
│   ├── app/                      all application code
│   └── tests/
├── frontend/                     static UI. One dashboard shell, one JS file per tab.
├── database/
│   └── 001_schema.sql            base Postgres schema. Gitignored (*.sql).
├── ml/
│   ├── forecasting.py            forecast helpers used by the API
│   ├── tree_model.py             NumPy runner for the XGBoost JSON model
│   └── models/                   xgb_weight.json (deployed), xgb_weight.ubj, model_meta.json
├── scripts/                      seed, import, forecast refresh, admin CLI, logo helper
├── docs/                         local images (service logos). Not required to run the app.
├── vercel.json                   routes, region, daily email cron, security headers
├── docker-compose.yml            optional local Postgres 16. Mounts 001_schema.sql on first start.
├── requirements.txt              "-r backend/requirements.txt"
├── run_dev.bat                   Windows: venv, deps, DuckDB init, uvicorn on port 8000
└── README.md                     install, seed, and deploy checklist
```

Folders that used to exist and do not: `admin-cli/` (the CLI is `scripts/admin.py`). There is no React app and no Supabase Auth client.

---

## `backend/app`

| Path | Role |
|---|---|
| `main.py` | Local app: every router, CORS, 404 HTML for non-API paths, static `frontend/` when not on Vercel. |
| `config.py` | `Settings` from environment, then `backend/.env` if that file exists. `get_settings()` is cached. |
| `db.py` | Postgres pool, or DuckDB when Postgres is unavailable and `database_backend` allows it. Also the DDL for tables the schema file does not create (`forecasts`, `farm_columns`, `logo_assets`, snapshot row storage, credential history). |
| `key_guard.py` | If `EID_PSEUDONYM_KEY` is missing, API calls return 503 before they touch data. |
| `constants.py` | Measure names and display units. |
| `auth/dependencies.py` | JWT issue and `CurrentUser`. Roles are `admin`, `owner`, and `user`. |
| `auth/passwords.py` | scrypt hashes. |
| `auth/cron.py` | Accepts cron via `X-Cron-Secret`, `Authorization: Bearer`, `?cron_secret=`, or Vercel’s cron header where that route allows it. |
| `models/schemas.py` | Pydantic bodies, including `FilterState`. |
| `utils/anonymize.py` | Masks EIDs as `ID-XXXXXXXX` for users who cannot see the real id. The mask is an HMAC of the id with `EID_PSEUDONYM_KEY`. Changing the key changes every mask. |

`database_backend` is `auto`, `postgres`, or `duckdb`. `auto` uses `DATABASE_URL` and falls back to `backend/data/dev.duckdb` (path is `duckdb_path`, relative to `backend/`). Production must stay on Postgres. DuckDB is one writer and is refused on Vercel.

Production `DATABASE_URL` is the Supabase **transaction pooler**, port **6543**. Serverless functions open many short connections. If the password contains `@`, encode it as `%40`.

### Routers

Prefixes are applied in `main.py`. Paths below are the full URL path.

| Router | Prefix | What it is for |
|---|---|---|
| `auth.py` | `/api/auth` | `POST /login`, `GET /me`, `POST /logout`, `PUT /password`, `GET /bootstrap-status`, `POST /rotate-initial-password`. `PUT /username` is deprecated. |
| `farms.py` | `/api/farms` | Farms the user can open, and public logo file reads. |
| `filters.py` | `/api/filters` | `GET /choices`, `/bootstrap`, `/active-count`. Sidebar options for the active farm. |
| `summary.py` | `/api/summary` | `POST /stats`. Summary tab. |
| `charts.py` | `/api/charts` | `POST /timeseries`, `/timeseries-grain`, `/distribution`, `/custom`. |
| `cohorts.py` | `/api/cohorts` | `POST /analyze`, `GET /export.csv`. |
| `anomaly_fast.py` | `/api/anomalies` | `GET` and `POST /snapshot`. Served by the light function. |
| `anomalies.py` | `/api/anomalies` | `GET`/`POST /warm` (heavy), `POST /analyze`, `/detail`, `/export.csv`. |
| `animals.py` | `/api/data` | `POST /query`, `GET /export.csv`. Data Management rows. |
| `forecasts.py` | `/api/forecasts` | `GET /animals`, `GET /series`. Prediction tab reads the `forecasts` table. It does not train. |
| `reports.py` | `/api/reports` | `POST /generate`, `POST /chart.png`. |
| `email_schedules.py` | `/api/email` | Schedules CRUD, `POST /send-now`, cron `process-due`. |
| `keepalive.py` | `/api/health` | `GET`/`POST /api/health/db`. `SELECT 1` so a free Supabase project does not pause. |
| `admin.py` | `/api/admin` | Farms, CSV upload (single and chunked), columns, forecast refresh, snapshots, users, logos, credential audit. |

`GET /api/health` is declared on the app, not in a router. The light app adds `"tier": "light"`.

### Services

Put calculation here, not in routers.

| File | Role |
|---|---|
| `sql_agg.py` | SQL for summary, time series grain, and distribution. Hot paths should stay here instead of pulling the whole farm into pandas. |
| `filter_service.py` | Apply `FilterState` to a frame when SQL is not enough. `df_to_records` turns NaN and Inf into JSON `null`. |
| `choices_service.py` | Distinct values for the sidebar. |
| `variable_service.py` | Per-farm extra columns (`farm_columns`). Uploaded headers become measures or categories only after they pass the safe-name check. |
| `summary_service.py`, `chart_service.py` | Page-level aggregation when it is not a single SQL query. |
| `data_service.py` | Row query for Data Management. |
| `forecast_service.py` | Rebuild `forecasts` after an upload or `POST /api/admin/farms/{id}/forecasts/refresh`. |
| `anomaly_service.py`, `anomaly_snapshot.py` | Score animals and store a snapshot the light function can read. |
| `active_window.py` | Which dates count as the current herd. |
| `report_generator.py` | PDF and HTML reports (reportlab, matplotlib). |
| `email_service.py` | SMTP or Resend. `EMAIL_DRY_RUN=true` reports success and does not send. |
| `farm_ingestion.py` | Parse an upload, map columns, insert rows. |
| `farm_backup.py` | Snapshot rows before an upload so Admin can revert. |
| `labels.py` | Display names and units. |

### Auth

Login checks `users.password_hash` (scrypt) and signs an HS256 JWT with `JWT_SECRET`. The token carries `sub`, `username`, `role`, `is_admin`, and `farm_ids`. Farm scope is `user_farm_access`. A user cannot read another farm by changing `farm_id` in the query.

| Role | EIDs | Data Management and CSV export | Admin tab |
|---|---|---|---|
| `user` | Masked | Hidden. API returns 403. | Hidden |
| `owner` | Real | Yes | Hidden. `isAdmin()` in the browser is false for owners. |
| `admin` | Real | Yes | Users, farms, uploads, logos, audit |

The browser stores `access_token` and `user` in `sessionStorage` (`frontend/js/auth.js`).

---

## `frontend`

There is no bundler. `dashboard.html` is the shell: header, tab buttons, filter sidebar, and an empty `#page-content`. Each tab is a function in `frontend/js/pages/`, registered as `PAGE_RENDERERS` at the bottom of `dashboard.html`.

| Tab `data-page` | File | Renderer |
|---|---|---|
| `summary` | `pages/summary.js` | `renderSummary` |
| `timeseries` | `pages/timeseries.js` | `renderTimeseries` |
| `prediction` | `pages/prediction.js` | `renderPrediction` |
| `distributions` | `pages/distributions.js` | `renderDistributions` |
| `cohorts` | `pages/cohorts.js` | `renderCohorts` |
| `anomalies` | `pages/anomalies.js` | `renderAnomalies` |
| `data-management` | `pages/data-management.js` | `renderDataManagement` |
| `reports` | `pages/reports.js` | `renderReports` |
| `admin` | `pages/admin.js` | `renderAdmin` |

Shared browser code:

| File | Role |
|---|---|
| `js/api.js` | `apiFetch`. Same-origin `/api` on port 8000 and in production. |
| `js/auth.js` | Token, role checks, farm id (`?farm=` or the user’s first farm). |
| `js/filters.js` | Sidebar. Debounced reloads. Calls `renderCurrentPage` when filters change. |
| `js/saved-views.js` | Named filter sets in `localStorage`. |
| `js/utils.js` | Formatting and small helpers. |
| `css/variables.css` | Colours and spacing. |
| `css/components.css` | Buttons, pickers, tables. |
| `css/pages.css` | Tab layouts. |
| `css/login.css` | Login page only. |
| `login.html` | Sign-in. |
| `index.html` | Redirects to login. |
| `404.html` | Used by `main.py` for non-API 404s, and as a static page on Vercel. |

Plotly is loaded from a CDN the first time a tab needs a chart (`timeseries`, `prediction`, `distributions`, `cohorts`, `anomalies`). Summary, reports, data management, and admin do not load it.

Logos live in `frontend/assets/logos/<farm_id>/`. `default/` is the fallback. `LOGOS_LOCAL_PATH` defaults to `frontend/assets/logos`.

To add a tab: add a button in `dashboard.html`, a `pages/<name>.js` that defines `render<Name>`, a script tag, and a `PAGE_RENDERERS` entry. If the tab needs new data, add a router and decide whether `vercel.json` should send it to the light function. Only do that when the route stays free of pandas and report libraries.

---

## Database

`database/001_schema.sql` is the base schema. It is gitignored, so a fresh clone may not contain it until someone copies it in. Tables it creates:

| Table | Contents |
|---|---|
| `farms` | `farm_id` (`KF`, `WOW`, …), name, slug, `is_active`. |
| `users` | Username, scrypt `password_hash`, `is_admin`, `is_active`. Later migrations add `role`. |
| `user_farm_access` | Which farms a user may open. |
| `animal_data` | One row per animal per day. Unique `(farm_id, eid, date)`. Measures are nullable doubles. Extra uploaded columns are added at runtime. |
| `animal_data_snapshots` | Record that a backup was taken before an upload. |
| `email_schedules` | Recipient, frequency (`daily`, `weekly`, `monthly`, `once`), next send time, filters, charts, format. |
| `data_uploads` | File name, rows inserted, skipped, overwritten, `duplicate_mode`. |

Created by the app on first use, not by that SQL file:

| Table | Created from |
|---|---|
| `forecasts` | `db.py` / `forecast_service.py`. Prediction page. |
| `farm_columns` | Which extra columns a farm exposes as filters or measures. |
| `anomaly_snapshots` | `anomaly_snapshot.py`. |
| `animal_data_snapshots_data` | Row payload for revert. |
| `logo_assets` | Logo metadata. |
| `upload_chunks` | Chunked CSV upload. |
| `user_credentials_history` | Admin credential audit and revert. |

Seed data is not in git (`*.csv` and `*.xlsx` are ignored). Locally, `Data.csv` is farm `KF` and `Data2.xlsx` is farm `WOW`.

```bash
python scripts/init_duckdb.py --force          # empty DuckDB, default users, farms KF and WOW
python scripts/init_duckdb.py --csv path.csv   # also load that CSV as KF
python scripts/seed.py                         # Postgres: users and KF from Data.csv
python scripts/import_data2.py                 # Postgres: Data2.xlsx as WOW
```

After go-live, new rows should come through Admin upload or `scripts/admin.py upload`, which also refreshes forecasts.

---

## `ml/` and prediction

`ml/models/xgb_weight.json` is what production loads. `tree_model.py` walks the trees with NumPy. `ml/models/model_meta.json` holds `fade_days`: how many days of the 182-day forecast were actually backtested. Past that, the curve follows the farm’s typical daily gain for that weight band. Animals with fewer than 14 recent days are flagged as short history.

Training is not in this runtime tree. After a retrain, export JSON, commit it, redeploy, then refresh forecasts. An upload refreshes that farm automatically. `forecast=false` on the upload skips it. `POST /api/admin/farms/{farm_id}/forecasts/refresh` retries.

```bash
python scripts/forecast_job.py --farm KF --dry-run
python scripts/admin.py forecast --farm KF --api http://localhost:8000 --token <admin JWT>
```

---

## `scripts/admin.py`

Talks to a running API. It does not open the database itself, except `seed` and offline user recovery.

```text
upload            CSV, then forecast refresh. Filename must start with the farm id.
forecast          refresh only
seed              users and KF sample data
logos             logo audit
users             list, set-username, set-password, create, set-status,
                  assign-farms, delete, audit-log, recover
farms             status, snapshots, revert, delete, prune-snapshots
```

`--api` defaults to `http://localhost:8000`. `--token` or `ADMIN_TOKEN` is the admin JWT.

Other scripts: `manage_logos.py` (files on disk), `test_email.py` (one manual send). `eval_arima_*.py` and the `arima_*.csv` results are local experiments. The dashboard does not use ARIMA.

---

## Tests

From `backend/`:

```bash
python -m pytest                 # all
python -m pytest -m unit         # no database server
python -m pytest -m api          # TestClient route contracts
python -m pytest -m integration  # uploads and logos against a real engine
```

`backend/pytest.ini` sets `pythonpath` to `backend` and the repo root, so tests can import `app` and `ml`. Markers are strict: a new test needs `@pytest.mark.unit`, `api`, or `integration`.

Shared fakes live in `backend/tests/conftest.py` and `backend/tests/fixtures/`.

---

## Configuration

The process reads environment variables first and `backend/.env` when that file exists. Never commit `.env`. Names that matter:

| Variable | Effect |
|---|---|
| `DATABASE_BACKEND` | `auto`, `postgres`, or `duckdb`. |
| `DATABASE_URL` | Postgres URL. Production: pooler port 6543. |
| `DUCKDB_PATH` | Default `data/dev.duckdb` under `backend/`. |
| `JWT_SECRET` | Signs tokens. Local and Vercel must match if you reuse a token. |
| `JWT_EXPIRY_HOURS` | Default 24. |
| `EID_PSEUDONYM_KEY` | Required. Empty means every API call is 503. |
| `CRON_SECRET` | Cron routes. |
| `CORS_ORIGINS` | Extra allowed origins. Localhost and `*.vercel.app` are already allowed by regex. |
| `EMAIL_PROVIDER` | `smtp` or `resend`. |
| `EMAIL_DRY_RUN` | `true` skips the real send. Production sending needs `false`. |
| `SMTP_*` or `RESEND_API_KEY` | The chosen provider. |
| `EMAIL_FROM` | From address. |
| `DEFAULT_TIMEZONE` | `Australia/Sydney`. |
| `DEFAULT_FARM_ID` | `KF`. |
| `LOGOS_LOCAL_PATH` | Logo directory. |
| `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` | Present in settings. The current app talks to Postgres through `DATABASE_URL`, not the Supabase client. |

Detail and the values you must replace on a handover are in `ENV.md`.

---

## What is gitignored

`.gitignore` hides local data and most notes:

| Pattern | What that means |
|---|---|
| `backend/.env`, `.env` | Secrets. |
| `*.duckdb` | Local database. |
| `*.csv`, `*.xlsx` | Source workbooks and script outputs. |
| `*.sql` | `database/001_schema.sql` is local. |
| `*.md` except `README.md`, `CRONJOB.md`, `EMAIL_SYSTEM.md`, `ENV.md`, `CLIENT_SERVICES.md` | Other markdown, including this file, stays on disk and is not in git unless you add an exception. |

`SUPABASE_SETUP.md` and `VERCEL_DEPLOYMENT.md` are local operator notes. `CLIENT_SERVICES.md` is the non-technical picture of GitHub, Vercel, Supabase, and cron-job.org.

---

## Where to make a change

| Task | Start here |
|---|---|
| A tab’s layout or chart | `frontend/js/pages/<tab>.js`, then `frontend/css/pages.css` |
| Sidebar filters | `frontend/js/filters.js` and `backend/app/routers/filters.py` |
| A number that should stay fast | `backend/app/services/sql_agg.py` |
| A new API | Router under `backend/app/routers/`, register it in `main.py` and in `api/index.py` or `api/light/index.py`, then add a `vercel.json` route if it must hit the light function |
| Who can see a farm or an EID | `auth/dependencies.py`, `utils/anonymize.py`, `frontend/js/auth.js` |
| Upload behaviour | `services/farm_ingestion.py`, `services/farm_backup.py`, `routers/admin.py` |
| Prediction shape | `ml/tree_model.py`, `services/forecast_service.py`. Do not add the `xgboost` package to `api/requirements.txt`. |
| Scheduled email | `services/email_service.py`, `routers/email_schedules.py`, `CRONJOB.md` |
| Deploy routing | `vercel.json` only. Do not add a `functions` block next to `builds`; Vercel rejects that combination. |

After a behaviour change, extend the matching test under `backend/tests/unit/` or `backend/tests/api/`.
