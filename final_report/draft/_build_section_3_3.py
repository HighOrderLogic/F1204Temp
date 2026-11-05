#!/usr/bin/env python3
"""Build Section 3.3 figures and a Google-Docs-ready .docx."""

from pathlib import Path

import matplotlib.pyplot as plt
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Mm, Pt, RGBColor

ROOT = Path("/Users/dang_tienthanh/Downloads/COMP3888/final_report")
FIG = ROOT / "figures"
OUT = ROOT / "draft" / "3.3_Product_Quality_Constraints.docx"

NAVY = "#1B3A4B"
STEEL = "#4E6E82"
SAND = "#A67C52"
RUST = "#8C4A3A"
INK = "#1F2933"
GRID = "#E6E2DA"


def _style_ax(ax):
    ax.set_facecolor("white")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#9AA4AE")
    ax.spines["bottom"].set_color("#9AA4AE")
    ax.tick_params(colors=INK, labelsize=8)
    ax.yaxis.grid(True, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def figure_latency():
    fig, axes = plt.subplots(1, 2, figsize=(7.15, 3.15), gridspec_kw={"width_ratios": [1, 1.35]})
    fig.patch.set_facecolor("white")

    ax = axes[0]
    labels = ["Active-herd\nmatrix switch", "Mean warm\ncomponent render"]
    vals = [48, 198]
    bars = ax.bar(labels, vals, color=[STEEL, NAVY], width=0.62, zorder=3)
    ax.axhline(200, color=SAND, ls="--", lw=1.0, label="Filter-render ceiling, 200 ms")
    ax.axhline(500, color=RUST, ls="--", lw=1.0, label="Multi-measure ceiling, 500 ms")
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 14, f"{v} ms", ha="center", va="bottom", fontsize=8, color=INK)
    ax.set_ylim(0, 620)
    ax.set_ylabel("Milliseconds", fontsize=8, color=INK)
    ax.set_title("Warm interactive telemetry", fontsize=10, color=NAVY, pad=8, loc="left")
    ax.legend(frameon=False, fontsize=7, loc="upper left")
    _style_ax(ax)

    ax = axes[1]
    labels = [
        "R, sensitivity change (cached)",
        "R, cold score, finalpweight",
        "R, cold score, weight or pweight",
        "Python cold recompute (avoided)",
    ]
    vals = [100, 300, 900, 4000]
    colors = [STEEL, STEEL, NAVY, SAND]
    y = list(range(len(labels)))
    ax.barh(y, vals, color=colors, height=0.62, zorder=3)
    ax.axvline(500, color=RUST, ls="--", lw=1.0, label="Interactive ceiling, 500 ms")
    for i, v in enumerate(vals):
        label = "3–5 s" if i == 3 else (f"{v} ms")
        ax.text(v + 80, i, label, va="center", ha="left", fontsize=8, color=INK)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=7.5)
    ax.set_xlim(0, 6200)
    ax.set_xlabel("Milliseconds", fontsize=8, color=INK)
    ax.set_title("Time to an anomaly result (FarmA)", fontsize=10, color=NAVY, pad=8, loc="left")
    ax.legend(frameon=False, fontsize=7, loc="lower right")
    _style_ax(ax)
    ax.xaxis.grid(True, color=GRID, linewidth=0.6)
    ax.yaxis.grid(False)

    fig.tight_layout(w_pad=1.6)
    path = FIG / "fig_3_2_latency.png"
    fig.savefig(path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close()
    return path


def figure_forecast():
    fig, axes = plt.subplots(1, 2, figsize=(7.15, 2.95), gridspec_kw={"width_ratios": [1.35, 1]})
    fig.patch.set_facecolor("white")

    import numpy as np

    methods = ["Naive\ncarry-forward", "Capped\nbaseline", "Shipped model\n(fade on)", "Same model\nfade removed"]
    short = {
        "5–20 days\n(steps 1–4)": [9.88, 6.63, 4.89, 5.10],
        "20 days\n(one step)": [11.58, 7.42, 5.62, 6.13],
    }
    colors = [SAND, STEEL, NAVY, RUST]
    ax = axes[0]
    x = np.arange(len(methods))
    width = 0.36
    for i, (name, series) in enumerate(short.items()):
        offset = (i - 0.5) * width
        bars = ax.bar(x + offset, series, width, label=name.replace("\n", " "), color=[NAVY, STEEL][i], zorder=3)
        for b, v in zip(bars, series):
            ax.text(b.get_x() + b.get_width() / 2, v + 0.15, f"{v:.2f}", ha="center", va="bottom", fontsize=6.5, color=INK)
    ax.set_xticks(x)
    ax.set_xticklabels(methods, fontsize=7.5)
    ax.set_ylim(0, 15)
    ax.set_ylabel("MAE (kg)", fontsize=8)
    ax.set_title("Inside the tested window", fontsize=10, color=NAVY, pad=8, loc="left")
    ax.legend(frameon=False, fontsize=7)
    _style_ax(ax)

    ax = axes[1]
    long_labels = ["Naive", "Fade\nremoved", "Capped\nbaseline", "Shipped\nmodel"]
    long_vals = [54.90, 39.94, 13.31, 12.81]
    bars = ax.bar(long_labels, long_vals, color=[SAND, RUST, STEEL, NAVY], width=0.68, zorder=3)
    for b, v in zip(bars, long_vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.8, f"{v:.1f}", ha="center", va="bottom", fontsize=7.5, color=INK)
    ax.set_ylim(0, 68)
    ax.set_ylabel("MAE (kg)", fontsize=8)
    ax.set_title("58 days ahead", fontsize=10, color=NAVY, pad=8, loc="left")
    _style_ax(ax)

    fig.tight_layout(w_pad=1.4)
    path = FIG / "fig_3_3_forecast_mae.png"
    fig.savefig(path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close()
    return path


def figure_anomaly():
    import numpy as np

    fig, ax = plt.subplots(figsize=(7.15, 3.05))
    fig.patch.set_facecolor("white")
    events = [
        "Background\n(no event)",
        "Single-day\n4σ dip",
        "Growth stall\n(−0.8 kg/day)",
        "Loss vs herd\n(1.6 kg/day)",
        "Persistent\n2σ drop",
        "Persistent\n3σ drop",
    ]
    high = [22, 26, 43, 60, 63, 80]
    med = [14, 19, 34, 52, 56, 71]
    low = [7, 11, 16, 33, 39, 60]
    x = np.arange(len(events))
    w = 0.24
    b1 = ax.bar(x - w, high, w, label="High sensitivity (α = 5%)", color=RUST, zorder=3)
    b2 = ax.bar(x, med, w, label="Medium sensitivity (α = 2%)", color=NAVY, zorder=3)
    b3 = ax.bar(x + w, low, w, label="Low sensitivity (α = 0.5%)", color=STEEL, zorder=3)
    ax.set_xticks(x)
    ax.set_xticklabels(events, fontsize=7.5)
    ax.set_ylabel("Share flagged at severity High (%)", fontsize=8)
    ax.set_ylim(0, 100)
    ax.set_title("Injected events on 60 FarmA animals, R engine", fontsize=10, color=NAVY, pad=6, loc="left")
    ax.legend(frameon=False, fontsize=7.5, ncol=3, loc="upper center", bbox_to_anchor=(0.48, -0.18))
    ax.set_ylim(0, 100)
    _style_ax(ax)
    fig.tight_layout()
    fig.subplots_adjust(bottom=0.28)
    path = FIG / "fig_3_4_anomaly_detection.png"
    fig.savefig(path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close()
    return path


def shade(cell, hex_color):
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), hex_color)
    shd.set(qn("w:val"), "clear")
    tcPr.append(shd)


def set_run_font(run, name="Times New Roman", size=11, bold=False, italic=False, color=None):
    run.font.name = name
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)
    run.font.size = Pt(size)
    run.bold = bold
    run.italic = italic
    if color:
        run.font.color.rgb = RGBColor(*color)


