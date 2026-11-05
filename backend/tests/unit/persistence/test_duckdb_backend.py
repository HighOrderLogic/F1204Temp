from __future__ import annotations

import pytest
from app.db import _convert_pg_to_duckdb_sql, get_active_engine, reset_active_engine


@pytest.mark.unit
def test_duckdb_query_conversion_placeholders():
    """Verifies %s conversion to ? while preserving % wildcards and translating to_char."""
    sql = "SELECT to_char(date, 'YYYY-MM-DD') AS d FROM animal_data WHERE farm_id = %s AND breed LIKE '%Angus%' AND date >= %s::date"
    converted = _convert_pg_to_duckdb_sql(sql)
    assert converted == "SELECT CAST(date AS VARCHAR) AS d FROM animal_data WHERE farm_id = ? AND breed LIKE '%Angus%' AND date >= ?::date"


@pytest.mark.unit
def test_duckdb_production_guard_raises(monkeypatch):
    """Verifies strict production guard prohibiting DuckDB in serverless/production."""
    from app.config import get_settings

    get_settings.cache_clear()
    reset_active_engine()
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("DATABASE_BACKEND", "duckdb")

    try:
        with pytest.raises(RuntimeError) as exc_info:
            get_active_engine()

        assert "CRITICAL: DuckDB embedded database is prohibited in production" in str(exc_info.value)
    finally:
        reset_active_engine()
        get_settings.cache_clear()
