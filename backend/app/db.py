from __future__ import annotations

import contextlib
import logging
import os
import re
from typing import Any, Generator

import psycopg2
import psycopg2.extras
from psycopg2.pool import SimpleConnectionPool

from app.config import get_settings

logger = logging.getLogger(__name__)

_pool: SimpleConnectionPool | None = None
_serverless_conn = None
_duckdb_raw_conn = None
_active_engine: str | None = None
IS_SERVERLESS = bool(os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))

# Written by app/services/forecast_service.py, read by the Prediction page. Valid in DuckDB and PostgreSQL.
# The ALTER upgrades tables made before validated_days existed; their old rows stay NULL there until
# the farm's forecasts are refreshed.
FORECASTS_DDL = """
    CREATE TABLE IF NOT EXISTS forecasts (
        farm_id VARCHAR(50) NOT NULL,
        eid VARCHAR(50) NOT NULL,
        target VARCHAR(50) NOT NULL,
        date DATE NOT NULL,
        pred DOUBLE PRECISION NOT NULL,
        horizon INTEGER NOT NULL,
        tier VARCHAR(10) NOT NULL,
        history_days INTEGER NOT NULL,
        data_through DATE NOT NULL,
        model_version VARCHAR(50) NOT NULL,
        validated_days INTEGER,
        generated_at TIMESTAMP NOT NULL,
        PRIMARY KEY (farm_id, eid, target, date)
    );
    ALTER TABLE forecasts ADD COLUMN IF NOT EXISTS validated_days INTEGER;
"""

FARM_COLUMNS_DDL = """
    CREATE TABLE IF NOT EXISTS farm_columns (
        farm_id VARCHAR(50) NOT NULL,
        column_name VARCHAR(100) NOT NULL,
        kind VARCHAR(20) NOT NULL,
        is_filter BOOLEAN DEFAULT FALSE,
        is_shown BOOLEAN DEFAULT FALSE,
        distinct_count INTEGER DEFAULT 0,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (farm_id, column_name)
    );
    CREATE INDEX IF NOT EXISTS idx_farm_columns_lookup ON farm_columns(farm_id, is_filter, is_shown);
"""

# The upload bookkeeping tables Supabase has (POST /api/admin/farms/{farm_id}/upload writes them),
# for the dev DuckDB only.
DUCKDB_UPLOAD_TABLES_DDL = """
    CREATE SEQUENCE IF NOT EXISTS animal_data_snapshots_id_seq START 1;
    CREATE TABLE IF NOT EXISTS animal_data_snapshots (
        id BIGINT PRIMARY KEY DEFAULT nextval('animal_data_snapshots_id_seq'),
        farm_id VARCHAR(50) NOT NULL,
        snapshot_name VARCHAR NOT NULL,
        record_count INTEGER NOT NULL,
        storage_path VARCHAR,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        created_by VARCHAR
    );
    CREATE TABLE IF NOT EXISTS animal_data_snapshots_data (
        snapshot_id BIGINT NOT NULL,
        farm_id VARCHAR(50) NOT NULL,
        eid VARCHAR(50) NOT NULL,
        date DATE NOT NULL,
        breed VARCHAR(50),
        treatment VARCHAR(100),
        mob VARCHAR(50),
        sex VARCHAR(20),
        weight DOUBLE PRECISION,
        pweight DOUBLE PRECISION,
        growthpbs DOUBLE PRECISION,
        finalpweight DOUBLE PRECISION,
        finalgrowthpbs DOUBLE PRECISION,
        finaldailygrowth DOUBLE PRECISION,
        feedintakekgd DOUBLE PRECISION,
        feedintakepct DOUBLE PRECISION,
        methane DOUBLE PRECISION,
        animalvalue DOUBLE PRECISION,
        animalprod DOUBLE PRECISION,
        feedintakekgdsum DOUBLE PRECISION,
        finalgrowthpbssum DOUBLE PRECISION,
        animalprodsum DOUBLE PRECISION,
        methanesum DOUBLE PRECISION,
        methanesupplsum DOUBLE PRECISION,
        carcassweight DOUBLE PRECISION,
        dressedcarcass DOUBLE PRECISION
    );
    CREATE INDEX IF NOT EXISTS idx_snapshots_data_id ON animal_data_snapshots_data (snapshot_id);
    CREATE SEQUENCE IF NOT EXISTS data_uploads_id_seq START 1;
    CREATE TABLE IF NOT EXISTS data_uploads (
        id BIGINT PRIMARY KEY DEFAULT nextval('data_uploads_id_seq'),
        farm_id VARCHAR(50) NOT NULL,
        filename VARCHAR NOT NULL,
        rows_inserted INTEGER DEFAULT 0,
        rows_skipped INTEGER DEFAULT 0,
        rows_overwritten INTEGER DEFAULT 0,
        duplicate_mode VARCHAR,
        storage_path VARCHAR,
        uploaded_by VARCHAR,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
"""

