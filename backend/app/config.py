from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_ROOT / ".env") if (BACKEND_ROOT / ".env").exists() else None,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"  # development | test | production
    database_backend: str = "auto"  # postgres | duckdb | auto
    duckdb_path: str = "data/dev.duckdb"
    database_url: str = "postgresql://postgres:postgres@localhost:5432/livestock"
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_anon_key: str = ""
    jwt_secret: str = "dev-secret-change-in-production-min-32-chars"
    jwt_expiry_hours: int = 24
    eid_pseudonym_key: str = ""  # required; see app.utils.anonymize.KEY_MISSING_MESSAGE
    email_provider: str = "smtp"  # smtp | resend
    resend_api_key: str = ""
    email_from: str = "reports@yourdomain.com"
    email_dry_run: bool = True
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    cron_secret: str = "dev-cron-secret"
    default_timezone: str = "Australia/Sydney"
    default_farm_id: str = "KF"
    logos_local_path: str = "frontend/assets/logos"
    app_version: str = "1.0.0"

    @property
    def is_production(self) -> bool:
        import os
        return bool(
            self.app_env == "production"
            or os.environ.get("VERCEL")
            or os.environ.get("VERCEL_ENV") == "production"
            or os.environ.get("AWS_LAMBDA_FUNCTION_NAME")
        )

    @property
    def resolved_duckdb_path(self) -> Path:
        p = Path(self.duckdb_path)
        if p.is_absolute():
            return p
        return BACKEND_ROOT / p

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def logos_path(self) -> Path:
        configured = Path(self.logos_local_path)
        if configured.is_absolute() and configured.is_dir():
            return configured
        for base in (PROJECT_ROOT, BACKEND_ROOT, Path.cwd()):
            candidate = (base / self.logos_local_path).resolve()
            if candidate.is_dir():
                return candidate
        return (PROJECT_ROOT / "frontend" / "assets" / "logos").resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
