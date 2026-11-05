"""Lightweight label helpers — no pandas (safe for Vercel cold auth/chart paths)."""
from __future__ import annotations

from typing import Any

from app.constants import MEASURE_LABELS, MEASURE_UNITS


def format_measure_display(measure: str) -> str:
    """Format measure display name ensuring unit token is attached exactly once."""
    base, unit = extract_measure_label_and_unit(measure)
    if not unit:
        return base
    return f"{base} ({unit})"


def extract_measure_label_and_unit(measure: str) -> tuple[str, str]:
    """Extract clean base measure label and unit suffix without duplicates."""
    import re

    raw_label = MEASURE_LABELS.get(measure, measure)
    unit = MEASURE_UNITS.get(measure, "")
    m = re.search(r"^(.*?)(?:\s*\((.*?)\))?$", raw_label)
    if m and m.group(2):
        return m.group(1).strip(), m.group(2).strip()
    return raw_label, unit


def clean_group_label(raw_label: str) -> str:
    """Format comparison group labels by stripping redundant dimension prefixes and default Overall tokens."""
    if not raw_label:
        return "Overall Herd"
    if raw_label in ("Overall", "Overall Average") or raw_label.startswith("Overall Average"):
        return "Overall Herd (Benchmark)"

    delims = [",", "|"]
    parts = [raw_label]
    for d in delims:
        new_parts = []
        for p in parts:
            new_parts.extend(p.split(d))
        parts = new_parts

    cleaned = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if ":" in p:
            k, v = p.split(":", 1)
            k = k.strip()
            v = v.strip()
            if v in ("Overall", "*****", "None"):
                continue
            cleaned.append(v)
        else:
            if p not in ("Overall", "*****"):
                cleaned.append(p)

    if not cleaned:
        return "Overall Herd"
    return " • ".join(cleaned)


def friendly_label(col: str) -> str:
    if col in ("treatment", "treatment_display"):
        return "Treatment"
    if col == "eid":
        return "EID"
    if col == "date":
        return "Date"
    if col in MEASURE_LABELS:
        return MEASURE_LABELS[col]
    return col.replace("_", " ").title()


def treatment_display(val: Any) -> str:
    if val is None:
        return "No Treatment"
    try:
        if val != val:  # NaN
            return "No Treatment"
    except Exception:
        pass
    return str(val)


def label_from_combo(combo: dict, varying_dims: list[str], all_overall: bool) -> str:
    if all_overall or not varying_dims:
        if all(v == "Overall" for v in combo.values()):
            return "Overall Average"
        parts = [
            f"{friendly_label(d)}: {combo[d]}"
            for d in ("sex", "treatment", "breed", "mob", "eid")
            if combo.get(d) and combo[d] != "Overall"
        ]
        return " | ".join(parts) if parts else "Overall Average"
    return " | ".join(f"{friendly_label(d)}: {combo[d]}" for d in varying_dims)


def full_label_from_combo(combo: dict, is_admin: bool) -> str:
    parts = []
    for col in ("sex", "treatment", "breed", "mob"):
        parts.append(f"{friendly_label(col)}: {combo.get(col, 'Overall')}")
    eid = combo.get("eid", "Overall") if is_admin else "*****"
    parts.append(f"EID: {eid}")
    return ", ".join(parts)