POSTGRES_BASE_SCHEMA_DDL = """
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        username VARCHAR(50) UNIQUE NOT NULL,
        password_hash VARCHAR(255) NOT NULL,
        role VARCHAR(20) DEFAULT 'user',
        is_admin BOOLEAN DEFAULT FALSE,
        is_active BOOLEAN DEFAULT TRUE,
        must_change_password BOOLEAN DEFAULT FALSE,
        last_login TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS farms (
        farm_id VARCHAR(50) PRIMARY KEY,
        farm_name VARCHAR(100) NOT NULL,
        slug VARCHAR(100) UNIQUE,
        is_active BOOLEAN DEFAULT TRUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS user_farm_access (
        user_id INTEGER NOT NULL,
        farm_id VARCHAR(50) NOT NULL,
        PRIMARY KEY (user_id, farm_id)
    );

    CREATE OR REPLACE VIEW user_farms AS 
    SELECT user_id, farm_id FROM user_farm_access;

    CREATE TABLE IF NOT EXISTS animal_data (
        farm_id VARCHAR(50) NOT NULL,
        eid VARCHAR(50) NOT NULL,
        date DATE NOT NULL,
        breed VARCHAR(50),
        treatment VARCHAR(100),
        mob VARCHAR(50),
        sex VARCHAR(20) DEFAULT 'Unknown',
        weight DOUBLE PRECISION,
        pweight DOUBLE PRECISION,
        growthpbs DOUBLE PRECISION,
        finalpweight DOUBLE PRECISION,
        finalgrowthpbs DOUBLE PRECISION,
        finaldailygrowth DOUBLE PRECISION,
        feedintakekgd DOUBLE PRECISION,
        feedintakepct DOUBLE PRECISION,
        methane DOUBLE PRECISION,
        animalvalue DOUBLE PRECISION,
        animalprod DOUBLE PRECISION,
        feedintakekgdsum DOUBLE PRECISION,
        finalgrowthpbssum DOUBLE PRECISION,
        animalprodsum DOUBLE PRECISION,
        methanesum DOUBLE PRECISION,
        methanesupplsum DOUBLE PRECISION,
        carcassweight DOUBLE PRECISION,
        dressedcarcass DOUBLE PRECISION,
        PRIMARY KEY (farm_id, eid, date)
    );
    CREATE INDEX IF NOT EXISTS idx_animal_data_farm_date ON animal_data (farm_id, date);
    CREATE INDEX IF NOT EXISTS idx_animal_data_farm_eid ON animal_data (farm_id, eid);

    CREATE TABLE IF NOT EXISTS data_uploads (
        id SERIAL PRIMARY KEY,
        farm_id VARCHAR(50) NOT NULL,
        filename VARCHAR NOT NULL,
        rows_inserted INTEGER DEFAULT 0,
        rows_skipped INTEGER DEFAULT 0,
        rows_overwritten INTEGER DEFAULT 0,
        duplicate_mode VARCHAR,
        storage_path VARCHAR,
        uploaded_by VARCHAR,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS animal_data_snapshots (
        id SERIAL PRIMARY KEY,
        farm_id VARCHAR(50) NOT NULL,
        snapshot_name VARCHAR NOT NULL,
        record_count INTEGER NOT NULL,
        storage_path VARCHAR,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        created_by VARCHAR
    );

    CREATE TABLE IF NOT EXISTS animal_data_snapshots_data (
        snapshot_id BIGINT NOT NULL,
        farm_id VARCHAR(50) NOT NULL,
        eid VARCHAR(50) NOT NULL,
        date DATE NOT NULL,
        breed VARCHAR(50),
        treatment VARCHAR(100),
        mob VARCHAR(50),
        sex VARCHAR(20),
        weight DOUBLE PRECISION,
        pweight DOUBLE PRECISION,
        growthpbs DOUBLE PRECISION,
        finalpweight DOUBLE PRECISION,
        finalgrowthpbs DOUBLE PRECISION,
        finaldailygrowth DOUBLE PRECISION,
        feedintakekgd DOUBLE PRECISION,
        feedintakepct DOUBLE PRECISION,
        methane DOUBLE PRECISION,
        animalvalue DOUBLE PRECISION,
        animalprod DOUBLE PRECISION,
        feedintakekgdsum DOUBLE PRECISION,
        finalgrowthpbssum DOUBLE PRECISION,
        animalprodsum DOUBLE PRECISION,
        methanesum DOUBLE PRECISION,
        methanesupplsum DOUBLE PRECISION,
        carcassweight DOUBLE PRECISION,
        dressedcarcass DOUBLE PRECISION
    );
    CREATE INDEX IF NOT EXISTS idx_snapshots_data_id ON animal_data_snapshots_data (snapshot_id);

    CREATE TABLE IF NOT EXISTS logo_assets (
        farm_id VARCHAR(50) NOT NULL,
        filename VARCHAR(200) NOT NULL,
        content_type VARCHAR(100) NOT NULL,
        data BYTEA NOT NULL,
        PRIMARY KEY (farm_id, filename)
    );

    CREATE TABLE IF NOT EXISTS email_schedules (
        id SERIAL PRIMARY KEY,
        farm_id VARCHAR(50),
        recipient_email VARCHAR(255) NOT NULL,
        schedule_name VARCHAR(255),
        frequency VARCHAR(50) NOT NULL,
        send_time TIME NOT NULL,
        send_date DATE,
        day_of_week INTEGER,
        day_of_month INTEGER,
        is_active BOOLEAN DEFAULT TRUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        next_send_at TIMESTAMP,
        last_sent TIMESTAMP,
        email_subject VARCHAR(500),
        email_body TEXT,
        report_filters TEXT,
        report_charts VARCHAR(500),
        report_format VARCHAR(50) DEFAULT 'pdf',
        created_by VARCHAR(100)
    );

    CREATE TABLE IF NOT EXISTS user_credentials_history (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL,
        username VARCHAR(100) NOT NULL,
        role VARCHAR(20) NOT NULL,
        password_hash TEXT NOT NULL,
        must_change_password BOOLEAN DEFAULT FALSE,
        action VARCHAR(50) NOT NULL,
        changed_by VARCHAR(100) NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_uch_user_id ON user_credentials_history (user_id);
"""



