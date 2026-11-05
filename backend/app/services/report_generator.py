import base64
import io
import os
import textwrap
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.platypus import Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.config import get_settings
from app.models.schemas import FilterState
from app.services.chart_service import CohortService, DistributionService, TimeseriesService
from app.services.email_service import generate_filter_summary
from app.services.labels import (
    clean_group_label,
    extract_measure_label_and_unit,
    format_measure_display,
)
from app.services.summary_service import SummaryService


def _render_logo_badge_png(text: str) -> bytes:
    """Render a crisp 300 DPI pine brand badge for PDF headers."""
    fig, ax = plt.subplots(figsize=(2.0, 0.48), layout="constrained")
    fig.patch.set_facecolor("#1B4332")
    ax.set_facecolor("#1B4332")
    ax.axis("off")
    ax.text(
        0.5,
        0.5,
        text,
        ha="center",
        va="center",
        fontsize=9.5,
        fontweight="bold",
        color="white",
        fontfamily="sans-serif",
    )
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=300, facecolor="#1B4332")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _render_logo_cards_composite_png(
    logo_items: list[Path | tuple[str, bytes]], target_h_px: int = 100
) -> bytes:
    """Composite one or more logo images onto clean white rounded cards matching dashboard styling."""
    from PIL import Image as PImage, ImageDraw

    cards = []
    card_w = 260 if len(logo_items) == 1 else 200
    card_h = target_h_px
    gap = 12

    for item in logo_items:
        try:
            if isinstance(item, tuple):
                _, raw_bytes = item
                im = PImage.open(io.BytesIO(raw_bytes))
            else:
                im = PImage.open(item)
            card = PImage.new("RGBA", (card_w, card_h), (255, 255, 255, 255))
            draw = ImageDraw.Draw(card)
            draw.rounded_rectangle(
                [0, 0, card_w - 1, card_h - 1],
                radius=6,
                fill=(255, 255, 255, 255),
                outline=(203, 213, 225, 255),
                width=1,
            )
            im = im.convert("RGBA")
            max_inner_w = card_w - 16
            max_inner_h = card_h - 12
            im.thumbnail((max_inner_w, max_inner_h), PImage.Resampling.LANCZOS)
            paste_x = (card_w - im.width) // 2
            paste_y = (card_h - im.height) // 2
            card.alpha_composite(im, (paste_x, paste_y))
            cards.append(card)
        except Exception:
            continue

    if not cards:
        return b""

    total_w = sum(c.width for c in cards) + gap * (len(cards) - 1)
    composite = PImage.new("RGBA", (total_w, card_h), (0, 0, 0, 0))
    x_cursor = 0
    for c in cards:
        composite.alpha_composite(c, (x_cursor, 0))
        x_cursor += c.width + gap

    buf = io.BytesIO()
    composite.save(buf, format="PNG")
    return buf.getvalue()


def get_farm_logo_assets(farm_id: str | None, farm_name: str) -> dict:
    """Resolve farm logo for PDF and HTML, mirroring dashboard partner logo behavior."""
    settings = get_settings()
    items: list[tuple[str, bytes]] = []

    # 1. Primary: Database persistence (logo_assets table)
    if farm_id:
        try:
            from app.routers.farms import get_db_logo, list_db_logos

            for row in list_db_logos(farm_id):
                fname = row["filename"]
                db_item = get_db_logo(farm_id, fname)
                if db_item and db_item.get("data"):
                    d = db_item["data"]
                    if not isinstance(d, (bytes, bytearray)):
                        d = bytes(d)
                    items.append((fname, bytes(d)))
        except Exception:
            pass

    # 2. Secondary: Static disk assets for farm
    if not items and farm_id:
        farm_logo_dir = Path(settings.logos_path) / farm_id
        if farm_logo_dir.is_dir():
            for fname in sorted(os.listdir(farm_logo_dir)):
                if fname.lower().endswith((".png", ".jpg", ".jpeg", ".svg")):
                    p = farm_logo_dir / fname
                    try:
                        items.append((fname, p.read_bytes()))
                    except Exception:
                        pass

    # 3. Fallback: Default partner logos (USYD + MLA) mirroring dashboard behavior
    if not items:
        try:
            from app.routers.farms import get_db_logo, list_db_logos

            for row in list_db_logos("default"):
                fname = row["filename"]
                db_item = get_db_logo("default", fname)
                if db_item and db_item.get("data"):
                    d = db_item["data"]
                    if not isinstance(d, (bytes, bytearray)):
                        d = bytes(d)
                    items.append((fname, bytes(d)))
        except Exception:
            pass

    if not items:
        default_dir = Path(settings.logos_path) / "default"
        if default_dir.is_dir():
            for fname in sorted(os.listdir(default_dir)):
                if fname.lower().endswith((".png", ".jpg", ".jpeg", ".svg")):
                    p = default_dir / fname
                    try:
                        items.append((fname, p.read_bytes()))
                    except Exception:
                        pass

    pdf_img_bytes = None
    html_logo_tag = None

    if items:
        pdf_img_bytes = _render_logo_cards_composite_png(items)
        html_cards = []
        for filename, raw_bytes in items:
            try:
                is_svg = raw_bytes.strip().startswith(b"<svg") or filename.lower().endswith(".svg")
                stem = Path(filename).stem
                if is_svg:
                    svg_text = raw_bytes.decode("utf-8", errors="replace")
                    html_cards.append(f'<div class="header-logo-card">{svg_text}</div>')
                else:
                    b64 = base64.b64encode(raw_bytes).decode("ascii")
                    mime = "image/png" if filename.lower().endswith(".png") else "image/jpeg"
                    html_cards.append(
                        f'<div class="header-logo-card"><img alt="{stem} Logo" src="data:{mime};base64,{b64}"/></div>'
                    )
            except Exception:
                continue
        if html_cards:
            html_logo_tag = f'<div class="header-logo header-logos">{"".join(html_cards)}</div>'

    if not pdf_img_bytes:
        pdf_img_bytes = _render_logo_badge_png(farm_name)
    if not html_logo_tag:
        html_logo_tag = f'<div class="header-logo header-logo-badge"><span>{farm_name}</span></div>'

    return {
        "pdf_bytes": pdf_img_bytes,
        "html_tag": html_logo_tag,
    }


