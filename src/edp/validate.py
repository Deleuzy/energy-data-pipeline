"""Rule-based data-quality checks.

Nothing is dropped. Every input row comes out again with a `quality` column
('ok', 'warning' or 'error') and the names of the rules it broke; missing time
slots are inserted as empty rows so they are visible too. Each finding is also
written as one row in a separate flags table.

Thresholds below are design choices for this demo, not Energinet limits.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

SEVERITY_RANK = {"ok": 0, "warning": 1, "error": 2}
FLAG_COLUMNS = ["dataset", "price_area", "ts_utc", "rule", "severity", "value", "detail"]


@dataclass(frozen=True)
class RangeRule:
    column: str
    min_value: float
    max_value: float


@dataclass(frozen=True)
class JumpRule:
    column: str
    max_abs_change: float | None = None  # absolute change between consecutive slots
    max_rel_change: float | None = None  # relative change, e.g. 0.35 = 35 %


# CO2 intensity of Danish grid electricity, g/kWh. 0 is possible (all wind),
# anything over 1000 would exceed a pure-coal grid and is treated as an error.
CO2_RANGE = RangeRule("co2_g_per_kwh", 0, 1000)
CO2_HOURLY_JUMP = JumpRule("co2_g_per_kwh", max_abs_change=75)
CO2_MIN_SAMPLES_PER_HOUR = 12  # 5-minute values per hour

# Gross consumption per price area and hour, MWh. DK1 peaks around 4 GWh/h.
CONSUMPTION_RANGE = RangeRule("gross_consumption_mwh", 0, 10_000)
CONSUMPTION_JUMP = JumpRule("gross_consumption_mwh", max_rel_change=0.35)
NON_NEGATIVE = ["wind_mwh", "solar_mwh"]


@dataclass
class ValidationResult:
    rows: pd.DataFrame
    flags: pd.DataFrame


def _flag(dataset, area, ts, rule, severity, value, detail) -> dict:
    return {
        "dataset": dataset,
        "price_area": area,
        "ts_utc": ts,
        "rule": rule,
        "severity": severity,
        "value": None if value is None or pd.isna(value) else float(value),
        "detail": detail,
    }


def check_series(
    df: pd.DataFrame,
    dataset: str,
    value_columns: list[str],
    freq: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    ranges: list[RangeRule] = (),
    non_negative: list[str] = (),
    jump: JumpRule | None = None,
    expected_areas: tuple[str, ...] = ("DK1", "DK2"),
) -> ValidationResult:
    """Validate a frame with columns ts_utc, price_area and value columns.

    Expected slots are every `freq` step in [start, end) for each price area.
    """
    df = df.copy()
    df["rid"] = np.arange(len(df))
    flags: list[dict] = []
    row_flags: dict[int, list[tuple[str, str]]] = {}

    def add(row_id, area, ts, rule, severity, value, detail):
        flags.append(_flag(dataset, area, ts, rule, severity, value, detail))
        row_flags.setdefault(row_id, []).append((rule, severity))

    areas = sorted(set(expected_areas) | set(df["price_area"].dropna()))
    expected = pd.date_range(start, end, freq=freq, inclusive="left")

    # Missing slots: insert an empty row so the gap is visible downstream.
    inserted = []
    for area in areas:
        present = set(df.loc[df["price_area"] == area, "ts_utc"])
        for ts in expected:
            if ts not in present:
                inserted.append({"ts_utc": ts, "price_area": area, **{c: np.nan for c in value_columns}})
    if inserted:
        gap = pd.DataFrame(inserted)
        gap["rid"] = np.arange(len(df), len(df) + len(gap))
        for r in gap.itertuples():
            add(r.rid, r.price_area, r.ts_utc, "missing_slot", "error", None, f"no record for this {freq} slot")
        df = pd.concat([df, gap], ignore_index=True)

    # Records outside the requested window.
    outside = df[(df["ts_utc"] < start) | (df["ts_utc"] >= end)]
    for r in outside.itertuples():
        add(r.rid, r.price_area, r.ts_utc, "outside_window", "warning", None, "timestamp outside requested window")

    # Duplicates: every copy is kept and flagged.
    dup_mask = df.duplicated(["ts_utc", "price_area"], keep=False)
    for r in df[dup_mask].itertuples():
        add(r.rid, r.price_area, r.ts_utc, "duplicate", "warning", None, "more than one record for this slot and area")

    is_gap = df["rid"] >= len(df) - len(inserted)
    for col in value_columns:
        for r in df[df[col].isna() & ~is_gap].itertuples():
            add(r.rid, r.price_area, r.ts_utc, "null_value", "error", None, f"{col} is empty")

    for col in non_negative:
        for r in df[df[col] < 0].itertuples():
            add(r.rid, r.price_area, r.ts_utc, "negative_value", "error", getattr(r, col), f"{col} < 0")

    for rule in ranges:
        vals = df[rule.column]
        # A negative value under a non-negative range is reported as negative_value.
        neg = (vals < 0) & (rule.min_value >= 0)
        for r in df[neg].itertuples():
            add(r.rid, r.price_area, r.ts_utc, "negative_value", "error", getattr(r, rule.column), f"{rule.column} < 0")
        oor = ((vals < rule.min_value) | (vals > rule.max_value)) & ~neg
        for r in df[oor].itertuples():
            add(
                r.rid, r.price_area, r.ts_utc, "out_of_range", "error", getattr(r, rule.column),
                f"{rule.column} outside [{rule.min_value}, {rule.max_value}]",
            )

    if jump is not None:
        for area in areas:
            s = (
                df[(df["price_area"] == area)]
                .drop_duplicates(["ts_utc"])
                .sort_values("ts_utc")
                .set_index("ts_utc")
            )
            vals = s[jump.column]
            prev = vals.shift(1)
            # Only compare with the slot directly before, not across gaps.
            consecutive = s.index.to_series().diff() == pd.tseries.frequencies.to_offset(freq).nanos * pd.Timedelta(1)
            delta = (vals - prev).abs()
            hit = pd.Series(False, index=s.index)
            if jump.max_abs_change is not None:
                hit |= delta > jump.max_abs_change
            if jump.max_rel_change is not None:
                hit |= (delta / prev.abs().replace(0, np.nan)) > jump.max_rel_change
            hit &= consecutive
            for ts in s.index[hit]:
                limit = (
                    f"{jump.max_abs_change}" if jump.max_abs_change is not None else f"{jump.max_rel_change:.0%}"
                )
                add(
                    int(s.loc[ts, "rid"]), area, ts, "sudden_jump", "warning", vals[ts],
                    f"{jump.column} changed by {delta[ts]:.1f} from {prev[ts]:.1f} (limit {limit})",
                )

    df["flag_rules"] = df["rid"].map(lambda i: ";".join(sorted({r for r, _ in row_flags.get(i, [])})))
    df["quality"] = df["rid"].map(
        lambda i: max((s for _, s in row_flags.get(i, [])), key=SEVERITY_RANK.get, default="ok")
    )
    df = df.sort_values(["price_area", "ts_utc", "rid"]).drop(columns="rid").reset_index(drop=True)
    flag_frame = pd.DataFrame(flags, columns=FLAG_COLUMNS)
    if not flag_frame.empty:
        flag_frame = flag_frame.sort_values(["dataset", "price_area", "ts_utc", "rule"]).reset_index(drop=True)
    return ValidationResult(df, flag_frame)


def tidy_co2(raw: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_utc": pd.to_datetime(raw["Minutes5UTC"]),
            "price_area": raw["PriceArea"].astype(object),
            "co2_g_per_kwh": pd.to_numeric(raw["CO2Emission"], errors="coerce"),
        }
    )


def tidy_consumption(raw: pd.DataFrame) -> pd.DataFrame:
    num = lambda c: pd.to_numeric(raw[c], errors="coerce")
    wind = sum(num(c).fillna(0) for c in raw.columns if "Wind" in c)
    solar = sum(num(c).fillna(0) for c in raw.columns if "Solar" in c)
    return pd.DataFrame(
        {
            "ts_utc": pd.to_datetime(raw["HourUTC"]),
            "price_area": raw["PriceArea"].astype(object),
            "gross_consumption_mwh": num("GrossConsumptionMWh"),
            "wind_mwh": wind,
            "solar_mwh": solar,
        }
    )


def validate_co2(raw: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp):
    """Validate 5-minute CO2 values, then build an hourly series from the usable ones.

    Returns (five_minute_result, hourly_result). The hourly value is the mean of
    the 5-minute values in that hour that have no error flag.
    """
    five = check_series(tidy_co2(raw), "CO2Emis", ["co2_g_per_kwh"], "5min", start, end, ranges=[CO2_RANGE])

    usable = five.rows[five.rows["quality"] != "error"].drop_duplicates(["ts_utc", "price_area"])
    usable = usable.assign(hour=usable["ts_utc"].dt.floor("h"))
    hourly = (
        usable.groupby(["price_area", "hour"])["co2_g_per_kwh"]
        .agg(["mean", "count"])
        .rename(columns={"mean": "co2_g_per_kwh", "count": "n_samples"})
        .reset_index()
        .rename(columns={"hour": "ts_utc"})
    )
    hourly["co2_g_per_kwh"] = hourly["co2_g_per_kwh"].round(2)
    hres = check_series(hourly, "CO2Emis_hourly", ["co2_g_per_kwh"], "h", start, end, jump=CO2_HOURLY_JUMP)

    rows = hres.rows
    rows["n_samples"] = rows["n_samples"].fillna(0).astype(int)
    extra = []
    for r in rows[(rows["n_samples"] > 0) & (rows["n_samples"] < CO2_MIN_SAMPLES_PER_HOUR)].itertuples():
        extra.append(
            _flag("CO2Emis_hourly", r.price_area, r.ts_utc, "incomplete_hour", "warning", r.co2_g_per_kwh,
                  f"mean of {r.n_samples} of {CO2_MIN_SAMPLES_PER_HOUR} usable 5-minute values")
        )
        i = r.Index
        rows.at[i, "flag_rules"] = ";".join(sorted(filter(None, [rows.at[i, "flag_rules"], "incomplete_hour"])))
        if rows.at[i, "quality"] == "ok":
            rows.at[i, "quality"] = "warning"
    flags = pd.concat([hres.flags, pd.DataFrame(extra, columns=FLAG_COLUMNS)], ignore_index=True)
    return five, ValidationResult(rows, flags)


DECLARED_METHOD = "125%"


def validate_declared(raw: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> ValidationResult:
    """Declared hourly CO2 (Energinet DeclarationGridEmission, 125% method only).

    Other allocation methods (200%, Total) are not used. Rows are kept, not filtered
    by quality, so the table holds exactly what the API returned for this method.
    """
    method = raw[raw["FuelAllocationMethod"] == DECLARED_METHOD]
    tidy = pd.DataFrame(
        {
            "ts_utc": pd.to_datetime(method["HourUTC"]),
            "price_area": method["PriceArea"].astype(object),
            "co2_declared_g_per_kwh": pd.to_numeric(method["CO2PerkWh"], errors="coerce"),
            "version": method["Version"].astype(object),
        }
    )
    return check_series(
        tidy, "DeclarationGridEmission", ["co2_declared_g_per_kwh"], "h", start, end,
        ranges=[RangeRule("co2_declared_g_per_kwh", CO2_RANGE.min_value, CO2_RANGE.max_value)],
    )


def validate_consumption(raw: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> ValidationResult:
    return check_series(
        tidy_consumption(raw),
        "ProductionConsumptionSettlement",
        ["gross_consumption_mwh", "wind_mwh", "solar_mwh"],
        "h",
        start,
        end,
        ranges=[CONSUMPTION_RANGE],
        non_negative=NON_NEGATIVE,
        jump=CONSUMPTION_JUMP,
    )


def summarize_flags(flags: pd.DataFrame) -> pd.DataFrame:
    if flags.empty:
        return pd.DataFrame(columns=["dataset", "price_area", "rule", "severity", "count"])
    return (
        flags.groupby(["dataset", "price_area", "rule", "severity"]).size().rename("count").reset_index()
    )