def _convert_pg_to_duckdb_sql(query: str) -> str:
    """Convert PostgreSQL %s positional placeholders and dialect quirks to DuckDB."""
    # Match %s not preceded by %
    sql = re.sub(r"(?<!%)%s", "?", query)
    sql = sql.replace("%%", "%")
    # Translate PostgreSQL to_char(col, 'YYYY-MM-DD') -> CAST(col AS VARCHAR)
    sql = re.sub(r"to_char\(([^,]+),\s*['\"]YYYY-MM-DD['\"]\)", r"CAST(\1 AS VARCHAR)", sql, flags=re.IGNORECASE)
    return sql


class _DuckDBCursorWrapper:
    def __init__(self, raw_cursor: Any) -> None:
        self._cur = raw_cursor

    def execute(self, query: str, params: tuple | list | None = None) -> Any:
        sql = _convert_pg_to_duckdb_sql(query)
        try:
            return self._cur.execute(sql, params or ())
        except Exception as exc:
            # DuckDB invalidates the whole database after a fatal error; without a reset
            # every later request (even login) 500s until the server is restarted.
            if type(exc).__name__ == "FatalException":
                logger.critical("DuckDB fatal error; resetting the shared connection: %s", exc)
                reset_active_engine()
            raise

    def fetchone(self) -> dict | None:
        if not self._cur.description:
            return None
        cols = [d[0] for d in self._cur.description]
        row = self._cur.fetchone()
        return dict(zip(cols, row)) if row else None

    def fetchall(self) -> list[dict]:
        if not self._cur.description:
            return []
        cols = [d[0] for d in self._cur.description]
        return [dict(zip(cols, row)) for row in self._cur.fetchall()]

    @property
    def rowcount(self) -> int:
        return getattr(self._cur, "rowcount", -1)

    def __enter__(self) -> _DuckDBCursorWrapper:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cur, name)


