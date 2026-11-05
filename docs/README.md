# Docs

Reference notes for the F12_04 livestock dashboard.

## Start here

For running the app locally see the [root README](../README.md).

| Doc | Covers | Read it when |
|---|---|---|
| [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md) | Repository tree, how a request moves, backend/frontend layout, where a change belongs | You are opening the repo for the first time |
| [ENV.md](ENV.md) | Every environment variable, where it lives, what to replace on handover | Setting up or taking over the project |

## Run & deploy

| Doc | Covers | Read it when |
|---|---|---|
| [VERCEL_DEPLOYMENT.md](VERCEL_DEPLOYMENT.md) | Deploying frontend + FastAPI to Vercel, env vars, cold starts, login troubleshooting | Shipping a new deployment |
| [SUPABASE_SETUP.md](SUPABASE_SETUP.md) | The hosted Postgres behind the app, connection string, RLS, backups, staying up | Working with the database or the DB is unreachable |
| [CRONJOB.md](CRONJOB.md) | The four cron-job.org jobs (keepalive, email, anomaly cache) and their routes | Setting up or debugging scheduled jobs |
| [PaidService.md](PaidService.md) | Why the live app needs Vercel Pro + Supabase Pro, and what each plan includes | Discussing hosting cost with a client |

## Feature internals

| Doc | Covers | Read it when |
|---|---|---|
| [AnomalyDetection.md](AnomalyDetection.md) | The three anomaly tests (CUSUM, peer slope, point score), severity and sensitivity, caching | Changing the Anomalies tab or its maths |
| [XGBoost.md](XGBoost.md) | The 182-day weight forecast: features, baseline, backtest, how trees run without the XGBoost package | Changing the Prediction tab or the model |
| [EMAIL_SYSTEM.md](EMAIL_SYSTEM.md) | Send Now, recurring schedules, the cron worker, providers, the `email_schedules` table | Changing Reports email behaviour |
