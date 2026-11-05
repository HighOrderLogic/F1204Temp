from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# Measures and extra filter keys name animal_data columns; which ones a farm may use is decided
# by its admin-configured farm_columns (see _restrict_to_farm_columns).
_COLUMN_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")


class FilterState(BaseModel):
    farm_id: str
    year: int
    month: str = "All"
    day: str | int = "All"
    sex: list[str] = Field(default_factory=lambda: ["Overall"])
    treatment: list[str] = Field(default_factory=lambda: ["Overall"])
    breed: list[str] = Field(default_factory=lambda: ["Overall"])
    mob: list[str] = Field(default_factory=lambda: ["Overall"])
    eid: list[str] = Field(default_factory=lambda: ["Overall"])
    # Admin-enabled categorical columns beyond the built-in four: column -> selected values.
    # They narrow the data only; charts don't split into comparison groups by them.
    extra_filters: dict[str, list[str]] = Field(default_factory=dict)
    measure: str = "finalpweight"
    measures: list[str] = Field(default_factory=lambda: ["finalpweight"])
    cohort_percentile: int = 10
    active_only: bool = False
    active_days: int = 30
    active_reference_mode: Literal["auto", "max", "today"] = "auto"

    @field_validator("active_days", mode="before")
    @classmethod
    def validate_active_days(cls, v: Any) -> int:
        try:
            val = int(v)
            return val if val in {7, 14, 30} else 30
        except (ValueError, TypeError):
            return 30

    @field_validator("active_reference_mode", mode="before")
    @classmethod
    def validate_ref_mode(cls, v: Any) -> str:
        s = str(v or "auto").lower()
        return s if s in {"auto", "max", "today"} else "auto"

    @model_validator(mode="before")
    @classmethod
    def _sync_measures(cls, data: Any) -> Any:
        if isinstance(data, dict):
            raw_measures = data.get("measures")
            raw_measure = data.get("measure")

            if isinstance(raw_measures, list):
                # Filter valid measures preserving order, without duplicates
                seen = set()
                valid = []
                for m in raw_measures:
                    if isinstance(m, str) and _COLUMN_NAME.match(m) and m not in seen:
                        valid.append(m)
                        seen.add(m)
                data["measures"] = valid
            elif raw_measure and isinstance(raw_measure, str) and _COLUMN_NAME.match(raw_measure):
                data["measures"] = [raw_measure]
            else:
                data["measures"] = []

            raw_extra = data.get("extra_filters")
            data["extra_filters"] = {
                k: v
                for k, v in (raw_extra.items() if isinstance(raw_extra, dict) else [])
                if isinstance(k, str) and _COLUMN_NAME.match(k) and isinstance(v, list)
            }
            data = cls._restrict_to_farm_columns(data)
        return data

    @staticmethod
    def _restrict_to_farm_columns(data: dict) -> dict:
        """Apply the farm's admin-enabled filters/measures; empty measures get the farm default."""
        from app.services.variable_service import VariableService

        if data.get("farm_id"):
            return VariableService.restrict_filter_values(data)
        data["measures"] = data["measures"] or ["finalpweight"]
        data["measure"] = data["measures"][0]
        return data


class LoginRequest(BaseModel):
    username: str
    password: str


class FarmInfo(BaseModel):
    farm_id: str
    farm_name: str
    slug: str


class UserInfo(BaseModel):
    id: int
    username: str
    is_admin: bool
    role: str = 'user'
    must_change_password: bool = False
    farms: list[FarmInfo]


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserInfo


class DataQueryRequest(FilterState):
    include_rows: bool = True
    page: int | None = None
    page_size: int | None = None


class TimeseriesRequest(FilterState):
    point_size: int = 3
    show_smooth: bool = False


class DistributionRequest(FilterState):
    hist_bins: int = 20


class CohortRequest(FilterState):
    percentile: int = 10


class CustomChartRequest(FilterState):
    chart_type: str = "line"
    title: str = "Custom Chart"
    x_col: str = "date"
    y_col: str = "finalpweight"
    group_col: str | None = "sex"
    agg_fun: str = "mean"
    smooth: bool = False
    bar_position: str = "stack"
    hist_bins: int = 20


class EmailScheduleCreate(BaseModel):
    farm_id: str
    recipient_email: str
    schedule_name: str | None = None
    frequency: str
    send_time: str = "09:00"
    send_date: str | None = None
    day_of_week: int | None = None
    day_of_month: int | None = None
    email_subject: str = "Automated Livestock Report"
    email_body: str = ""
    report_filters: dict[str, Any] | None = None
    report_charts: list[str] = Field(default_factory=lambda: ["Distribution", "Summary Statistics"])
    report_format: str = "PDF"


class EmailSendNow(BaseModel):
    farm_id: str
    recipient_email: str
    email_subject: str = "Automated Livestock Report"
    email_body: str = ""
    report_filters: dict[str, Any] | None = None
    report_charts: list[str] = Field(default_factory=lambda: ["Distribution", "Summary Statistics"])
    report_format: str = "PDF"


class ReportGenerateRequest(BaseModel):
    farm_id: str
    filters: FilterState
    filename: str = "livestock_report"
    format: str = "PDF"
    charts: list[str] = Field(default_factory=lambda: ["Time Series", "Distribution", "Summary Statistics"])


class ChartPngRequest(BaseModel):
    chart_source: str
    filters: FilterState


class FarmCreate(BaseModel):
    farm_id: str
    farm_name: str
    slug: str


class PasswordUpdate(BaseModel):
    user_id: int
    new_password: str


class UsernameUpdate(BaseModel):
    user_id: int
    new_username: str


class UserStatusUpdate(BaseModel):
    is_active: bool


class UserRoleUpdate(BaseModel):
    role: Literal["user", "owner"]


class UserFarmsUpdate(BaseModel):
    farm_ids: list[str]


class UserCreate(BaseModel):
    username: str
    password: str
    is_admin: bool = False
    role: str | None = None
    farm_ids: list[str] = Field(default_factory=list)


class SchedulePatch(BaseModel):
    is_active: bool | None = None


class AnomalyAnalyzeRequest(FilterState):
    weight_col: Literal["weight", "pweight"] = "weight"
    sensitivity: Literal["High", "Medium", "Low"] = "Medium"


class AnomalyDetailRequest(AnomalyAnalyzeRequest):
    animal: str


class ColumnConfigItem(BaseModel):
    column_name: str
    kind: Literal["id", "date", "numeric", "categorical", "other"]
    is_filter: bool = False
    is_shown: bool = False
    distinct_count: int = 0
    is_reserved: bool = False


class FarmColumnsResponse(BaseModel):
    farm_id: str
    filters_count: int
    measures_count: int
    active_filters: list[str]
    active_measures: list[str]
    columns: list[ColumnConfigItem]


class FarmColumnsUpdate(BaseModel):
    columns: list[ColumnConfigItem]

class RotateInitialPasswordRequest(BaseModel):
    new_password: str
