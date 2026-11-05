from __future__ import annotations

import io

import pandas as pd
import pytest

from app.models.schemas import FilterState
from app.services.report_generator import ReportGenerator


@pytest.fixture
def report_df(sample_df: pd.DataFrame) -> pd.DataFrame:
    df = sample_df.copy()
    df["group"] = "Overall"
    df["full_group"] = "Overall"
    return df


@pytest.mark.unit
def test_html_report_includes_all_selected_measures(report_df: pd.DataFrame):
    filters = FilterState(
        farm_id="KF",
        year=2023,
        measures=["finalpweight", "methane"],
    )
    html = ReportGenerator.generate_html(
        filters, report_df, ["Summary Statistics", "Distribution"], "Killara Feedlot"
    )
    assert "Final processed weight" in html
    assert "Methane" in html
    # One section heading per selected measure
    assert html.count("<h2>") >= 3  # measure sections + chart titles


@pytest.mark.unit
def test_single_measure_report_unchanged(report_df: pd.DataFrame):
    filters = FilterState(farm_id="KF", year=2023, measures=["finalpweight"])
    html = ReportGenerator.generate_html(filters, report_df, ["Summary Statistics"], "Killara")
    assert "Measure: Final processed weight" in html


@pytest.mark.unit
def test_pdf_report_multi_measure_generation(report_df: pd.DataFrame):
    """US14: Verifies generate_pdf produces a valid PDF file when multiple measures are selected."""
    filters = FilterState(farm_id="KF", year=2023, measures=["finalpweight", "methane"])
    pdf_bytes = ReportGenerator.generate_pdf(
        filters, report_df, ["Summary Statistics", "Distribution"], "Killara Feedlot"
    )
    assert isinstance(pdf_bytes, (bytes, bytearray))
    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 1000


@pytest.mark.unit
def test_email_service_multi_measure_summary():
    """US14: Verifies email filter summary combines multiple measure labels cleanly."""
    from app.services.email_service import generate_filter_summary

    payload = {"farm_id": "KF", "year": 2023, "measures": ["finalpweight", "methane"]}
    summary = generate_filter_summary(payload)
    assert "Final processed weight (kg)" in summary["measure"]
    assert "Methane production (g/day)" in summary["measure"]


@pytest.mark.unit
def test_executive_summary_stats_computation(report_df: pd.DataFrame):
    """US14: Verifies compute_executive_summary_stats returns mean, median, std_dev, min, max, count."""
    stats = ReportGenerator.compute_executive_summary_stats(
        report_df, ["finalpweight", "methane"]
    )
    assert len(stats) == 2
    for st in stats:
        assert "mean" in st
        assert "median" in st
        assert "std_dev" in st
        assert "min" in st
        assert "max" in st
        assert st["total_records"] > 0


@pytest.mark.unit
def test_pdf_report_exact_page_count_per_measure(report_df: pd.DataFrame):
    """US14: Verifies Group-by-Measure architecture packs exactly 1 page per measure (1 cover + M pages)."""
    import io
    from pypdf import PdfReader

    filters = FilterState(farm_id="KF", year=2023, measures=["finalpweight", "methane"])
    pdf_bytes = ReportGenerator.generate_pdf(
        filters,
        report_df,
        ["Summary Statistics", "Distribution", "Time Series", "Cohorts"],
        "Killara Feedlot",
    )
    reader = PdfReader(io.BytesIO(pdf_bytes))
    # 1 cover page + 2 measure pages = exactly 3 pages
    assert len(reader.pages) == 3


@pytest.mark.unit
def test_email_body_includes_inline_kpi_table(report_df: pd.DataFrame):
    """US14: Verifies build_email_body injects formatted inline KPI table when stats_summary provided."""
    from app.services.email_service import EmailService

    filters = {"farm_id": "KF", "year": 2023, "measures": ["finalpweight"]}
    stats = ReportGenerator.compute_executive_summary_stats(report_df, ["finalpweight"])
    body = EmailService.build_email_body(
        filters, "Custom test note", "Killara Feedlot", stats_summary=stats
    )
    assert "Executive Key Performance Indicators" in body
    assert "Final processed weight" in body
    assert "<table" in body