def add_text(p, text, **kwargs):
    r = p.add_run(text)
    set_run_font(r, **kwargs)
    return r


def body(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(6)
    p.paragraph_format.line_spacing = 1.08
    p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    add_text(p, text, size=11)
    return p


def heading(doc, text, level):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(10 if level == 2 else 2)
    p.paragraph_format.space_after = Pt(4)
    p.paragraph_format.line_spacing = 1.0
    p.paragraph_format.keep_with_next = True
    add_text(p, text, size=13 if level == 1 else 12, bold=True, color=(27, 58, 75))
    return p


def caption(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(8)
    p.paragraph_format.line_spacing = 1.0
    add_text(p, text, size=9, italic=True)
    return p


def equation(doc, text):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(2)
    add_text(p, text, size=11, italic=True)
    return p


def add_table(doc, headers, rows, widths):
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.autofit = False
    table.allow_autofit = False
    tbl = table._tbl
    tblPr = tbl.tblPr if tbl.tblPr is not None else OxmlElement("w:tblPr")
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "4")
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), "C5CDD4")
        borders.append(el)
    tblPr.append(borders)

    for i, h in enumerate(headers):
        cell = table.rows[0].cells[i]
        cell.text = ""
        p = cell.paragraphs[0]
        p.paragraph_format.space_before = Pt(1)
        p.paragraph_format.space_after = Pt(1)
        add_text(p, h, size=8, bold=True, color=(255, 255, 255))
        shade(cell, "1B3A4B")
    for r_i, row in enumerate(rows):
        for c_i, val in enumerate(row):
            cell = table.rows[r_i + 1].cells[c_i]
            cell.text = ""
            p = cell.paragraphs[0]
            p.paragraph_format.space_before = Pt(1)
            p.paragraph_format.space_after = Pt(1)
            add_text(p, val, size=8)
            if r_i % 2 == 1:
                shade(cell, "F4F7F8")
    for row in table.rows:
        for i, w in enumerate(widths):
            row.cells[i].width = Cm(w)
    # set tbl width
    tblW = OxmlElement("w:tblW")
    tblW.set(qn("w:w"), str(int(sum(widths) * 567)))
    tblW.set(qn("w:type"), "dxa")
    tblPr.append(tblW)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return table


