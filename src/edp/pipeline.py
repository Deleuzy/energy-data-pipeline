"""The pipeline steps as plain functions, shared by the CLI and the Dagster assets."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from edp import fetch, report, scope2, store, validate

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Settings:
    start: str = "2026-10-01T00:00"
    end: str = "2026-10-15T00:00"
    db_path: str = str(ROOT / "data" / "energy.db")
    cache_dir: str = str(ROOT / "data" / "raw")
    report_dir: str = str(ROOT / "reports")
    site_csv: str = str(ROOT / "examples" / "synthetic_site_hourly.csv")
    site_area: str = "DK1"
    offline: bool = False
    refresh: bool = False

    @property
    def window(self) -> tuple[pd.Timestamp, pd.Timestamp]:
        return pd.Timestamp(self.start), pd.Timestamp(self.end)


def fetch_dataset(s: Settings, dataset: str) -> fetch.FetchResult:
    client = fetch.EnergiDataServiceClient(s.cache_dir, offline=s.offline)
    return client.fetch(dataset, s.start, s.end, refresh=s.refresh)


def validate_all(co2: fetch.FetchResult, cons: fetch.FetchResult, s: Settings) -> dict:
    start, end = s.window
    five, hourly = validate.validate_co2(co2.frame, start, end)
    consumption = validate.validate_consumption(cons.frame, start, end)
    flags = pd.concat([five.flags, hourly.flags, consumption.flags], ignore_index=True)
    return {
        "co2_5min": five.rows,
        "co2_hourly": hourly.rows,
        "consumption_hourly": consumption.rows,
        "flags": flags,
    }


def store_all(co2: fetch.FetchResult, cons: fetch.FetchResult, validated: dict, s: Settings) -> str:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    fetch_log = pd.DataFrame([asdict(p) for p in co2.pages + cons.pages])
    raw_co2 = co2.frame.rename(
        columns={"Minutes5UTC": "minutes5_utc", "PriceArea": "price_area", "CO2Emission": "co2_emission"}
    )
    raw_cons = pd.DataFrame(
        {
            "hour_utc": cons.frame["HourUTC"],
            "price_area": cons.frame["PriceArea"],
            "gross_consumption_mwh": cons.frame["GrossConsumptionMWh"],
            "raw_json": [json.dumps(r) for r in cons.frame.to_dict("records")],
        }
    )
    engine = store.get_engine(s.db_path)
    store.write_run(
        engine, run_id, started, s.start, s.end,
        {
            "fetch_log": fetch_log,
            "raw_co2emis": raw_co2,
            "raw_production_consumption": raw_cons,
            "co2_5min_validated": validated["co2_5min"],
            "co2_hourly": validated["co2_hourly"],
            "consumption_hourly": validated["consumption_hourly"],
            "quality_flags": validated["flags"],
        },
    )
    return run_id


def scope2_example(s: Settings) -> scope2.Scope2Result:
    engine = store.get_engine(s.db_path)
    intensity = store.hourly_intensity(engine, s.site_area)
    return scope2.calculate(scope2.read_site_csv(s.site_csv), intensity, s.site_area)


def build_report(s: Settings, run_id: str, result: scope2.Scope2Result) -> dict:
    return report.write_reports(store.get_engine(s.db_path), Path(s.report_dir), run_id, result)


def run(s: Settings) -> dict:
    co2 = fetch_dataset(s, "CO2Emis")
    cons = fetch_dataset(s, "ProductionConsumptionSettlement")
    validated = validate_all(co2, cons, s)
    run_id = store_all(co2, cons, validated, s)
    result = scope2_example(s)
    return build_report(s, run_id, result)
