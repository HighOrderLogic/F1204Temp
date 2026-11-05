# Paid Vercel and Supabase

The live dashboard needs **Vercel Pro** and **Supabase Pro**. The free tiers are enough to try the app. They are not enough to leave it running for the farm.

Prices below are in US dollars from the public pricing pages in October 2026. They change. Check [Vercel pricing](https://vercel.com/pricing), [Vercel fair use](https://vercel.com/docs/limits/fair-use-guidelines), and [Supabase pricing](https://supabase.com/pricing) before telling a client a number.

Headline before extra usage: **$20 a month for Vercel** plus **$25 a month for Supabase**, about **$45 a month**. Usage past the included allowances is billed on top.

---

## Why this project cannot stay on free

| Service | Free tier blocks this project because | Paid plan |
|---|---|---|
| **Vercel** | Hobby is personal, non-commercial use only. This site is a working tool for a farm business. Vercel’s terms let them turn a Hobby deployment off. | **Pro**, from $20 a month. |
| **Supabase** | The free plan includes **5 GB** of data leaving the database each month (egress). This project has already used that up. Free projects also pause after about a week with no queries. | **Pro**, from $25 a month, with **250 GB** of egress included. |

Charts, login, and scheduled emails all read Postgres through the Vercel functions. If Supabase stops sending rows, the site can still load and then fail those calls. If Vercel shuts the Hobby deployment, nothing loads.

---

## Vercel: Hobby and Pro

Vercel hosts the static pages and the two Python functions (`api/light` and `api/index.py`). It does not store the animal records.

### Hobby ($0)

- For personal projects. [Fair use](https://vercel.com/docs/limits/fair-use-guidelines) says Hobby teams are **non-commercial personal use only**. Any commercial deployment needs Pro or Enterprise.
- Vercel’s terms say a Hobby project can be disabled or removed, including with no notice.
- Included usage is a cap. You cannot buy more. Past the cap, that feature waits until the next period. Typical Hobby allowances include about **100 GB** of fast data transfer and **1,000,000** function invocations a month.
- One daily cron is what this repo configures (`vercel.json`, `0 23 * * *` UTC on `/api/email/process-due`). More frequent email, anomaly warm, and the database ping are external jobs on cron-job.org. See `CRONJOB.md`.
- A commercial example in Vercel’s own list is being paid to create, update, or host the site, or running a deployment for someone’s financial gain.

### Pro (from $20 / month)

- The plan this farm site is allowed to run on.
- The $20 is the developer seat. Pro also includes a **$20 usage credit**. Past that credit, Vercel bills on demand. A quiet month can stay at $20. A heavy month of PDF reports or bandwidth costs more.
- Longer function runs, more crons, and a higher transfer allowance than Hobby. Heavy report generation is the feature most likely to need the longer limit. See `VERCEL_DEPLOYMENT.md`.
- Spend can be capped. Hobby cannot buy its way past a cap.

Enterprise is the tier above Pro (custom price, SLAs). This dashboard does not need it.

---

## Supabase: Free and Pro

Supabase is the Postgres database. The browser never talks to it. Vercel opens `DATABASE_URL` (transaction pooler, port **6543**) and reads or writes rows. Every chart, login, and email check moves those rows out of Supabase. That movement is **egress** (bandwidth).

Cached egress (files served from Supabase’s CDN) is a separate quota. This app’s main cost is uncached database egress.

### Free ($0)

| Item | Allowance |
|---|---|
| Uncached egress | **5 GB / month** |
| Cached egress | **5 GB / month** |
| Database size | **500 MB** per project |
| Projects | 2 active |
| Inactivity | Paused after about **7 days** with no queries |
| Backups | None on the free plan |
| Overage | Cannot pay for more. Over the quota, the project is notified and then restricted until the next cycle or an upgrade. |

The daily `GET /api/health/db` job exists so a free project does not pause. It does not raise the 5 GB cap. Once egress is spent, more queries can be blocked even if the keepalive still runs.

### Pro (from $25 / month)

| Item | Allowance |
|---|---|
| Uncached egress | **250 GB / month**, then **$0.09 / GB** |
| Cached egress | **250 GB / month**, then **$0.03 / GB** |
| Disk | **8 GB** per project, then $0.125 / GB |
| Inactivity | **Does not pause** |
| Backups | Daily, kept 7 days |
| Compute | $10 of compute credit, which covers one project on the Micro size. The $25 headline is that Micro project before extra egress or disk. |

A paid project can keep a spend cap. With the cap on, going past the quota restricts the project instead of billing forever. With the cap off, extra gigabytes appear on the invoice at the rates above.

The 5 GB free allowance is what this project already passed. Pro is the plan that raises it to 250 GB and stops the weekly pause.

---

## What each plan does to this app

| If we stay on | What users see |
|---|---|
| Vercel Hobby | The site can be removed because the farm use is commercial. |
| Supabase Free, egress spent | Login and charts fail even though the HTML may still load. The block lasts until next month or until the project is upgraded. |
| Supabase Free, idle for a week | The database pauses. The first requests fail until someone resumes it in the Supabase dashboard. The keepalive cron only helps while the project is still under the free rules. |
| Both Pro | The deployment is allowed for the business, and the database keeps serving after the free 5 GB. |

cron-job.org is a separate free timer. It is not part of the $45. It calls the Vercel URLs. It does not host the site or the database.

---

## What to check before changing a plan

1. Vercel → the project’s plan is **Pro**, not Hobby.
2. Supabase → the organisation plan is **Pro**, and the usage page shows egress against **250 GB**, not 5 GB.
3. `DATABASE_URL` in Vercel still uses the pooler on port **6543** after any plan change. A plan upgrade does not by itself rewrite that string.
4. `EMAIL_DRY_RUN` is `false` in production if scheduled mail should actually send. The plan does not change that flag.
5. After upgrading Supabase, confirm `GET /api/health/db?cron_secret=…` returns `{"status":"ok","database":"up"}`.

Do not commit the real `DATABASE_URL`, `CRON_SECRET`, or SMTP password while doing this. Names and where they live are in `ENV.md`.