def build():
    FIG.mkdir(parents=True, exist_ok=True)
    p2 = figure_latency()
    p3 = figure_forecast()
    p4 = figure_anomaly()

    doc = Document()
    sec = doc.sections[0]
    sec.page_width = Mm(210)
    sec.page_height = Mm(297)
    sec.left_margin = Cm(1.9)
    sec.right_margin = Cm(1.9)
    sec.top_margin = Cm(1.7)
    sec.bottom_margin = Cm(1.7)
    normal = doc.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(11)
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")

    heading(doc, "3.3 Product Quality Constraints & Non-Trivial Algorithmic Validation", 1)
    body(
        doc,
        "Product quality is specified here as measurable constraints on four ISO/IEC 25010 characteristics: performance efficiency, security, reliability, and functional correctness of the analytical functions [4]. The constraints apply to both shipped platforms, the R/Shiny application (COMP3988_F12_04_P34) and the Python/FastAPI application (F12_04). Section 3.2 fixed the oracles. This section states what those oracles are protecting, the mechanism on each stack, and the validation of the two procedures whose output on a noisy herd cannot be written down as a single expected row: the 28-day anomaly engine, and the 182-day weight forecast. Figures reuse the Week 12 interactive measurements cited in Section 3.4, the FarmA evaluation recorded for the R engine, and the shipped model card ml/models/model_meta.json (xgb-recursive-v2, trained 20 October 2026 on 376 animals).",
    )

    heading(doc, "3.3.1 Performance & Scalability Quality", 2)
    body(
        doc,
        "The interactive contract is common to both stacks. A warm filter render completes within 200 ms, and a multi-measure query completes within 500 ms. Warm Playwright telemetry met that contract: mean component render 198 ms, active-herd matrix switch 48 ms (Section 3.4). The mechanism differs because the hosts differ. shinyapps.io keeps one R process resident. Vercel Hobby freezes a Python function after the response and cuts execution at about 15 seconds, so any work that can miss 500 ms has to be finished before the click.",
    )
    body(
        doc,
        "On the Python stack, summary, time-series, and distribution aggregates are computed inside PostgreSQL or DuckDB by sql_agg.py. Headcount is COUNT(DISTINCT eid) before any median, so an animal that crosses the scale several times cannot dominate the herd statistic (US09). A column name reaches an f-string only after it matches SAFE_COLUMN_NAME. Routes on the interactive budget — authentication, filters, summary, time series, distribution, and the anomaly snapshot read — are built into the light function api/light/index.py and deployed in region syd1 (vercel.json). Upload finalisation, forecast refresh, and anomaly recomputation stay on api/index.py. The browser collapses a burst of filter edits into one request after 120 ms (filters.js) and aborts the previous request with AbortController on the cohorts, distributions, time-series, and anomaly pages. Choice lists for a farm are kept in farmBootCache, so a switch between KF and WOW does not repeat the bootstrap round trip.",
    )
    body(
        doc,
        "On the R stack the same collapse is a Shiny debounce: 1,000 ms on the sidebar filters (filter.R) and 500 ms on the measure selector (server.R). Each farm is its own DuckDB file. The anomaly loader reads only eid, date, and the chosen weight column for the 28-day window. Scoring a new FarmA selection takes about 0.9 s on weight or pweight and about 0.3 s on finalpweight, which skips CUSUM calibration because that series has no day-to-day noise. Changing sensitivity reuses the cached scores and finishes in under 0.1 s. The process cache holds 20 combinations of weight column, period, and selection (ANOMALY_CACHE_SIZE). Forecasts are written by admin_scripts/forecasting.R when data are loaded. The Shiny process reads the stored table and does not load XGBoost.",
    )
    body(
        doc,
        "The Python anomaly path cannot spend 0.9 s inside a cold function. An uncached analyse takes 3–5 s, and the first scoring of both KF and WOW takes about 20–30 s (docs/CRONJOB.md). That cost is taken off the click. Every five minutes, GET /api/anomalies/warm?preload=1&compute=1 writes anomaly_snapshots, and the tab reads GET /api/anomalies/snapshot on the light function. An in-process LRU of 20 score objects covers selections the snapshot does not store. An upload clears the LRU, so a snapshot from the previous file is not served as current. Figure 3.2 places the measured times against the 500 ms line: the warm UI sits under both ceilings, the cached R sensitivity change sits under the line, and the Python cold recompute sits an order of magnitude above it, which is why the snapshot exists.",
    )

    doc.add_picture(str(p2), width=Cm(16.4))
    last = doc.paragraphs[-1]
    last.alignment = WD_ALIGN_PARAGRAPH.CENTER
    last.paragraph_format.space_before = Pt(4)
    last.paragraph_format.space_after = Pt(0)
    caption(
        doc,
        "Figure 3.2. Interactive latency against the product ceilings. Left: warm Playwright telemetry (Section 3.4). Right: FarmA anomaly cost. R timings are from the engine evaluation (cold score about 0.9 s on weight, 0.3 s on finalpweight, sensitivity change under 0.1 s). The Python bar is the documented 3–5 s uncached serverless recompute, which the five-minute snapshot refresh is there to avoid.",
    )

    body(
        doc,
        "Scale is bounded on purpose, and the bound is the corpus in Section 3.5: Killara Feedlot (KF, 34,294 records) and FarmA (12,117 records; 368 animals in the anomaly window). Inside that corpus the controls are distinct-animal aggregation, forecasts stored as 182 daily rows per animal rather than trained on request, and chunked uploads of 4 MB parts with a 50 MB ceiling so a file never arrives as one body over the 4.5 MB Vercel gateway (DEF-01). Volumetric stress tests against the free tier were excluded because they measure the host quota rather than the query plan (Section 3.1). Table 3.5 is the control register.",
    )
    caption(doc, "Table 3.5. Performance and scalability controls on the two stacks.")
    add_table(
        doc,
        ["Constraint", "Python / FastAPI", "R / Shiny", "Bound held"],
        [
            [
                "Multi-measure query",
                "SQL pushdown in sql_agg.py, including COUNT(DISTINCT eid); light function",
                "Per-farm DuckDB; filters debounced 1,000 ms",
                "≤ 500 ms warm; observed mean render 198 ms",
            ],
            [
                "Filter burst",
                "120 ms debounce; AbortController cancels the in-flight fetch",
                "1,000 ms on filters; 500 ms on the measure selector",
                "One query cycle per burst of edits",
            ],
            [
                "Anomaly first paint",
                "Snapshot read on the light route; warm job every 5 min; LRU of 20",
                "Process cache of 20 keys, shared across sessions",
                "R cold 0.9 s; sensitivity < 0.1 s; Python click avoids the 3–5 s recompute",
            ],
            [
                "Forecast page",
                "Stored rows. Trees are walked in NumPy only when a refresh runs",
                "Stored rows. XGBoost loads only inside the admin script",
                "The page does not train, and it does not refit on each session",
            ],
            [
                "Ingestion payload",
                "4 MB chunks, 50 MB ceiling, out-of-order reassembly",
                "One DuckDB file per farm, loaded by the admin scripts",
                "No single HTTP body over the 4.5 MB gateway",
            ],
        ],
        [3.0, 4.6, 4.5, 4.3],
    )

    heading(doc, "3.3.2 Security, Privacy & Data Integrity Quality", 2)
    body(
        doc,
        "The privacy constraint is unconditional. A station hand receives no plaintext RFID. An owner receives no other farm’s animals. A missing masking key shuts the service rather than falling back to a key that is already in the git history. Both stacks use the same three roles and different cryptography. Table 3.6 is the register; the paragraphs fix the parameters.",
    )
    body(
        doc,
        "Authentication. Python issues an HS256 JWT (auth/dependencies.py) with lifetime jwt_expiry_hours = 24, which is 86,400 s. requireAuth() reads the expiry before any further call, so a lapsed session does not produce a storm of HTTP 401 responses (DEF-02). Passwords are stored with scrypt (auth/passwords.py). A password shorter than 8 characters is rejected in 41 ms with HTTP 400. R authenticates through shinymanager against the same scrypt hashes in the credentials DuckDB (scrypt::hashPassword). The Shiny session lasts 8 × 60 minutes, 28,800 s (global.R). A forged signature, a missing header, or a farm-mismatched session is rejected (US01, US05).",
    )
    body(
        doc,
        "Authorisation and tenancy. Roles are admin, owner, and analyst (the Python login label is user). Raw EIDs are granted only by role_can_view_eids in R and by user.can_view_eid in Python, and the same predicate gates export. After DEF-04, anomaly rows are masked on that predicate rather than on an is_admin flag. Tenancy fails closed. Every Python analytical query carries farm_id = %s, and a cross-tenant parameter returns HTTP 403 (test_tenancy_guard.py). An account whose user_farms list is empty is denied. R runs one application instance per farm: user_belongs_to_farm demands an exact, case-sensitive match, and an empty user_farms vector ends the login (DEF-06, TC-R-TENANT-01). Admins may move between KF and WOW. Owners stay on the assigned property.",
    )
    body(
        doc,
        "Pseudonymisation. For an analyst, Python computes HMAC-SHA256 of the EID under EID_PSEUDONYM_KEY and publishes the first 8 hex characters, ID-XXXXXXXX (32 bits). The sentinels Overall, All, and No Treatment pass through unchanged. The mask is not reversible. When an analyst picks an animal, resolve_animal_id matches the mask against that farm’s known EIDs and returns no row if the client submits a raw tag, so a guessed RFID cannot be used as a query key. A collision of two animals into the same 32-bit prefix raises PseudonymCollisionError, and the message names the mask only. key_guard.py answers HTTP 503 with code EID_PSEUDONYM_KEY_MISSING when the variable is unset or still equal to the retired public string comp3988-default-secret-key. R masks with CRC32 truncated to 6 hex characters, ID-XXXXXX (24 bits), in mask_eid (server.R). The shorter mask fits a deployment in which each process holds one farm and the analyst is not offered an EID filter. Both constructions meet the metamorphic checks in Section 3.2.3: the same EID and key always yield the same mask, a second key yields a different mask, and sentinels are unchanged (US02).",
    )
    body(
        doc,
        "Integrity of the stored weights. An upload is accepted when the file name begins with the target farm id and the file contains Australian day-month-year dates plus at least one numeric weight column (US06). Python upserts on the composite key (farm_id, eid, date) in skip or overwrite mode, and it writes a pre-mutation snapshot capped at MAX_SNAPSHOTS_PER_FARM = 3 (farm_backup.py). R writes a dated copy <farm_id>_backup_YYYYMMDD.duckdb before a destructive admin load, and revert_farm.R restores it (TC-R-CLI-06). Farm-specific columns are discovered from the upload, which is the repair for DEF-05, and only names that match the safe-column pattern are interpolated. Responses carry X-Content-Type-Options: nosniff and X-Frame-Options: DENY. Cron routes that send mail or recompute anomalies require CRON_SECRET and return HTTP 401 otherwise. The light-function health ping is unauthenticated and does not open the database.",
    )
    caption(doc, "Table 3.6. Security, privacy, and data-integrity controls.")
    add_table(
        doc,
        ["Constraint", "Python control", "R control", "Failure behaviour"],
        [
            [
                "Session",
                "HS256 JWT, 86,400 s",
                "shinymanager session, 28,800 s",
                "HTTP 401; client drops the token before further calls",
            ],
            [
                "Password storage",
                "scrypt via passlib; length ≥ 8",
                "scrypt::hashPassword in the credentials DuckDB",
                "Shorter passwords rejected in 41 ms, HTTP 400",
            ],
            [
                "Analyst EID",
                "HMAC-SHA256, 8 hex digits, keyed",
                "CRC32, 6 hex digits, one farm per process",
                "Sentinels pass through; raw tags from an analyst do not resolve",
            ],
            [
                "Key absent",
                "HTTP 503 from key_guard.py, including the retired public default",
                "Export and EID filter gated by role_can_view_eids",
                "Service refuses masked traffic it cannot key",
            ],
            [
                "Tenancy",
                "farm_id predicate on every analytical query",
                "user_belongs_to_farm; empty user_farms denied",
                "HTTP 403, or login terminated (DEF-06)",
            ],
            [
                "Weight rows",
                "Upsert on (farm_id, eid, date); 3 snapshots kept",
                "Dated DuckDB backup before a destructive load",
                "Bad file rejected; previous snapshot remains restorable",
            ],
        ],
        [2.6, 4.8, 4.6, 4.4],
    )

    heading(doc, "3.3.3 Reliability & Availability Quality", 2)
    body(
        doc,
        "Both deployments sit on free tiers with hard quotas: Vercel Hobby allows one platform cron per day and a short function life, and shinyapps.io allows 25 active hours a month. Recurring work is therefore scheduled so that a cold process still has a defined recovery, and a repeated tick does not repeat a side effect.",
    )
    body(
        doc,
        "Python keeps four jobs. Three of them run every five minutes through cron-job.org, because the cron in vercel.json can fire only once a day (0 23 * * * on /api/email/process-due). GET /api/health touches the light function only and does not open Postgres, which keeps login and filters warm between demonstrations. GET /api/health/db runs SELECT 1 once a day so the Supabase project is not paused after about seven days without traffic. GET /api/email/process-due sends reports that are due. The schedule moves from pending to sent and stores last_sent; a later sweep in the same period finds the row already sent. The same transition is tested on the R scheduler (test-background-email-scheduler.R). SMTP uses a 45-second socket timeout. Regression suites run with a dry-run flag so they do not consume the Gmail quota of about 500 messages a day or the Grist quota of 5,000 calls a day. GET /api/anomalies/warm?preload=1&compute=1 refreshes anomaly_snapshots for KF and WOW, which is the availability counterpart of the latency control in Section 3.3.1.",
    )
    body(
        doc,
        "A forecast refresh replaces every stored row for that farm (forecast_service.refresh_farm), so two model versions cannot share the table. A failed DuckDB statement cannot leave the shared connection unusable: the cursor wrapper calls reset_active_engine(), and the next request opens a new connection (DEF-03). Deleting a farm requires an explicit force=true confirmation, which is what keeps the baseline tenant KF from disappearing on a mis-click. Chunk reassembly accepts parts out of order and rejects a payload over 50 MB, so a dropped chunk fails the upload instead of committing a partial herd.",
    )
    body(
        doc,
        "R reliability follows the single-writer contract of DuckDB. Admin scripts and the running app are not opened together on the same file. A destructive load takes the dated backup first. deploy code retries a timeout rather than recording a farm as published after a dropped connection (farm_utils.R). Because each farm is a separate application instance, a fault in one file stays in that file. An empty filter intersection renders an empty-state card and a clear-filters action. Section 3.4 records that every participant who reached that state returned to a populated view in under 15 seconds, which is the user-visible form of the same constraint.",
    )
    body(
        doc,
        "The residual availability risk is the host. A five-minute cron does not cover a multi-hour database pause if the daily ping is removed, and the shinyapps.io hour cap is a billing quota. Neither stack claims a second availability zone. The claim the suites support is narrower: a cold process, a repeated cron tick, a failed statement, and a rejected upload each end in a state the regression tests already execute.",
    )

    heading(doc, "3.3.4 Innovation & Non-Trivial Algorithms", 2)
    body(
        doc,
        "Two procedures are non-trivial in the sense this section uses. Their correct output on a noisy herd cannot be precomputed by hand, a simpler rule was tried and failed, and both platforms run them as product behaviour. Cohort tails (US12–US13) are the smaller third case: high and low sets are disjoint, and each tail has size ceil(n · p / 100). That invariant is already the oracle in Section 3.2.3. The validation below is for the anomaly engine and the forecast.",
    )
    body(
        doc,
        "The anomaly engine watches the latest 28 days, four whole weeks ending on the last weighing in the selected period, and asks three questions. Has the animal stayed light for many monitored days, after the herd’s shared day has been removed? That is the CUSUM, and it is the only route to severity High. Is the animal’s growth over the month low against its peers? That is severity Medium. Did one reading fall in the extreme lower tail of day-to-day noise? That is severity Low. Sensitivity High, Medium, or Low is a separate control, defaulting to Medium. It chooses how often noise alone may flag a healthy animal. It does not rename the severity.",
    )
    body(
        doc,
        "The scale of the noise is why a textbook chart was rejected. On FarmA a heifer moves about ±7 to ±11 kg in a day while the herd gains about 0.8 kg/day, and 6.5% of residuals lie beyond 3σ, against 0.27% under a normal model. σ is 1.4826 times the MAD of local residuals. Each residual is the herd-adjusted weight minus that animal’s median in a window of ±3 days, and the window must contain at least four readings. σ is withheld below 200 residuals, and the point test and the CUSUM then do not run. The constant only sets the unit. Thresholds are taken from the upload, not from a normal table.",
    )
    body(
        doc,
        "A point flag uses the animal’s lowest residual in units of σ, and only when that residual sits below an empirical quantile of every residual in the reference set: 0.5%, 0.1%, or 0.05% as sensitivity moves from High to Low. A surprisingly heavy day does not flag. On FarmA at Medium the cut is about −7.9σ, near −54 kg. A normal cut at −3.5σ would have marked about 4.4% of readings, on the order of 370 flags in a month, which is the result that removed it.",
    )
    body(
        doc,
        "Peer growth fits an ordinary least-squares slope of adjusted weight on the day index, for an animal with at least 10 readings spanning at least 21 days. The robust score is z = (β − median) / (1.4826 × MAD of the peer slopes), flagged below −2.0, −2.5, or −3.0. Python takes the finest group among breed, sex, mob, and treatment that still holds at least 10 eligible animals, and otherwise uses the reference set. The R page compares the animal with the selected reference set as a whole; a grouped fallback exists in score_peer_growth and is not the call the page makes. The sentence on the page quotes the raw slope, because that is the number a reader can check against a weigh sheet. The adjusted slope is the one under test.",
    )
    body(
        doc,
        "The CUSUM is a one-sided Page chart [5]. Weeks 1–2 form the baseline mean B of adjusted weight, provided there are at least 7 readings. Weeks 3–4 are monitored, provided there are at least 3. On each monitored reading the standardised value is clipped, and the chart accumulates only the shortfall past an allowance:",
    )
    equation(doc, "u = clip( (A − B) / σ ,  −2 ,  2 )")
    equation(doc, "S = max( 0 ,  S_previous − u − k )    with k = 0.5")
    body(
        doc,
        "The allowance k = 0.5σ is the smallest sustained shift the chart is built to accumulate [6]. The clip at ±2σ means one day adds at most 1.5, so a High flag requires a run of low days [7]. The decision limit h is the (1 − α) quantile of peak scores after each animal’s adjusted weights are shuffled across its own days, 20 times, seed 3988, with α in {0.05, 0.02, 0.005}. The shuffle keeps the multiset of weights and destroys their order, so an animal that is merely light for the whole month does not look special, and σ is not refitted inside the shuffle. On simulated steady growers plus resampled FarmA noise, the High rate matched α: 4.9%, 1.8%, and 0.4%.",
    )
    body(
        doc,
        "Shared movement is removed before any of the three tests. FarmA moved together by up to 21.4 kg beyond ordinary growth around 6 September, which is a ration, water, weather, or scale effect, and it would otherwise flag the whole herd. R removes it with a day-first median polish of three passes (adjust_herd in anomaly_detection.R). Each pass takes the daily median of weight minus the animal offset, then refreshes the offset, and a day with fewer than 10 animals is left unadjusted. Starting from the day removes a common shift in full. Centring each animal on its own median first removed only about 88% of a herd trend and, on a herd losing 1.5 kg/day, added 20 High flags on FarmA. Python’s adjust_herd (anomaly_service.py) is that one-step estimator: the day’s median of weight minus the animal’s own window median, again only when at least 10 animals are present. The recurrence, the constants, the sensitivity table, and the severity order are the same function on both stacks. The herd-effect estimator is not, and the forecast differential test was not applied to h. What both stacks do lock is the herd-drift invariance MR_1 from Section 3.2.3. In R, a herd-wide drift of −2 or +1.5 kg/day, with 15% of weighings missing, leaves per-animal severity unchanged. In Python, a shared −25 kg on one day is absorbed into herd_effect and does not become an animal flag.",
    )
    body(
        doc,
        "The rules that were retired are part of the evidence. A percentage gap between consecutive readings flagged nobody at a 20% cut and 288 of 368 animals at a 5% cut, and it caught none of the injected stalls. A textbook h of 5 flagged about 40% of animals with nothing wrong, which is the heavy tail showing up as false Highs. The latest 7-day change remains a sort key and is not a fourth test. Figure 3.4 is the injection campaign that replaced those rules, run on the R engine: 60 real FarmA animals, the event applied from day 15, five repeats, on weight. At Medium sensitivity a persistent drop of about 20 kg (3σ) is severity High for 71% of animals, a loss of 1.6 kg/day versus the herd for 52%, and a growth stall for 34%, against a 14% background. A single-day dip of 4σ flags 19%, in line with that background, which is the property the clip and the permutation limit were built to obtain. On the unperturbed FarmA month (25 August to 21 September 2026, 368 animals, Medium, weight) the page returns 56 High, 0 Medium, 8 Low, 265 Normal, and 39 with too little data. Medium is almost empty because the slowest growers are already High, and the explanation lists both reasons. finalpweight has σ of 0, so only the peer-growth check runs. pweight is smoothed and autocorrelated, and at Medium it raises 134 High flags against 56 on weight; the page states that the sensitivity no longer means what it says on that column.",
    )

    doc.add_picture(str(p4), width=Cm(16.4))
    last = doc.paragraphs[-1]
    last.alignment = WD_ALIGN_PARAGRAPH.CENTER
    last.paragraph_format.space_before = Pt(2)
    last.paragraph_format.space_after = Pt(0)
    caption(
        doc,
        "Figure 3.4. Share of animals flagged at severity High by the R engine (src/anomaly_detection.R). Sixty FarmA animals, events injected from day 15, five repeats, weight. The single-day dip tracks the background. Sustained drops pull away from it, and a stricter sensitivity lowers every bar. Python implements the same CUSUM recurrence, cap, allowance, permutation seed, and severity order; this injection campaign was scored on the R estimator, whose herd adjustment is the three-pass median polish.",
    )

    body(
        doc,
        "The forecast answers a different question: where finalpweight will sit over the next 182 days, starting the day after the animal’s last record. One model serves every farm, and the request path does not train. The shipped booster is xgb-recursive-v2, 633 trees of depth 4, learning rate 0.05, minimum child weight 20, L2 penalty 5, objective absolute error, fit on 376 animals from 16 June to 21 September 2026 [8]. Each day updates the weight by the farm’s typical gain at the current weight, plus a fading excess from the trees:",
    )
    equation(doc, "w(t+1) = w(t) + clip( b(w(t)) + s(t) · e(t) )")
    body(
        doc,
        "b(w) is recomputed on the farm being forecast, not frozen from training, so a model fit on one property can be applied to another whose cattle gain at a different rate. Consecutive-day changes are split into 50 kg bands. A band with fewer than 30 changes is dropped and filled from its neighbours. The band value is the median change, and it is capped at the farm-wide median, so a heavy animal is given a slower gain than the farm and never a faster one. The clip holds the daily step inside that farm’s 1st to 99th percentile of observed day-to-day changes, or inside (−3, +3) kg/day when the farm has no consecutive-day pairs. e(t) is the predicted excess over b, from a 14-day window: latest weight, how many of the 14 days are present, the last seven excesses, means over 3, 7, and 13 steps, and the standard deviation of the raw changes over 7 and 13. A missing day stays missing and follows the tree’s default branch. The fade is s(t) = max(0, 1 − s / 20). Day 1 uses the full excess, day 10 uses half, and from day 21 the path is the farm curve alone. The page stores 20 as validated_days from the model card, and it draws that portion apart from the later curve.",
    )
    body(
        doc,
        "The fade is the result that justifies the procedure. On the 58-day backtest, 373 animals and 20,551 points, the capped model with the fade has MAE 12.81 kg. The same model with the fade removed has MAE 39.94 kg. The capped baseline alone is 13.31 kg, and carrying the last observed daily change forward is 54.90 kg. Inside the window the trees still earn their place. Over steps 1–4 at 5–20 days, 344 animals and 16,142 points, MAE is 4.89 kg against 6.63 kg for the capped baseline and 9.88 kg for the naive path. A single step at 20 days is 5.62 kg, 7.42 kg, and 11.58 kg. Figure 3.3 plots the four methods. Past day 20 a six-month chart is mostly the farm’s typical gain. That limit sits beside the figure: 182 days are stored so the curve is continuous, and the checked model output is the first 20.",
    )

    doc.add_picture(str(p3), width=Cm(16.4))
    last = doc.paragraphs[-1]
    last.alignment = WD_ALIGN_PARAGRAPH.CENTER
    last.paragraph_format.space_before = Pt(2)
    last.paragraph_format.space_after = Pt(0)
    caption(
        doc,
        "Figure 3.3. Backtest MAE of the shipped forecast (model card xgb-recursive-v2; 14-day window, capped baseline). Left: the horizon the fade still covers. Right: 58 days, where removing the fade (39.9 kg) is close to the naive path (54.9 kg) and the faded model (12.8 kg) sits with the farm baseline (13.3 kg). Source: ml/models/model_meta.json.",
    )

    body(
        doc,
        "Cross-stack parity is gated where the kilograms must match. admin_scripts/forecasting.R is a port of ml/forecasting.py, and test-forecasting.R requires max |ŷ_R − ŷ_Py| < 0.01 kg against the Python fixtures, with the baseline gains and the clip agreeing to 1e−9. The Python request path does not import XGBoost. ml/tree_model.py walks the exported trees in NumPy, and test_tree_model.py checks that walk against XGBoost 2.1.4 on the committed booster. The R application never walks trees. Both Prediction pages therefore display a stored forecast, and an edit to either port fails the differential test when the weights diverge [2].",
    )
    body(
        doc,
        "The four constraints close on evidence that Section 3.4 then accepts story by story. Warm interactive work stays inside 200 ms and 500 ms because aggregation, debounce, and precomputation sit in front of the click, and the anomaly cost that cannot meet 500 ms is served from a snapshot. Analyst identifiers leave the server only as a keyed mask, and a missing key is HTTP 503. A repeated schedule tick, a failed statement, and a rejected upload each have a recovery the suites already run. The CUSUM flags a run of low days and leaves a single spike at the background rate, and within the faded window the forecast is ahead of both the naive path and the farm baseline, with the two implementations held to a hundredth of a kilogram.",
    )

    heading(doc, "References cited in Section 3.3", 2)
    for ref in [
        "[2] McKeeman, W. M. (1998). Differential testing for software. Digital Technical Journal, 10(1), 100–107. (Already listed in the report.)",
        "[4] ISO/IEC 25010:2011. Systems and software engineering — Systems and software Quality Requirements and Evaluation (SQuaRE) — System and software quality models. International Organization for Standardization.",
        "[5] Page, E. S. (1954). Continuous inspection schemes. Biometrika, 41(1/2), 100–115.",
        "[6] Montgomery, D. C. (2019). Introduction to Statistical Quality Control (8th ed.). Wiley.",
        "[7] Huber, P. J. (1964). Robust estimation of a location parameter. Annals of Mathematical Statistics, 35(1), 73–101.",
        "[8] Chen, T., & Guestrin, C. (2016). XGBoost: A scalable tree boosting system. In Proceedings of the 22nd ACM SIGKDD International Conference on Knowledge Discovery and Data Mining, pp. 785–794.",
    ]:
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(2)
        p.paragraph_format.line_spacing = 1.05
        p.paragraph_format.left_indent = Cm(0.75)
        p.paragraph_format.first_line_indent = Cm(-0.75)
        add_text(p, ref, size=10)

    doc.save(OUT)
    print("wrote", OUT)
    print("figures", p2, p3, p4)


if __name__ == "__main__":
    build()
