"""SQLite storage with explicit tables for raw, validated and flag data."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

SCHEMA = """
CREATE TABLE IF NOT EXISTS fetch_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL,
    dataset      TEXT NOT NULL,
    url          TEXT NOT NULL,
    fetched_at   TEXT NOT NULL,
    n_records    INTEGER NOT NULL,
    cache_path   TEXT NOT NULL,
    sha256       TEXT NOT NULL,
    from_cache   INTEGER NOT NULL
);

-- Raw records exactly as returned by the API (selected columns).
CREATE TABLE IF NOT EXISTS raw_co2emis (
    minutes5_utc   TEXT,
    price_area     TEXT,
    co2_emission   REAL,
    run_id         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS raw_declared_emission (
    hour_utc            TEXT,
    price_area          TEXT,
    allocation_method   TEXT,
    version             TEXT,
    co2_per_kwh         REAL,
    run_id              TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS raw_production_consumption (
    hour_utc                TEXT,
    price_area              TEXT,
    gross_consumption_mwh   REAL,
    raw_json                TEXT NOT NULL,
    run_id                  TEXT NOT NULL
);

-- Validated series: every raw row plus inserted gap rows, with quality labels.
CREATE TABLE IF NOT EXISTS co2_5min_validated (
    ts_utc          TEXT NOT NULL,
    price_area      TEXT NOT NULL,
    co2_g_per_kwh   REAL,
    quality         TEXT NOT NULL CHECK (quality IN ('ok','warning','error')),
    flag_rules      TEXT NOT NULL,
    run_id          TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS co2_hourly (
    ts_utc          TEXT NOT NULL,
    price_area      TEXT NOT NULL,
    co2_g_per_kwh   REAL,
    n_samples       INTEGER NOT NULL,
    quality         TEXT NOT NULL CHECK (quality IN ('ok','warning','error')),
    flag_rules      TEXT NOT NULL,
    run_id          TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS co2_declared_hourly (
    ts_utc                  TEXT NOT NULL,
    price_area              TEXT NOT NULL,
    co2_declared_g_per_kwh  REAL,
    version                 TEXT,
    quality                 TEXT NOT NULL CHECK (quality IN ('ok','warning','error')),
    flag_rules              TEXT NOT NULL,
    run_id                  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS consumption_hourly (
    ts_utc                  TEXT NOT NULL,
    price_area              TEXT NOT NULL,
    gross_consumption_mwh   REAL,
    wind_mwh                REAL,
    solar_mwh               REAL,
    quality                 TEXT NOT NULL CHECK (quality IN ('ok','warning','error')),
    flag_rules              TEXT NOT NULL,
    run_id                  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quality_flags (
    dataset     TEXT NOT NULL,
    price_area  TEXT,
    ts_utc      TEXT,
    rule        TEXT NOT NULL,
    severity    TEXT NOT NULL CHECK (severity IN ('warning','error')),
    value       REAL,
    detail      TEXT,
    run_id      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    started_at  TEXT NOT NULL,
    window_start TEXT NOT NULL,
    window_end  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_co2_hourly ON co2_hourly (price_area, ts_utc);
CREATE INDEX IF NOT EXISTS ix_flags ON quality_flags (dataset, rule);
"""

DATA_TABLES = [
    "fetch_log", "raw_co2emis", "raw_declared_emission", "raw_production_consumption", "co2_5min_validated",
    "co2_hourly", "co2_declared_hourly", "consumption_hourly", "quality_flags",
]


def get_engine(db_path: Path | str) -> Engine:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        for stmt in SCHEMA.split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
    return engine


def _iso(frame: pd.DataFrame, cols=("ts_utc",)) -> pd.DataFrame:
    frame = frame.copy()
    for c in cols:
        if c in frame:
            frame[c] = pd.to_datetime(frame[c]).dt.strftime("%Y-%m-%dT%H:%M:%S")
    return frame


def write_run(engine: Engine, run_id: str, started_at: str, start, end, tables: dict[str, pd.DataFrame]) -> None:
    """Replace the database contents with one run. The DB always holds the latest run."""
    with engine.begin() as conn:
        for t in DATA_TABLES + ["runs"]:
            conn.execute(text(f"DELETE FROM {t}"))
        conn.execute(
            text("INSERT INTO runs VALUES (:r, :s, :a, :b)"),
            {"r": run_id, "s": started_at, "a": str(start), "b": str(end)},
        )
        for name, frame in tables.items():
            if name not in DATA_TABLES:
                raise KeyError(name)
            out = _iso(frame).assign(run_id=run_id)
            out.to_sql(name, conn, if_exists="append", index=False)


def read_sql(engine: Engine, sql: str, params: dict | None = None) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn, params=params or {})


def hourly_intensity(engine: Engine, area: str, start: str | None = None, end: str | None = None) -> pd.DataFrame:
    sql = "SELECT ts_utc, price_area, co2_g_per_kwh, n_samples, quality, flag_rules FROM co2_hourly WHERE price_area = :a"
    params = {"a": area}
    if start:
        sql += " AND ts_utc >= :s"
        params["s"] = start
    if end:
        sql += " AND ts_utc < :e"
        params["e"] = end
    df = read_sql(engine, sql + " ORDER BY ts_utc", params)
    df["ts_utc"] = pd.to_datetime(df["ts_utc"])
    return df