def _normalize_chart_name(name: str) -> str:
    key = (name or "").strip().lower().replace("_", " ")
    if key in ("summary statistics", "summary", "stats"):
        return "summary"
    if key in ("time series", "timeseries", "ts"):
        return "timeseries"
    if key in ("distribution", "distributions", "hist", "histogram"):
        return "distribution"
    if key in ("cohorts", "cohort"):
        return "cohorts"
    return key


def _to_datetimes(values: list) -> list[datetime]:
    out: list[datetime] = []
    for v in values:
        if isinstance(v, datetime):
            out.append(v)
        else:
            out.append(pd.to_datetime(v).to_pydatetime())
    return out


def _format_time_axis(ax, n_points: int) -> None:
    locator = mdates.AutoDateLocator(minticks=4, maxticks=8)
    if n_points <= 14:
        formatter = mdates.DateFormatter("%d %b")
    elif n_points <= 90:
        formatter = mdates.DateFormatter("%d %b")
    else:
        formatter = mdates.ConciseDateFormatter(locator)
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(formatter)
    ax.tick_params(axis="x", labelsize=7)
    for label in ax.get_xticklabels():
        label.set_rotation(25)
        label.set_horizontalalignment("right")


class NumberedCanvas(canvas.Canvas):
    """Two-pass canvas to dynamically render running headers and footers with total page count."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states: list[dict] = []

    def showPage(self) -> None:
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count: int) -> None:
        self.saveState()
        page_w, page_h = A4

        if self._pageNumber == 1:
            # Page 1 is executive cover: footer note only
            self.setFont("Helvetica", 7.5)
            self.setFillColor(colors.HexColor("#64748B"))
            self.drawCentredString(
                page_w / 2.0, 24, "Livestock Analytics System • Confidential Client Report"
            )
            self.restoreState()
            return

        # Running Top Banner (Pages 2+)
        self.setFont("Helvetica-Bold", 7.5)
        self.setFillColor(colors.HexColor("#1B4332"))
        self.drawString(36, page_h - 26, "LIVESTOCK PERFORMANCE & HERD REPORT")
        self.setFont("Helvetica", 7.5)
        self.setFillColor(colors.HexColor("#64748B"))
        self.drawRightString(page_w - 36, page_h - 26, "Automated Analytics Export")
        self.setStrokeColor(colors.HexColor("#CBD5E1"))
        self.setLineWidth(0.5)
        self.line(36, page_h - 30, page_w - 36, page_h - 30)

        # Running Bottom Footer (Pages 2+)
        self.line(36, 32, page_w - 36, 32)
        self.setFont("Helvetica", 7.5)
        self.drawString(36, 22, "Livestock Analytics System • Confidential Client Report")
        page_text = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(page_w - 36, 22, page_text)
        self.restoreState()


class ReportGenerator:
    @staticmethod
    def compute_executive_summary_stats(df: pd.DataFrame, measures: list[str]) -> list[dict]:
        """Compute herd-level parametric statistics for each selected measure."""
        results = []
        for m in measures:
            base_label, unit = extract_measure_label_and_unit(m)
            disp = format_measure_display(m)
            if m in df.columns:
                s = pd.to_numeric(df[m], errors="coerce").dropna()
            else:
                s = pd.Series(dtype=float)

            if not s.empty:
                results.append(
                    {
                        "measure": m,
                        "label": base_label,
                        "unit": unit,
                        "display_name": disp,
                        "total_records": len(s),
                        "mean": round(float(s.mean()), 2),
                        "median": round(float(s.median()), 2),
                        "std_dev": round(float(s.std()), 2) if len(s) > 1 else 0.0,
                        "min": round(float(s.min()), 2),
                        "max": round(float(s.max()), 2),
                    }
                )
            else:
                results.append(
                    {
                        "measure": m,
                        "label": base_label,
                        "unit": unit,
                        "display_name": disp,
                        "total_records": 0,
                        "mean": 0.0,
                        "median": 0.0,
                        "std_dev": 0.0,
                        "min": 0.0,
                        "max": 0.0,
                    }
                )
        return results

    @staticmethod
    def generate_chart_png(
        grouped_df: pd.DataFrame,
        chart_source: str,
        measure: str,
        filters: FilterState,
        filtered_df: pd.DataFrame | None = None,
        figsize: tuple[float, float] = (7.2, 2.15),
    ) -> bytes:
        plt.rcParams["font.sans-serif"] = ["Helvetica", "Arial", "DejaVu Sans", "sans-serif"]
        plt.rcParams["font.family"] = "sans-serif"
        plt.rcParams["axes.edgecolor"] = "#CBD5E1"
        plt.rcParams["axes.linewidth"] = 0.8

        kind = _normalize_chart_name(chart_source)
        filtered_df = filtered_df if filtered_df is not None else grouped_df
        base_label, unit = extract_measure_label_and_unit(measure)
        full_disp = format_measure_display(measure)
        y_wrapped = textwrap.fill(full_disp, width=32)

        if measure not in grouped_df.columns:
            fig, ax = plt.subplots(figsize=figsize, layout="constrained")
            ax.axis("off")
            ax.text(
                0.5,
                0.5,
                f"Measure '{base_label}' not present in filtered dataset",
                ha="center",
                va="center",
                fontsize=8,
                color="#64748B",
            )
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=300)
            plt.close(fig)
            buf.seek(0)
            return buf.read()

        if kind == "timeseries":
            fig, ax = plt.subplots(figsize=figsize, layout="constrained")
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            ax.spines["left"].set_color("#94A3B8")
            ax.spines["bottom"].set_color("#94A3B8")
            ax.grid(axis="y", linestyle="--", alpha=0.5, color="#E2E8F0", linewidth=0.6)

            result = TimeseriesService.compute(grouped_df, measure)
            n_points = 0
            groups = sorted({s["group"] for s in result["series"] if "(trend)" not in s["group"]})

            for idx, group in enumerate(groups[:6]):
                pts = sorted(
                    (s for s in result["series"] if s["group"] == group),
                    key=lambda p: p["date"],
                )
                if not pts:
                    continue
                dates = _to_datetimes([p["date"] for p in pts])
                values = [p["value"] for p in pts]
                n_points = max(n_points, len(dates))
                clean_lbl = clean_group_label(group)
                ax.plot(
                    dates,
                    values,
                    label=clean_lbl,
                    marker="o",
                    markersize=2.0,
                    linewidth=1.5,
                )
            ax.set_xlabel("Date", fontsize=7.5, color="#334155")
            ax.set_ylabel(y_wrapped, fontsize=7.5, color="#334155")
            ax.set_title(f"Time Series — {base_label}", fontsize=8.5, fontweight="bold", color="#1E293B", pad=4)
            ax.tick_params(labelsize=6.5, colors="#475569")
            if groups:
                ax.legend(fontsize=6.5, frameon=True, facecolor="#F8FAFC", edgecolor="#CBD5E1", ncol=min(3, len(groups)))
            if n_points:
                _format_time_axis(ax, n_points)

        elif kind == "distribution":
            result = DistributionService.compute(grouped_df, measure)
            box_groups = result.get("boxplot", {}).get("groups", [])
            bxp_data = []
            for g in box_groups:
                lbl = clean_group_label(g["group"])
                if len(box_groups) > 4 and len(lbl) > 14:
                    lbl = f"{lbl[:13]}…"
                bxp_data.append({
                    "med": g["median"],
                    "q1": g["q1"],
                    "q3": g["q3"],
                    "whislo": g["min"],
                    "whishi": g["max"],
                    "mean": round((g["q1"] + g["q3"]) / 2, 2),
                    "fliers": [],
                    "label": lbl,
                })

            width_ratios = [1.15, 1.0] if len(bxp_data) > 4 else [1.35, 1.0]
            fig, (ax_hist, ax_box) = plt.subplots(
                1, 2, figsize=figsize, gridspec_kw={"width_ratios": width_ratios}, layout="constrained"
            )

            # Left Panel: Frequency Histogram
            ax_hist.spines["top"].set_visible(False)
            ax_hist.spines["right"].set_visible(False)
            ax_hist.spines["left"].set_color("#94A3B8")
            ax_hist.spines["bottom"].set_color("#94A3B8")
            ax_hist.grid(axis="y", linestyle="--", alpha=0.5, color="#E2E8F0", linewidth=0.6)

            hist_info = result.get("histogram", {})
            groups = hist_info.get("groups", [])
            bins = hist_info.get("bins", 20)

            if len(groups) <= 3:
                for idx, g in enumerate(groups):
                    ax_hist.hist(
                        g["values"],
                        bins=bins,
                        alpha=0.65,
                        label=clean_group_label(g["group"]),
                        edgecolor="white",
                    )
            else:
                all_vals = []
                for g in groups:
                    all_vals.extend(g["values"])
                ax_hist.hist(all_vals, bins=bins, alpha=0.75, edgecolor="white", label="Filtered Herd")

            mean_v = hist_info.get("mean")
            med_v = hist_info.get("median")
            if mean_v is not None and mean_v > 0:
                ax_hist.axvline(
                    mean_v,
                    color="#D97706",
                    linestyle="--",
                    linewidth=1.6,
                    label=f"Mean: {mean_v:.1f}",
                )
            if med_v is not None and med_v > 0:
                ax_hist.axvline(
                    med_v,
                    color="#475569",
                    linestyle=":",
                    linewidth=1.6,
                    label=f"Median: {med_v:.1f}",
                )

            ax_hist.set_xlabel(y_wrapped, fontsize=7.5, color="#334155")
            ax_hist.set_ylabel("Animal Count", fontsize=7.5, color="#334155")
            ax_hist.set_title(f"Distribution (7d Avg) — {base_label}", fontsize=8.5, fontweight="bold", color="#1E293B", pad=4)
            ax_hist.tick_params(labelsize=6.5, colors="#475569")
            ax_hist.legend(fontsize=6.0, frameon=True, facecolor="#F8FAFC", edgecolor="#CBD5E1")

            # Right Panel: Reintroduced Quartile Box Plot
            ax_box.spines["top"].set_visible(False)
            ax_box.spines["right"].set_visible(False)
            ax_box.spines["left"].set_color("#94A3B8")
            ax_box.spines["bottom"].set_color("#94A3B8")
            ax_box.grid(axis="y", linestyle="--", alpha=0.5, color="#E2E8F0", linewidth=0.6)

            if bxp_data:
                bp = ax_box.bxp(
                    bxp_data,
                    patch_artist=True,
                    showmeans=True,
                    meanprops=dict(marker="D", markeredgecolor="#D97706", markerfacecolor="#D97706", markersize=2.0),
                )
                prop_cycle = plt.rcParams["axes.prop_cycle"].by_key().get("color", ["#1f77b4"])
                for idx, patch in enumerate(bp["boxes"]):
                    patch.set_facecolor(prop_cycle[idx % len(prop_cycle)])
                    patch.set_alpha(0.65)
                    patch.set_edgecolor("#334155")
                for median in bp["medians"]:
                    median.set_color("#0F172A")
                    median.set_linewidth(1.2)

            ax_box.set_xlabel("Animal Group", fontsize=7.5, color="#334155")
            ax_box.set_ylabel(y_wrapped, fontsize=7.5, color="#334155")
            ax_box.set_title("Quartile Box Plot", fontsize=8.5, fontweight="bold", color="#1E293B", pad=4)
            ax_box.tick_params(labelsize=5.5 if len(bxp_data) > 4 else 6.5, colors="#475569")
            rot = 25 if len(bxp_data) > 4 else 15
            for lbl in ax_box.get_xticklabels():
                lbl.set_rotation(rot)
                lbl.set_horizontalalignment("right")

        elif kind == "cohorts":
            fig, ax = plt.subplots(figsize=figsize, layout="constrained")
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            ax.spines["left"].set_color("#94A3B8")
            ax.spines["bottom"].set_color("#94A3B8")
            ax.grid(axis="y", linestyle="--", alpha=0.5, color="#E2E8F0", linewidth=0.6)

            pct = getattr(filters, "cohort_percentile", 10) or 10
            result = CohortService.analyze(filtered_df, grouped_df, measure, pct, filters)
            n_points = 0
            cohort_meta = [
                ("high", "top", f"High Cohort (Top {result['percentile']}%)", "#059669"),
                ("medium", "medium", "Medium Cohort (Core 80%)", "#475569"),
                ("low", "bottom", f"Low Cohort (Tail {result['percentile']}%)", "#B91C1C"),
            ]
            for primary_key, fallback_key, label_str, c_color in cohort_meta:
                pts = [t for t in result.get("timeline", []) if t["cohort"] in (primary_key, fallback_key)]
                if not pts:
                    continue
                pts = sorted(pts, key=lambda p: p["date"])
                dates = _to_datetimes([p["date"] for p in pts])
                values = [p["value"] for p in pts]
                n_points = max(n_points, len(dates))
                ax.plot(
                    dates,
                    values,
                    label=label_str,
                    color=c_color,
                    marker="o",
                    markersize=2.0,
                    linewidth=1.5,
                )

            ax.set_xlabel("Date", fontsize=7.5, color="#334155")
            ax.set_ylabel(y_wrapped, fontsize=7.5, color="#334155")
            ax.set_title(
                f"Cohorts — {base_label} (High/Core/Low)",
                fontsize=8.5,
                fontweight="bold",
                color="#1E293B",
                pad=4,
            )
            ax.tick_params(labelsize=6.5, colors="#475569")
            handles, labels = ax.get_legend_handles_labels()
            if labels:
                ax.legend(fontsize=6.5, frameon=True, facecolor="#F8FAFC", edgecolor="#CBD5E1")
            if n_points:
                _format_time_axis(ax, n_points)
            if not result.get("timeline"):
                ax.axis("off")
                ax.text(
                    0.5,
                    0.5,
                    "Insufficient longitudinal data for 3-tier cohort trajectory",
                    ha="center",
                    va="center",
                    fontsize=8,
                    color="#64748B",
                )

        else:
            # Fallback visual for summary statistics
            fig, ax = plt.subplots(figsize=figsize, layout="constrained")
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            stats = SummaryService.compute_stats(grouped_df, measure)
            labels = []
            means = []
            for g in stats[:4]:
                for w in g["windows"].values():
                    labels.append(f"{clean_group_label(g['full_group'])[:14]} {w['label']}")
                    means.append(w["mean"])
            if means:
                ax.barh(labels[:8], means[:8], color="#2C7FB8", edgecolor="white")
                ax.set_xlabel(y_wrapped, fontsize=7.5)
            ax.set_title(f"Summary Overview — {base_label}", fontsize=8.5, fontweight="bold")
            ax.tick_params(labelsize=6.5)

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=300)
        plt.close(fig)
        buf.seek(0)
        return buf.read()

    @staticmethod
    def build_horizon_matrix_table(
        grouped_df: pd.DataFrame, measure: str, printable_width: float, max_rows: int = 8
    ) -> Table | None:
        """Construct the Pivoted Comparative Horizon Matrix for multi-filter group scaling."""
        stats = SummaryService.compute_stats(grouped_df, measure)
        if not stats:
            return None

        base_label, unit = extract_measure_label_and_unit(measure)
        unit_str = f" ({unit})" if unit else ""
        header = ["Comparison Group", f"Last Day{unit_str}", "Last 15 Days", "Last Month", "Overall (Count)"]
        rows = [header]

        styles = getSampleStyleSheet()
        grp_style = ParagraphStyle(
            "MatrixGroup",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=6.5,
            leading=8.0,
            textColor=colors.HexColor("#0F172A"),
        )
        overflow_style = ParagraphStyle(
            "MatrixOverflow",
            parent=styles["Normal"],
            fontName="Helvetica-Oblique",
            fontSize=6.0,
            leading=7.5,
            textColor=colors.HexColor("#64748B"),
        )

        display_stats = stats[:max_rows]
        for g in display_stats:
            w = g["windows"]
            grp_name = clean_group_label(g["full_group"])
            ld = f"{w['last_day']['mean']:.1f}" if w.get("last_day") else "—"
            l15 = f"{w['last_15_days']['mean']:.1f}" if w.get("last_15_days") else "—"
            lm = f"{w['last_month']['mean']:.1f}" if w.get("last_month") else "—"
            ov_m = f"{w['overall']['mean']:.1f}" if w.get("overall") else "—"
            ov_c = w["overall"]["count"] if w.get("overall") else 0
            rows.append([Paragraph(grp_name, grp_style), ld, l15, lm, f"{ov_m} ({ov_c})"])

        if len(stats) > max_rows:
            diff = len(stats) - max_rows
            msg = f"+ {diff} additional comparison groups (see Executive Summary / export)"
            rows.append([Paragraph(msg, overflow_style), "—", "—", "—", "—"])

        col_w = [
            printable_width * 0.40,
            printable_width * 0.15,
            printable_width * 0.15,
            printable_width * 0.15,
            printable_width * 0.15,
        ]
        t = Table(rows, colWidths=col_w)
        t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1B4332")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, 0), 7.0),
                    ("ALIGN", (0, 0), (0, -1), "LEFT"),
                    ("ALIGN", (1, 0), (-1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                    ("FONTSIZE", (0, 1), (-1, -1), 6.5),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                    ("TOPPADDING", (0, 0), (-1, -1), 1.8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 1.8),
                ]
            )
        )
        return t

    @staticmethod
    def generate_pdf(
        filters: FilterState,
        grouped_df: pd.DataFrame,
        charts: list[str],
        farm_name: str,
        filtered_df: pd.DataFrame | None = None,
    ) -> bytes:
        filtered_df = filtered_df if filtered_df is not None else grouped_df
        buf = io.BytesIO()
        # 0.5 inch (36 pt) margins for exact A4 fit
        margin = 36
        doc = SimpleDocTemplate(
            buf,
            pagesize=A4,
            leftMargin=margin,
            rightMargin=margin,
            topMargin=36,
            bottomMargin=36,
        )
        printable_width = A4[0] - 2 * margin
        styles = getSampleStyleSheet()

        cover_title_style = ParagraphStyle(
            "CoverTitle",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=15,
            leading=18,
            textColor=colors.white,
        )
        cover_sub_style = ParagraphStyle(
            "CoverSub",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=8.5,
            leading=12,
            textColor=colors.HexColor("#E2E8F0"),
        )
        section_title_style = ParagraphStyle(
            "SectionTitle",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=10.5,
            leading=14,
            textColor=colors.HexColor("#1B4332"),
        )
        measure_header_style = ParagraphStyle(
            "MeasureHeader",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=15,
            textColor=colors.HexColor("#1B4332"),
        )

        story = []
        measures = list(dict.fromkeys(filters.measures or [filters.measure]))
        selected_charts = [_normalize_chart_name(c) for c in (charts or [])]
        if not selected_charts:
            selected_charts = ["summary", "distribution", "timeseries"]

        # ---- PAGE 1: EXECUTIVE COVER & SUMMARY ----
        now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
        total_records = len(filtered_df) if filtered_df is not None else 0

        logo_assets = get_farm_logo_assets(filters.farm_id, farm_name)
        logo_bytes = logo_assets.get("pdf_bytes")
        if logo_bytes:
            im_logo = PILImage.open(io.BytesIO(logo_bytes))
            lw_px, lh_px = im_logo.size
            logo_h = 28.0
            logo_w = min(120.0, logo_h * (lw_px / lh_px))
            logo_img = Image(io.BytesIO(logo_bytes), width=logo_w, height=logo_h)
            header_data = [
                [
                    Paragraph("<b>Livestock Performance & Herd Report</b>", cover_title_style),
                    logo_img,
                ],
                [
                    Paragraph(
                        f"Farm: <b>{farm_name}</b>  •  Generated: {now_str}  •  Head Count: {total_records:,}",
                        cover_sub_style,
                    ),
                    "",
                ],
            ]
            header_table = Table(header_data, colWidths=[printable_width - 130, 130])
            header_table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#1B4332")),
                        ("SPAN", (1, 0), (1, 1)),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                        ("ALIGN", (1, 0), (1, 1), "RIGHT"),
                        ("TOPPADDING", (0, 0), (-1, -1), 10),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                        ("LEFTPADDING", (0, 0), (-1, -1), 14),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
                    ]
                )
            )
        else:
            header_data = [
                [Paragraph("<b>Livestock Performance & Herd Report</b>", cover_title_style)],
                [
                    Paragraph(
                        f"Farm: <b>{farm_name}</b>  •  Generated: {now_str}  •  Head Count: {total_records:,}",
                        cover_sub_style,
                    )
                ],
            ]
            header_table = Table(header_data, colWidths=[printable_width])
            header_table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#1B4332")),
                        ("TOPPADDING", (0, 0), (-1, -1), 12),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
                        ("LEFTPADDING", (0, 0), (-1, -1), 14),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
                    ]
                )
            )
        story.append(header_table)
        story.append(Spacer(1, 14))

        # Executive Summary Table (All selected measures)
        story.append(Paragraph("<b>Executive Summary Statistics</b>", section_title_style))
        story.append(Spacer(1, 5))

        stats_summary = ReportGenerator.compute_executive_summary_stats(filtered_df, measures)
        stats_rows = [["Metric", "Mean", "Median", "Std Dev", "Min", "Max", "Count"]]
        for st in stats_summary:
            stats_rows.append(
                [
                    st["display_name"],
                    f"{st['mean']:.2f}",
                    f"{st['median']:.2f}",
                    f"{st['std_dev']:.2f}",
                    f"{st['min']:.2f}",
                    f"{st['max']:.2f}",
                    f"{st['total_records']:,}",
                ]
            )

        col_w_stats = [
            printable_width * 0.34,
            printable_width * 0.11,
            printable_width * 0.11,
            printable_width * 0.11,
            printable_width * 0.11,
            printable_width * 0.11,
            printable_width * 0.11,
        ]
        summary_table = Table(stats_rows, colWidths=col_w_stats)
        summary_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1B4332")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, 0), 8.0),
                    ("ALIGN", (0, 0), (0, -1), "LEFT"),
                    ("ALIGN", (1, 0), (-1, -1), "CENTER"),
                    ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                    ("FONTSIZE", (0, 1), (-1, -1), 7.5),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                    ("TOPPADDING", (0, 0), (-1, -1), 4.5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
                ]
            )
        )
        story.append(summary_table)
        story.append(Spacer(1, 14))

        # Active Filters Applied Table
        story.append(Paragraph("<b>Active Filters</b>", section_title_style))
        story.append(Spacer(1, 5))

        filter_dict = generate_filter_summary(filters.model_dump())
        filter_rows = [["Filter Dimension", "Active Selection"]]
        for k, v in filter_dict.items():
            filter_rows.append([k.replace("_", " ").title(), str(v)])
        if len(filter_rows) == 1:
            filter_rows.append(["Filters", "No restrictive filters applied (Full herd scope)"])

        filter_table = Table(filter_rows, colWidths=[printable_width * 0.28, printable_width * 0.72])
        filter_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2E8F0")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1B4332")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, 0), 8.0),
                    ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
                    ("FONTNAME", (1, 1), (1, -1), "Helvetica"),
                    ("FONTSIZE", (0, 1), (-1, -1), 7.5),
                    ("TEXTCOLOR", (0, 1), (0, -1), colors.HexColor("#475569")),
                    ("TEXTCOLOR", (1, 1), (1, -1), colors.HexColor("#0F172A")),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                    ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#F8FAFC")),
                    ("TOPPADDING", (0, 0), (-1, -1), 4.0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4.0),
                ]
            )
        )
        story.append(filter_table)
        story.append(Spacer(1, 16))

        # PageBreak to ensure Visuals strictly start on Page 2
        story.append(PageBreak())

        def _make_chart_flowable(png_bytes: bytes, target_width: float) -> Image:
            im = PILImage.open(io.BytesIO(png_bytes))
            w_px, h_px = im.size
            aspect = h_px / w_px
            return Image(io.BytesIO(png_bytes), width=target_width, height=target_width * aspect)

        # ---- PAGES 2+: GROUP BY MEASURE (1 PAGE PER MEASURE) ----
        visual_charts = [_normalize_chart_name(c) for c in selected_charts if _normalize_chart_name(c) != "summary"]
        include_summary_matrix = "summary" in selected_charts

        for measure_idx, measure in enumerate(measures):
            m_disp = format_measure_display(measure)

            measure_elements = []
            measure_elements.append(
                Paragraph(f"<b>{m_disp}</b>", measure_header_style)
            )
            measure_elements.append(Spacer(1, 4))

            # 1. Comparative Horizon Matrix (Framed to max 8 groups to avoid overflow)
            if include_summary_matrix:
                matrix_tbl = ReportGenerator.build_horizon_matrix_table(grouped_df, measure, printable_width, max_rows=8)
                if matrix_tbl:
                    measure_elements.append(matrix_tbl)
                    measure_elements.append(Spacer(1, 4))

            # 2. Visual Charts in Dashboard Sequence (Timeseries -> Distribution -> Cohorts)
            DASHBOARD_ORDER = ["timeseries", "distribution", "cohorts"]
            ordered_visuals = [c for c in DASHBOARD_ORDER if c in visual_charts]
            for c in visual_charts:
                if c not in ordered_visuals:
                    ordered_visuals.append(c)

            chart_count = len(ordered_visuals)
            if chart_count >= 3:
                c_height = 2.45 if include_summary_matrix else 3.20
            elif chart_count == 2:
                c_height = 3.80 if include_summary_matrix else 4.80
            elif chart_count == 1:
                c_height = 5.20 if include_summary_matrix else 6.50
            else:
                c_height = 2.45

            for vk in ordered_visuals:
                png = ReportGenerator.generate_chart_png(
                    grouped_df, vk, measure, filters, filtered_df=filtered_df, figsize=(7.2, c_height)
                )
                img = _make_chart_flowable(png, printable_width)
                measure_elements.append(img)
                measure_elements.append(Spacer(1, 4))

            story.append(KeepTogether(measure_elements))

            # PageBreak after each measure so each measure is strictly 1 vertical A4 page
            if measure_idx < len(measures) - 1:
                story.append(PageBreak())

        doc.build(story, canvasmaker=NumberedCanvas)
        buf.seek(0)
        return buf.read()

    @staticmethod
    def generate_html(
        filters: FilterState,
        grouped_df: pd.DataFrame,
        charts: list[str],
        farm_name: str,
        filtered_df: pd.DataFrame | None = None,
    ) -> str:
        filtered_df = filtered_df if filtered_df is not None else grouped_df
        summary_dict = generate_filter_summary(filters.model_dump())
        measures = list(dict.fromkeys(filters.measures or [filters.measure]))
        selected_charts = [_normalize_chart_name(c) for c in (charts or [])]
        if not selected_charts:
            selected_charts = ["summary", "timeseries", "distribution", "cohorts"]

        now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
        total_records = len(filtered_df) if filtered_df is not None else 0

        logo_assets = get_farm_logo_assets(filters.farm_id, farm_name)
        html_logo_tag = logo_assets.get("html_tag", "")

        # Executive Summary Table HTML
        stats_summary = ReportGenerator.compute_executive_summary_stats(filtered_df, measures)
        stats_rows_html = "".join(
            f"<tr><td><b>{st['display_name']}</b></td><td>{st['mean']:.2f}</td><td>{st['median']:.2f}</td>"
            f"<td>{st['std_dev']:.2f}</td><td>{st['min']:.2f}</td><td>{st['max']:.2f}</td><td>{st['total_records']:,}</td></tr>"
            for st in stats_summary
        )

        filter_items_html = "".join(
            f"<li><b>{k.replace('_', ' ').title()}:</b> {v}</li>" for k, v in summary_dict.items()
        )
        if not filter_items_html:
            filter_items_html = "<li>No restrictive filters applied (Full herd scope)</li>"

        # Multi-Measure Sections in Dashboard Order
        measure_sections_html = []
        for m in measures:
            m_disp = format_measure_display(m)

            sec_html = f"<section class='measure-section page-break'><h2>{m_disp}</h2>"
            if len(measures) == 1:
                sec_html += f"<p class='measure-sub'><i>Measure: {m_disp}</i></p>"

            # Summary Statistics Horizon Table
            if "summary" in selected_charts:
                stats = SummaryService.compute_stats(grouped_df, m)
                if stats:
                    base_label, unit = extract_measure_label_and_unit(m)
                    u_hdr = f" ({unit})" if unit else ""
                    sec_html += "<div class='card'><h3>Summary Statistics</h3><div class='table-responsive'><table class='report-table'>"
                    sec_html += f"<thead><tr><th>Comparison Group</th><th>Last Day{u_hdr}</th><th>Last 15 Days</th><th>Last Month</th><th>Overall</th></tr></thead><tbody>"
                    for g in stats:
                        w = g["windows"]
                        grp_name = clean_group_label(g["full_group"])
                        ld = f"{w['last_day']['mean']:.1f}" if w.get("last_day") else "—"
                        l15 = f"{w['last_15_days']['mean']:.1f}" if w.get("last_15_days") else "—"
                        lm = f"{w['last_month']['mean']:.1f}" if w.get("last_month") else "—"
                        ov_m = f"{w['overall']['mean']:.1f}" if w.get("overall") else "—"
                        ov_c = w["overall"]["count"] if w.get("overall") else 0
                        sec_html += f"<tr><td>{grp_name}</td><td>{ld}</td><td>{l15}</td><td>{lm}</td><td>{ov_m} ({ov_c})</td></tr>"
                    sec_html += "</tbody></table></div></div>"

            # Visual Charts in Dashboard Sequence
            visual_charts = [_normalize_chart_name(c) for c in selected_charts if _normalize_chart_name(c) != "summary"]
            DASHBOARD_ORDER = ["timeseries", "distribution", "cohorts"]
            ordered_visuals = [c for c in DASHBOARD_ORDER if c in visual_charts]
            for c in visual_charts:
                if c not in ordered_visuals:
                    ordered_visuals.append(c)

            chart_count = len(ordered_visuals)
            if chart_count >= 3:
                html_c_height = 2.90
            elif chart_count == 2:
                html_c_height = 3.80
            elif chart_count == 1:
                html_c_height = 5.20
            else:
                html_c_height = 2.90

            for vk in ordered_visuals:
                title = {
                    "timeseries": "Time Series",
                    "distribution": "Distribution",
                    "cohorts": "Cohorts",
                }.get(vk, vk.title())
                try:
                    png = ReportGenerator.generate_chart_png(
                        grouped_df, vk, m, filters, filtered_df=filtered_df, figsize=(7.2, html_c_height)
                    )
                    b64 = base64.b64encode(png).decode("ascii")
                    sec_html += (
                        f"<div class='card chart-card'><h3>{title}</h3>"
                        f"<img alt='{title} — {m_disp}' src='data:image/png;base64,{b64}'/></div>"
                    )
                except Exception as exc:
                    sec_html += f"<div class='card'><h3>{title}</h3><p><i>Could not render chart: {exc}</i></p></div>"

            sec_html += "</section>"
            measure_sections_html.append(sec_html)

        body_content = "\n".join(measure_sections_html)

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Livestock Report — {farm_name}</title>
  <style>
    :root {{
      --primary: #1B4332;
      --bg: #F8F9FA;
      --card-bg: #FFFFFF;
      --border: #CBD5E1;
      --text-main: #0F172A;
      --text-muted: #475569;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background-color: var(--bg);
      color: var(--text-main);
      margin: 0;
      padding: 24px;
      line-height: 1.5;
    }}
    .report-container {{
      max-width: 940px;
      margin: 0 auto;
    }}
    .header-banner {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      background-color: var(--primary);
      color: #FFFFFF;
      padding: 18px 24px;
      border-radius: 6px;
      margin-bottom: 20px;
    }}
    .header-info h1 {{
      margin: 0 0 4px 0;
      font-size: 20px;
      letter-spacing: 0.5px;
    }}
    .header-info p {{
      margin: 0;
      font-size: 13px;
      color: #E2E8F0;
    }}
    .header-logos {{
      display: flex;
      gap: 8px;
      align-items: center;
      justify-content: flex-end;
    }}
    .header-logo-card {{
      background: #FFFFFF;
      padding: 3px 6px;
      border: 1px solid #CBD5E1;
      border-radius: 4px;
      display: flex;
      align-items: center;
      justify-content: center;
      height: 38px;
      box-sizing: border-box;
    }}
    .header-logo-card img {{
      max-height: 28px;
      max-width: 90px;
      object-fit: contain;
      display: block;
    }}
    .header-logo-card svg {{
      max-height: 28px;
      max-width: 90px;
      display: block;
    }}
    .header-logo svg {{
      max-height: 44px;
      max-width: 180px;
      display: block;
    }}
    .header-logo img {{
      max-height: 44px;
      max-width: 180px;
      object-fit: contain;
      display: block;
    }}
    .header-logo-badge {{
      background: rgba(255, 255, 255, 0.12);
      border: 1px solid rgba(255, 255, 255, 0.25);
      padding: 6px 14px;
      border-radius: 4px;
      font-weight: 600;
      font-size: 13px;
      color: #FFFFFF;
    }}
    .card {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 16px 20px;
      margin-bottom: 20px;
    }}
    h2 {{
      color: var(--primary);
      font-size: 16px;
      margin: 0 0 12px 0;
      padding-bottom: 6px;
      border-bottom: 2px solid var(--primary);
    }}
    h3 {{
      color: var(--text-main);
      font-size: 14px;
      margin: 0 0 10px 0;
    }}
    .report-table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
      margin: 4px 0 10px 0;
    }}
    .report-table th, .report-table td {{
      padding: 8px 10px;
      border: 1px solid var(--border);
      text-align: center;
    }}
    .report-table th {{
      background-color: var(--primary);
      color: #FFFFFF;
      font-weight: 600;
    }}
    .report-table td:first-child, .report-table th:first-child {{
      text-align: left;
    }}
    .report-table tbody tr:nth-child(even) {{
      background-color: #F8FAFC;
    }}
    .filters-list {{
      list-style: none;
      padding: 0;
      margin: 0;
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 8px;
      font-size: 12px;
    }}
    .filters-list li {{
      background: #F1F5F9;
      padding: 6px 10px;
      border-radius: 4px;
      color: var(--text-muted);
    }}
    .filters-list li b {{
      color: var(--text-main);
    }}
    .chart-card img {{
      width: 100%;
      height: auto;
      border-radius: 4px;
      display: block;
    }}
    .print-btn {{
      position: fixed;
      bottom: 24px;
      right: 24px;
      background: var(--primary);
      color: #FFFFFF;
      border: none;
      padding: 10px 18px;
      border-radius: 6px;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      box-shadow: 0 4px 10px rgba(0,0,0,0.15);
      z-index: 100;
    }}
    .print-btn:hover {{
      background: #143326;
    }}
    @media print {{
      body {{ background: #FFFFFF; padding: 0; font-size: 11px; }}
      .print-btn {{ display: none !important; }}
      .page-break {{ page-break-before: always; }}
      .card {{ border: 1px solid #E2E8F0; margin-bottom: 14px; padding: 12px; page-break-inside: avoid; }}
      .header-banner {{ border-radius: 0; margin-bottom: 14px; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
    }}
  </style>
</head>
<body>
  <button class="print-btn" onclick="window.print()">Print / Save as PDF</button>
  <div class="report-container">
    <div class="header-banner">
      <div class="header-info">
        <h1>Livestock Report — {farm_name}</h1>
        <p>Generated: {now_str} • Observations: {total_records:,}</p>
      </div>
      {html_logo_tag}
    </div>

    <div class="card">
      <h2>Executive Summary Statistics</h2>
      <div class="table-responsive">
        <table class="report-table">
          <thead>
            <tr>
              <th>Metric</th><th>Mean</th><th>Median</th><th>Std Dev</th><th>Min</th><th>Max</th><th>Count</th>
            </tr>
          </thead>
          <tbody>
            {stats_rows_html}
          </tbody>
        </table>
      </div>
    </div>

    <div class="card">
      <h2>Active Filters</h2>
      <ul class="filters-list">
        {filter_items_html}
      </ul>
    </div>

    {body_content}
  </div>
</body>
</html>"""