def test_clean_group_label_formatting():
    """US14: Verifies group labels strip redundant dimension prefixes and Overall tokens."""
    from app.services.labels import clean_group_label

    assert clean_group_label("Overall Average") == "Overall Herd (Benchmark)"
    assert clean_group_label("Overall") == "Overall Herd (Benchmark)"
    assert (
        clean_group_label("Sex: Heifer, Treatment: Overall, Breed: Angus, Mob: Overall, EID: *****")
        == "Heifer • Angus"
    )
    assert (
        clean_group_label("Sex: Steer, Treatment: Pasture, Breed: Crossbred, Mob: Mob A, EID: *****")
        == "Steer • Pasture • Crossbred • Mob A"
    )
    assert clean_group_label("Sex: Overall, Treatment: Overall, Breed: Overall, Mob: Overall, EID: *****") == "Overall Herd"


def test_format_measure_display_no_duplicate_units():
    """US14: Verifies format_measure_display prevents duplicate units like ((kg) (kg))."""
    from app.constants import MEASURE_CHOICES
    from app.services.labels import format_measure_display

    for m in MEASURE_CHOICES:
        disp = format_measure_display(m)
        assert "(kg) (kg)" not in disp
        assert "(kg/day) (kg/day)" not in disp
        assert "(g/day) (g/day)" not in disp
        assert "($) ($)" not in disp
        assert "(S/day) (units)" not in disp


def test_summary_matrix_overflow_framing(report_df):
    """US14: Verifies build_horizon_matrix_table frames to max_rows and adds overflow indicator."""
    # Create 12 groups in report_df
    dfs = []
    for i in range(12):
        sub = report_df.copy()
        sub["group"] = f"Group_{i}"
        sub["full_group"] = f"Sex: Heifer, Treatment: Overall, Breed: Breed_{i}, Mob: Mob A, EID: *****"
        dfs.append(sub)
    multi_group_df = pd.concat(dfs, ignore_index=True)

    tbl = ReportGenerator.build_horizon_matrix_table(multi_group_df, "finalpweight", 500, max_rows=8)
    assert tbl is not None
    # 1 header + 8 groups + 1 overflow row = 10 rows total
    assert len(tbl._cellvalues) == 10


def test_all_four_components_exact_page_count(report_df):
    """US14: Verifies all 4 vertically stacked components produce exactly 1 page per measure."""
    import pypdf

    filters = FilterState(farm_id="KF", year=2023, measures=["finalpweight", "methane"])
    charts = ["summary", "timeseries", "distribution", "cohorts"]
    pdf_bytes = ReportGenerator.generate_pdf(filters, report_df, charts, "Killara Feedlot")

    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    # 1 executive cover + 2 measure pages = exactly 3 pages
    assert len(reader.pages) == 3


def test_get_farm_logo_assets(monkeypatch):
    """US14: Verifies get_farm_logo_assets resolves logo assets for PDF and HTML."""
    from app.services.report_generator import get_farm_logo_assets

    # Hermetic: ignore any custom logos in the developer's database so the
    # disk-asset and partner-default fallbacks are what actually get tested.
    monkeypatch.setattr("app.routers.farms.list_db_logos", lambda farm_id: [])

    # Farm with logo in assets
    kf_assets = get_farm_logo_assets("KF", "Killara Feedlot")
    assert kf_assets["pdf_bytes"] is not None
    assert len(kf_assets["pdf_bytes"]) > 0
    assert "header-logo" in kf_assets["html_tag"]

    # Unknown or non-KF farm fallback: mirrors dashboard with default partner logos (USYD + MLA)
    wow_assets = get_farm_logo_assets("WOW", "WOW Feedlot Killara")
    assert wow_assets["pdf_bytes"] is not None
    assert "header-logo" in wow_assets["html_tag"]
    assert "Killara Feedlot" not in wow_assets["html_tag"]
    assert "01_logo_usyd" in wow_assets["html_tag"]
    assert "02_logo_mla" in wow_assets["html_tag"]

    unknown_assets = get_farm_logo_assets("UNKNOWN", "Unknown Farm")
    assert unknown_assets["pdf_bytes"] is not None
    assert len(unknown_assets["pdf_bytes"]) > 0
    assert "header-logo" in unknown_assets["html_tag"]
    assert "Killara Feedlot" not in unknown_assets["html_tag"]
    assert "01_logo_usyd" in unknown_assets["html_tag"]
    assert "02_logo_mla" in unknown_assets["html_tag"]


