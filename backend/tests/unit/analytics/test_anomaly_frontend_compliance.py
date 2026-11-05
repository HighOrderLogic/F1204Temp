from __future__ import annotations

import re
from pathlib import Path
import pytest

FRONTEND_DIR = Path(__file__).resolve().parents[4] / "frontend"


def test_p4_t01_navigation_and_tab_wiring():
    html = (FRONTEND_DIR / "dashboard.html").read_text(encoding="utf-8")
    assert 'data-page="anomalies"' in html
    assert "anomalies: renderAnomalies" in html
    assert '<script src="/js/pages/anomalies.js"></script>' in html
    assert "'anomalies'" in html  # in needsPlotly list
    assert "prefetchAnomalies" in html


def test_p4_t01b_anomalies_prefetch_exported():
    anomalies_js = (FRONTEND_DIR / "js" / "pages" / "anomalies.js").read_text(encoding="utf-8")
    assert "async function prefetchAnomalies" in anomalies_js
    assert "window.prefetchAnomalies = prefetchAnomalies" in anomalies_js
    assert "window.prefetchAnomaliesForFarm = prefetchAnomaliesForFarm" in anomalies_js
    assert "window.prefetchOtherFarmAnomalies = prefetchOtherFarmAnomalies" in anomalies_js
    assert "anomalyAnalyzeCache" in anomalies_js


def test_p4_t01c_farm_switch_prefetches_anomalies():
    html = (FRONTEND_DIR / "dashboard.html").read_text(encoding="utf-8")
    assert "prefetchAnomaliesForFarm(nextFarm)" in html
    assert "prefetchOtherFarmAnomalies" in html


def test_p4_t02_sidebar_measure_picker_lock_logic():
    filters_js = (FRONTEND_DIR / "js" / "filters.js").read_text(encoding="utf-8")
    assert "function updateMeasureModuleState" in filters_js
    assert "window.updateMeasureModuleState = updateMeasureModuleState" in filters_js
    assert "currentActiveModule === 'anomalies'" in filters_js
    assert "currentActiveModule === 'prediction'" in filters_js
    assert "picker.classList.add('disabled')" in filters_js
    assert "trigger.disabled = true" in filters_js


def test_p4_t03_dashboard_invokes_update_measure_state():
    html = (FRONTEND_DIR / "dashboard.html").read_text(encoding="utf-8")
    assert "updateMeasureModuleState(page)" in html
    assert "updateMeasureModuleState(currentPage)" in html


def test_p4_t04_anomalies_instant_client_side_filtering():
    anomalies_js = (FRONTEND_DIR / "js" / "pages" / "anomalies.js").read_text(encoding="utf-8")
    assert "function applyClientFilters()" in anomalies_js
    assert "cachedAnalysis.animals.filter" in anomalies_js or "allAnimals.filter" in anomalies_js


def test_p4_t05_pweight_warning_banner_toggle():
    anomalies_js = (FRONTEND_DIR / "js" / "pages" / "anomalies.js").read_text(encoding="utf-8")
    assert "id=\"anomaly-smoothing-banner\"" in anomalies_js
    assert "currentMeasure === 'pweight'" in anomalies_js
    assert "pweight is smoothed by the data provider" in anomalies_js


def test_p4_t06_csv_export_button_admin_guard():
    anomalies_js = (FRONTEND_DIR / "js" / "pages" / "anomalies.js").read_text(encoding="utf-8")
    assert "isAdmin" in anomalies_js
    assert "anomaly-export-btn" in anomalies_js
    assert "handleExportCsv" in anomalies_js


def test_p4_t07_plotly_charts_wiring():
    anomalies_js = (FRONTEND_DIR / "js" / "pages" / "anomalies.js").read_text(encoding="utf-8")
    assert "anomaly-weight-chart" in anomalies_js
    assert "anomaly-cusum-chart" in anomalies_js
    assert "Plotly.newPlot" in anomalies_js


def test_p4_t08_ux_rule5_no_prohibited_styles_in_anomaly_css():
    css = (FRONTEND_DIR / "css" / "pages.css").read_text(encoding="utf-8")
    idx = css.find("Rate-of-Change Weight Anomaly Engine Styles")
    assert idx != -1
    anomaly_css = css[idx:]

    assert "linear-gradient" not in anomaly_css
    assert "radial-gradient" not in anomaly_css
    assert "@keyframes" not in anomaly_css
    assert "animation" not in anomaly_css
    assert "box-shadow" not in anomaly_css
    assert "text-transform: uppercase" not in anomaly_css

    anomalies_js = (FRONTEND_DIR / "js" / "pages" / "anomalies.js").read_text(encoding="utf-8")
    assert "text-transform: uppercase" not in anomalies_js


def test_p4_t09_container_corner_radii_compliance():
    css = (FRONTEND_DIR / "css" / "pages.css").read_text(encoding="utf-8")
    idx = css.find("Rate-of-Change Weight Anomaly Engine Styles")
    anomaly_css = css[idx:]

    radii = re.findall(r"border-radius:\s*(\d+)px", anomaly_css)
    assert len(radii) > 0
    for r in radii:
        assert 4 <= int(r) <= 12, f"Border radius {r}px outside allowable 5-20% design constraint"


def test_p4_t10_severity_badge_colours_palette():
    anomalies_js = (FRONTEND_DIR / "js" / "pages" / "anomalies.js").read_text(encoding="utf-8")
    assert "#7B241C" in anomalies_js  # High
    assert "#B08968" in anomalies_js  # Medium
    assert "#95A5A6" in anomalies_js  # Low
    assert "#2D6A4F" in anomalies_js  # Normal
    assert "#64748B" in anomalies_js  # Not enough data

