"""Location-based scope 2 emissions from a site's electricity use.

Hourly input: kg CO2 = sum over hours of kWh(h) * intensity(h) [g/kWh] / 1000.
Monthly input: kWh(month) * mean hourly intensity of that month. This ignores
when in the month the power was used, so it is an approximation and labelled so.

Hours whose intensity is missing or flagged as an error are not filled in.
Their kWh are reported as "not covered" instead, so the gap is visible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from io import StringIO

import pandas as pd


@dataclass
class Scope2Result:
    method: str
    price_area: str
    total_kwh: float
    covered_kwh: float
    uncovered_kwh: float
    emissions_kg_co2: float
    coverage_pct: float
    weighted_intensity_g_per_kwh: float | None
    periods_not_covered: list[str] = field(default_factory=list)
    detail: pd.DataFrame | None = None

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "detail"}
        for k in ("total_kwh", "covered_kwh", "uncovered_kwh", "emissions_kg_co2", "coverage_pct"):
            d[k] = round(d[k], 3)
        if d["weighted_intensity_g_per_kwh"] is not None:
            d["weighted_intensity_g_per_kwh"] = round(d["weighted_intensity_g_per_kwh"], 2)
        return d


def read_site_csv(source) -> pd.DataFrame:
    """Read a site CSV. Lines starting with '#' are comments.

    Accepted layouts: columns `timestamp_utc,kwh` (hourly) or `month,kwh` (YYYY-MM).
    """
    if isinstance(source, (bytes, bytearray)):
        source = StringIO(source.decode("utf-8"))
    df = pd.read_csv(source, comment="#")
    df.columns = [c.strip().lower() for c in df.columns]
    if "kwh" not in df.columns or not ({"timestamp_utc", "month"} & set(df.columns)):
        raise ValueError("CSV needs a 'kwh' column and either 'timestamp_utc' or 'month'")
    df["kwh"] = pd.to_numeric(df["kwh"], errors="raise")
    if (df["kwh"] < 0).any():
        raise ValueError("kwh must be non-negative")
    return df


def calculate(site: pd.DataFrame, intensity: pd.DataFrame, price_area: str) -> Scope2Result:
    """`intensity` has columns ts_utc, co2_g_per_kwh, quality (one row per hour)."""
    usable = intensity[(intensity["quality"] != "error") & intensity["co2_g_per_kwh"].notna()]
    usable = usable.drop_duplicates("ts_utc").set_index("ts_utc")["co2_g_per_kwh"]

    if "timestamp_utc" in site.columns:
        method = "hourly, location-based"
        ts = pd.to_datetime(site["timestamp_utc"], utc=True).dt.tz_localize(None).dt.floor("h")
        detail = pd.DataFrame({"period": ts, "kwh": site["kwh"].to_numpy()})
        detail = detail.groupby("period", as_index=False)["kwh"].sum()
        detail["co2_g_per_kwh"] = detail["period"].map(usable)
        fmt = "%Y-%m-%dT%H:%M"
    else:
        method = "monthly mean intensity, location-based (approximation)"
        monthly = usable.groupby(usable.index.to_period("M")).mean()
        detail = pd.DataFrame({"period": pd.PeriodIndex(site["month"], freq="M"), "kwh": site["kwh"].to_numpy()})
        detail = detail.groupby("period", as_index=False)["kwh"].sum()
        detail["co2_g_per_kwh"] = detail["period"].map(monthly)
        fmt = "%Y-%m"

    detail["kg_co2"] = detail["kwh"] * detail["co2_g_per_kwh"] / 1000
    covered = detail["co2_g_per_kwh"].notna()
    total = float(detail["kwh"].sum())
    covered_kwh = float(detail.loc[covered, "kwh"].sum())
    kg = float(detail.loc[covered, "kg_co2"].sum())
    return Scope2Result(
        method=method,
        price_area=price_area,
        total_kwh=total,
        covered_kwh=covered_kwh,
        uncovered_kwh=total - covered_kwh,
        emissions_kg_co2=kg,
        coverage_pct=100 * covered_kwh / total if total else 0.0,
        weighted_intensity_g_per_kwh=(1000 * kg / covered_kwh) if covered_kwh else None,
        periods_not_covered=[p.strftime(fmt) for p in detail.loc[~covered, "period"]],
        detail=detail,
    )