class _DuckDBConnectionWrapper:
    def __init__(self, raw_conn: Any) -> None:
        self._conn = raw_conn

    def cursor(self, *args: Any, **kwargs: Any) -> _DuckDBCursorWrapper:
        return _DuckDBCursorWrapper(self._conn.cursor())

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


def _connect_postgres():
    settings = get_settings()
    return psycopg2.connect(
        settings.database_url,
        connect_timeout=3,
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=3,
    )


def get_pool() -> SimpleConnectionPool:
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = SimpleConnectionPool(1, 5, settings.database_url, connect_timeout=5)
    return _pool


def _ensure_duckdb_schema(raw_conn: Any) -> None:
    try:
        cur = raw_conn.cursor()
        tables = [r[0] for r in cur.execute("SHOW TABLES").fetchall()]
        if "email_schedules" in tables:
            cols = {r[0].lower() for r in cur.execute("DESCRIBE email_schedules").fetchall()}
            required_cols = {
                "farm_id": "VARCHAR(50)",
                "next_send_at": "TIMESTAMP",
                "report_charts": "VARCHAR(500)",
                "report_format": "VARCHAR(50) DEFAULT 'pdf'",
            }
            for col_name, col_type in required_cols.items():
                if col_name not in cols:
                    try:
                        cur.execute(f"ALTER TABLE email_schedules ADD COLUMN {col_name} {col_type}")
                    except Exception as err:
                        logger.warning("Could not add column %s to email_schedules: %s", col_name, err)
        if "users" in tables:
            cols = {r[0].lower() for r in cur.execute("DESCRIBE users").fetchall()}
            if "role" not in cols:
                try:
                    cur.execute("ALTER TABLE users ADD COLUMN role VARCHAR(20) DEFAULT 'user'")
                    cur.execute("UPDATE users SET role = 'owner', is_admin = FALSE WHERE lower(username) = 'owner'")
                    cur.execute("UPDATE users SET role = 'admin', is_admin = TRUE WHERE is_admin = TRUE AND lower(username) <> 'owner'")
                except Exception as err:
                    logger.warning("Could not add role column to users: %s", err)
            if "last_login" not in cols:
                try:
                    cur.execute("ALTER TABLE users ADD COLUMN last_login TIMESTAMP")
                except Exception as err:
                    logger.warning("Could not add last_login column to users: %s", err)
            if "is_active" not in cols:
                try:
                    cur.execute("ALTER TABLE users ADD COLUMN is_active BOOLEAN DEFAULT TRUE")
                except Exception as err:
                    logger.warning("Could not add is_active column to users: %s", err)
            if "must_change_password" not in cols:
                try:
                    cur.execute("ALTER TABLE users ADD COLUMN must_change_password BOOLEAN DEFAULT FALSE")
                except Exception as err:
                    logger.warning("Could not add must_change_password column to users: %s", err)
            try:
                cur.execute("UPDATE users SET is_admin = FALSE WHERE lower(username) <> 'admin'")
                cur.execute("UPDATE users SET is_admin = TRUE, is_active = TRUE WHERE lower(username) = 'admin'")
            except Exception as err:
                logger.warning("Could not enforce single admin seed: %s", err)
        if "user_farm_access" in tables:
            try:
                cur.execute("CREATE VIEW IF NOT EXISTS user_farms AS SELECT user_id, farm_id FROM user_farm_access")
            except Exception as err:
                logger.warning("Could not create user_farms view: %s", err)
        cur.execute(FORECASTS_DDL)
        cur.execute(DUCKDB_UPLOAD_TABLES_DDL)
        cur.execute(FARM_COLUMNS_DDL)
        cur.execute("""
            CREATE SEQUENCE IF NOT EXISTS user_credentials_history_id_seq START 1;
            CREATE TABLE IF NOT EXISTS user_credentials_history (
                id INTEGER PRIMARY KEY DEFAULT nextval('user_credentials_history_id_seq'),
                user_id INTEGER,
                username VARCHAR,
                role VARCHAR,
                password_hash VARCHAR,
                must_change_password BOOLEAN DEFAULT FALSE,
                action VARCHAR,
                changed_by VARCHAR,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        if "animal_data" in tables:
            try:
                from app.services.variable_service import VariableService

                fids = [
                    r[0]
                    for r in cur.execute(
                        "SELECT DISTINCT farm_id FROM animal_data WHERE farm_id IS NOT NULL"
                    ).fetchall()
                ]
                for fid in fids:
                    if fid:
                        has_cols = cur.execute(
                            "SELECT 1 FROM farm_columns WHERE farm_id = ? LIMIT 1", [fid]
                        ).fetchone()
                        if not has_cols:
                            VariableService.seed_farm_columns_if_missing(fid)
            except Exception as seed_err:
                logger.warning("Could not seed default farm_columns: %s", seed_err)
    except Exception as exc:
        logger.warning("Error verifying DuckDB schema: %s", exc)


def _get_duckdb_conn() -> _DuckDBConnectionWrapper:
    global _duckdb_raw_conn
    if _duckdb_raw_conn is None:
        import duckdb

        settings = get_settings()
        db_path = settings.resolved_duckdb_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _duckdb_raw_conn = duckdb.connect(str(db_path), read_only=False)
        _ensure_duckdb_schema(_duckdb_raw_conn)
    return _DuckDBConnectionWrapper(_duckdb_raw_conn)


def get_active_engine() -> str:
    """Resolve the active database backend ('postgres' or 'duckdb')."""
    global _active_engine
    if _active_engine is not None:
        return _active_engine

    settings = get_settings()

    # Strict Production Guard: DuckDB prohibited in production/serverless
    if settings.is_production:
        if settings.database_backend == "duckdb":
            raise RuntimeError(
                "CRITICAL: DuckDB embedded database is prohibited in production/serverless environments. "
                "Configure DATABASE_URL with a valid remote PostgreSQL/Supabase connection."
            )
        _active_engine = "postgres"
        return _active_engine

    if settings.database_backend in ("duckdb", "postgres"):
        _active_engine = settings.database_backend
        return _active_engine

    # Auto mode in development: attempt PostgreSQL first, fall back to DuckDB on failure
    try:
        test_conn = _connect_postgres()
        test_conn.close()
        _active_engine = "postgres"
    except Exception as exc:
        logger.warning(
            "PostgreSQL/Supabase unavailable (%s). Falling back to local embedded DuckDB (%s).",
            exc,
            settings.resolved_duckdb_path,
        )
        _active_engine = "duckdb"

    return _active_engine


def reset_active_engine() -> None:
    """Reset cached active engine for dynamic environment switching in tests."""
    global _active_engine, _duckdb_raw_conn, _pg_schema_checked
    _active_engine = None
    _pg_schema_checked = False
    if _duckdb_raw_conn is not None:
        with contextlib.suppress(Exception):
            _duckdb_raw_conn.close()
        _duckdb_raw_conn = None


_pg_schema_checked = False


def _ensure_postgres_schema(conn: Any) -> None:
    global _pg_schema_checked
    if _pg_schema_checked:
        return

    # 1. Base DDL execution (idempotent table creation)
    try:
        with conn.cursor() as cur:
            cur.execute(POSTGRES_BASE_SCHEMA_DDL)
        conn.commit()
    except Exception as exc:
        logger.warning("PostgreSQL base schema DDL notice: %s", exc)
        with contextlib.suppress(Exception):
            conn.rollback()

    # 2. Users table migration columns
    try:
        with conn.cursor() as cur:
            cur.execute("""
                ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(20) DEFAULT 'user';
                ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;
                ALTER TABLE users ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN DEFAULT FALSE;
                ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login TIMESTAMP;
                UPDATE users SET role = 'owner', is_admin = FALSE WHERE lower(username) = 'owner' AND (role IS NULL OR role = 'user');
                UPDATE users SET role = 'admin', is_admin = TRUE WHERE lower(username) = 'admin' AND (role IS NULL OR role = 'user');
            """)
        conn.commit()
    except Exception as exc:
        logger.warning("PostgreSQL users schema migration notice: %s", exc)
        with contextlib.suppress(Exception):
            conn.rollback()

    # 3. user_farms view
    try:
        with conn.cursor() as cur:
            cur.execute("""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.tables 
                        WHERE table_schema = 'public' AND table_name = 'user_farm_access'
                    ) THEN
                        CREATE OR REPLACE VIEW user_farms AS 
                        SELECT user_id, farm_id FROM user_farm_access;
                    END IF;
                END $$;
            """)
        conn.commit()
    except Exception as exc:
        logger.warning("PostgreSQL user_farms view migration notice: %s", exc)
        with contextlib.suppress(Exception):
            conn.rollback()

    # 4. Forecasts DDL
    try:
        with conn.cursor() as cur:
            cur.execute(FORECASTS_DDL)
        conn.commit()
    except Exception as exc:
        logger.warning("PostgreSQL forecasts DDL notice: %s", exc)
        with contextlib.suppress(Exception):
            conn.rollback()

    # 5. Farm columns DDL
    try:
        with conn.cursor() as cur:
            cur.execute(FARM_COLUMNS_DDL)
        conn.commit()
    except Exception as exc:
        logger.warning("PostgreSQL farm_columns DDL notice: %s", exc)
        with contextlib.suppress(Exception):
            conn.rollback()

    # 6. Cold-Start Auto-Seed: If users table is completely empty, provision default admin & baseline farms
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM users;")
            row = cur.fetchone()
            user_count = row[0] if row else 0
            if user_count == 0:
                from app.auth.passwords import hash_password
                admin_hash = hash_password("admin123")
                cur.execute("""
                    INSERT INTO users (username, password_hash, role, is_admin, is_active, must_change_password)
                    VALUES ('admin', %s, 'admin', TRUE, TRUE, TRUE)
                    RETURNING id;
                """, (admin_hash,))
                admin_id = cur.fetchone()[0]
                cur.execute("""
                    INSERT INTO farms (farm_id, farm_name, slug) VALUES 
                    ('KF', 'Killara Feedlot', 'killara-feedlot'),
                    ('WOW', 'WOW Feedlot Killara', 'wow-feedlot-killara')
                    ON CONFLICT (farm_id) DO NOTHING;
                """)
                cur.execute("""
                    INSERT INTO user_farm_access (user_id, farm_id) VALUES 
                    (%s, 'KF'),
                    (%s, 'WOW')
                    ON CONFLICT DO NOTHING;
                """, (admin_id, admin_id))
                logger.info("Cold-start bootstrap: seeded default admin (must_change_password=True) and baseline farms KF, WOW")
        conn.commit()
    except Exception as exc:
        logger.warning("PostgreSQL cold-start auto-seed notice: %s", exc)
        with contextlib.suppress(Exception):
            conn.rollback()

    _pg_schema_checked = True


def _reset_serverless_conn() -> None:
    global _serverless_conn
    if _serverless_conn is not None:
        with contextlib.suppress(Exception):
            _serverless_conn.close()
        _serverless_conn = None


@contextlib.contextmanager
def get_conn() -> Generator[Any, None, None]:
    engine = get_active_engine()
    if engine == "duckdb":
        yield _get_duckdb_conn()
        return

    if IS_SERVERLESS:
        global _serverless_conn
        if _serverless_conn is None or _serverless_conn.closed:
            _serverless_conn = _connect_postgres()
            _ensure_postgres_schema(_serverless_conn)
        conn = _serverless_conn
        try:
            yield conn
            conn.commit()
        except Exception:
            with contextlib.suppress(Exception):
                conn.rollback()
            _reset_serverless_conn()
            raise
        return

    pool = get_pool()
    conn = pool.getconn()
    _ensure_postgres_schema(conn)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def fetch_all(query: str, params: tuple | list | dict | None = None) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params)
            return [dict(row) for row in cur.fetchall()]


def fetch_one(query: str, params: tuple | list | dict | None = None) -> dict | None:
    rows = fetch_all(query, params)
    return rows[0] if rows else None


def execute(query: str, params: tuple | list | dict | None = None) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return getattr(cur, "rowcount", 1)


def table_column_types(table: str) -> list[dict]:
    """A table's columns and SQL types ({column_name, data_type}) in table order."""
    schema_filter = "" if get_active_engine() == "duckdb" else " AND table_schema = 'public'"
    return fetch_all(
        f"""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE lower(table_name) = %s{schema_filter}
        ORDER BY ordinal_position
        """,
        (table.lower(),),
    )