def test_clean_group_label_preserves_no_treatment_to_avoid_collision():
    """Verifies that 'No Treatment' is preserved in group labels so it never collides with Overall."""
    from app.services.labels import clean_group_label

    overall_treatment = "Sex: Overall, Treatment: Overall, Breed: AA, Mob: Overall, EID: *****"
    no_treatment = "Sex: Overall, Treatment: No Treatment, Breed: AA, Mob: Overall, EID: *****"

    cleaned_overall = clean_group_label(overall_treatment)
    cleaned_no_tx = clean_group_label(no_treatment)

    assert cleaned_overall == "AA"
    assert cleaned_no_tx == "No Treatment • AA"
    assert cleaned_overall != cleaned_no_tx


def test_executive_summary_fluff_removal_and_logo(report_df):
    """US14: Verifies HTML report includes logo and eliminates boilerplate fluff text."""
    filters = FilterState(farm_id="KF", year=2023, measures=["finalpweight"])
    html = ReportGenerator.generate_html(filters, report_df, ["summary"], "Killara Feedlot")

    # Logo presence
    assert "header-logo" in html
    # Fluff text elimination
    assert "Individual animal identifiers are masked with keyed HMAC-SHA256 privacy tokens" not in html
    assert "Data reflects filtered database grain evaluated at report generation time" not in html
    # Clean section titles
    assert "<h2>Executive Summary Statistics</h2>" in html
    assert "<h2>Active Filters</h2>" in html


def test_max_comparison_groups_all_four_components_no_overflow(report_df):
    """Verifies that 8 comparison groups with wrapped labels and all 4 components fit strictly on 1 page per measure."""
    import pypdf

    dfs = []
    combos = [
        ("Overall", "Overall", "Overall"),
        ("Overall", "Overall", "AA"),
        ("Overall", "Overall", "AXH"),
        ("Overall", "No Treatment", "Overall"),
        ("Overall", "No Treatment", "AA"),
        ("Overall", "No Treatment", "AXH"),
        ("H", "Overall", "Overall"),
        ("H", "Overall", "AA"),
    ]
    for sex, tx, breed in combos:
        sub = report_df.copy()
        sub["group"] = f"{sex}_{tx}_{breed}"
        sub["full_group"] = f"Sex: {sex}, Treatment: {tx}, Breed: {breed}, Mob: Overall, EID: *****"
        dfs.append(sub)
    multi_group_df = pd.concat(dfs, ignore_index=True)

    filters = FilterState(farm_id="KF", year=2023, measures=["finalpweight"])
    charts = ["summary", "timeseries", "distribution", "cohorts"]
    pdf_bytes = ReportGenerator.generate_pdf(filters, multi_group_df, charts, "Killara Feedlot")

    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    # 1 executive cover + 1 measure page = strictly 2 pages total (zero page overflow)
    assert len(reader.pages) == 2


def test_get_farm_logo_assets_from_database():
    """US14: Verifies get_farm_logo_assets loads and composites database-backed custom logos."""
    from PIL import Image as PImage
    from app.routers.farms import delete_db_logos, save_logo
    from app.services.report_generator import get_farm_logo_assets

    buf = io.BytesIO()
    im = PImage.new("RGBA", (100, 40), (46, 125, 50, 255))
    im.save(buf, format="PNG")
    dummy_png = buf.getvalue()

    farm_id = "TEST_DB_LOGO"
    delete_db_logos(farm_id)
    try:
        save_logo(farm_id, "custom_banner.png", "image/png", dummy_png)
        assets = get_farm_logo_assets(farm_id, "Test DB Logo Farm")
        assert assets["pdf_bytes"] is not None
        assert len(assets["pdf_bytes"]) > 0
        assert "header-logo" in assets["html_tag"]
        assert "custom_banner" in assets["html_tag"]
        assert "01_logo_usyd" not in assets["html_tag"]
        assert "02_logo_mla" not in assets["html_tag"]
    finally:
        delete_db_logos(farm_id)






