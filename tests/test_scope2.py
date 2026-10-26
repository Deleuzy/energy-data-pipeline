import io

import pandas as pd
import pytest

from edp import scope2


def intensity(values, quality=None):
    ts = pd.date_range("2026-10-13T06:00", periods=len(values), freq="h")
    return pd.DataFrame({"ts_utc": ts, "co2_g_per_kwh": values, "quality": quality or ["ok"] * len(values)})


def site(text):
    return scope2.read_site_csv(io.StringIO(text))


HOURLY = "# synthetic\ntimestamp_utc,kwh\n2026-10-13T06:00:00Z,10\n2026-10-13T07:00:00Z,20\n2026-10-13T08:00:00Z,30\n"


def test_hourly_multiplies_hour_by_hour():
    r = scope2.calculate(site(HOURLY), intensity([100, 200, 50]), "DK1")
    # 10*100 + 20*200 + 30*50 = 6500 g = 6.5 kg
    assert r.emissions_kg_co2 == pytest.approx(6.5)
    assert r.coverage_pct == 100 and r.weighted_intensity_g_per_kwh == pytest.approx(6500 / 60)


def test_missing_or_error_hours_are_not_covered_and_not_filled():
    r = scope2.calculate(site(HOURLY), intensity([100, 999, None], ["ok", "error", "error"]), "DK1")
    assert r.emissions_kg_co2 == pytest.approx(1.0)
    assert r.covered_kwh == 10 and r.uncovered_kwh == 50
    assert r.periods_not_covered == ["2026-10-13T07:00", "2026-10-13T08:00"]


def test_warning_hours_are_used():
    r = scope2.calculate(site(HOURLY), intensity([100, 200, 50], ["ok", "warning", "ok"]), "DK1")
    assert r.coverage_pct == 100


def test_monthly_uses_mean_intensity_of_month():
    r = scope2.calculate(site("month,kwh\n2026-10,1000\n2026-11,5\n"), intensity([100, 200, 300]), "DK2")
    assert r.emissions_kg_co2 == pytest.approx(200.0)
    assert "approximation" in r.method
    assert r.periods_not_covered == ["2026-11"]


def test_bad_csv_is_rejected():
    with pytest.raises(ValueError):
        site("time,energy\n2026-10-13,1\n")
    with pytest.raises(ValueError):
        site("timestamp_utc,kwh\n2026-10-13T06:00Z,-1\n")