def execute_returning(query: str, params: tuple | list | dict | None = None) -> dict | None:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params)
            row = cur.fetchone()
            return dict(row) if row else None


def user_has_farm(user_id: int, farm_id: str) -> bool:
    try:
        u = fetch_one("SELECT is_admin, role FROM users WHERE id = %s", (user_id,))
    except Exception:
        u = fetch_one("SELECT is_admin FROM users WHERE id = %s", (user_id,))
    if u and (u.get("is_admin") or (u.get("role") or "").lower() == "admin"):
        return True
    row = fetch_one(
        "SELECT 1 FROM user_farm_access WHERE user_id = %s AND farm_id = %s",
        (user_id, farm_id),
    )
    return row is not None


def get_user_farms(user_id: int) -> list[dict]:
    try:
        u = fetch_one("SELECT is_admin, role FROM users WHERE id = %s", (user_id,))
    except Exception:
        u = fetch_one("SELECT is_admin FROM users WHERE id = %s", (user_id,))
    if u and (u.get("is_admin") or (u.get("role") or "").lower() == "admin"):
        return fetch_all(
            "SELECT f.farm_id, f.farm_name, f.slug FROM farms f WHERE f.is_active = TRUE ORDER BY f.farm_name"
        )
    return fetch_all(
        """
        SELECT f.farm_id, f.farm_name, f.slug
        FROM farms f
        JOIN user_farm_access ufa ON ufa.farm_id = f.farm_id
        WHERE ufa.user_id = %s AND f.is_active = TRUE
        ORDER BY f.farm_name
        """,
        (user_id,),
    )


def record_user_login(user_id: int) -> None:
    """Record user login timestamp."""
    try:
        execute("UPDATE users SET last_login = NOW() WHERE id = %s", (user_id,))
    except Exception as exc:
        logger.warning("Could not record user login: %s", exc)


def record_credential_history(user_id: int, action: str, changed_by: str) -> None:
    """Record prior credential state to user_credentials_history before mutation."""
    try:
        user = fetch_one(
            "SELECT id, username, role, is_admin, password_hash, must_change_password FROM users WHERE id = %s",
            (user_id,),
        )
        if not user:
            return
        role = user.get("role") or ("admin" if user.get("is_admin") else "user")
        must_change = bool(user.get("must_change_password", False))
        execute(
            """
            INSERT INTO user_credentials_history (user_id, username, role, password_hash, must_change_password, action, changed_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (user["id"], user["username"], role, user["password_hash"], must_change, action, changed_by),
        )
    except Exception as exc:
        logger.warning("Could not record credential history for user %s: %s", user_id, exc)
