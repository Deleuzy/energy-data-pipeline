import pandas as pd

from edp import fetch, validate
from conftest import END, FIXTURES, START

S, E = pd.Timestamp("2026-01-01T00:00"), pd.Timestamp("2026-01-01T04:00")


def frame(values, area="DK1", start=S):
    ts = pd.date_range(start, periods=len(values), freq="h")
    return pd.DataFrame({"ts_utc": ts, "price_area": area, "v": values})


def both(values_dk1, values_dk2=None):
    return pd.concat([frame(values_dk1, "DK1"), frame(values_dk2 or values_dk1, "DK2")], ignore_index=True)


def check(df, **kw):
    return validate.check_series(df, "test", ["v"], "h", S, E, **kw)


RANGE = [validate.RangeRule("v", 0, 100)]


def rules(res):
    return sorted(res.flags["rule"]) if not res.flags.empty else []


def test_clean_data_has_no_flags():
    res = check(both([10, 11, 12, 13]), ranges=RANGE, jump=validate.JumpRule("v", max_abs_change=50))
    assert res.flags.empty
    assert (res.rows["quality"] == "ok").all() and len(res.rows) == 8


def test_missing_hour_is_inserted_and_flagged():
    df = both([10, 11, 12, 13])
    df = df.drop(index=1)  # DK1 01:00
    res = check(df)
    assert rules(res) == ["missing_slot"]
    gap = res.rows[(res.rows["price_area"] == "DK1") & (res.rows["ts_utc"] == S + pd.Timedelta(hours=1))]
    assert len(gap) == 1 and gap["v"].isna().all() and gap["quality"].iloc[0] == "error"


def test_missing_whole_area_is_flagged():
    res = check(frame([1, 2, 3, 4], "DK1"))
    assert rules(res) == ["missing_slot"] * 4
    assert set(res.flags["price_area"]) == {"DK2"}


def test_duplicates_are_kept_and_flagged():
    df = both([10, 11, 12, 13])
    df = pd.concat([df, df.iloc[[2]]], ignore_index=True)
    res = check(df)
    assert len(res.rows) == 9  # nothing dropped
    assert rules(res) == ["duplicate", "duplicate"]
    assert (res.rows["quality"] == "warning").sum() == 2


def test_negative_out_of_range_and_null():
    res = check(both([10, -5, 250, None], [10, 11, 12, 13]), ranges=RANGE)
    assert rules(res) == ["negative_value", "null_value", "out_of_range"]
    assert (res.rows["quality"] == "error").sum() == 3
    assert len(res.rows) == 8


def test_sudden_jump_absolute_and_relative():
    res = check(both([10, 100, 105, 106]), jump=validate.JumpRule("v", max_abs_change=50))
    assert rules(res) == ["sudden_jump", "sudden_jump"]
    res = check(both([100, 140, 141, 142]), jump=validate.JumpRule("v", max_rel_change=0.35))
    assert rules(res) == ["sudden_jump", "sudden_jump"]


def test_no_jump_reported_across_a_gap():
    df = both([10, 11, 90, 91]).drop(index=1)  # DK1 01:00 missing, 10 -> 90 is across the gap
    res = check(df, jump=validate.JumpRule("v", max_abs_change=50))
    dk1 = res.flags[res.flags["price_area"] == "DK1"]
    assert sorted(dk1["rule"]) == ["missing_slot"]


def test_real_fixture_slice():
    """The saved real slice has one missing 5-minute value per area at 07:15 UTC."""
    client = fetch.EnergiDataServiceClient(FIXTURES / "raw", offline=True)
    raw = client.fetch("CO2Emis", START, END).frame
    five, hourly = validate.validate_co2(raw, pd.Timestamp(START), pd.Timestamp(END))
    assert len(five.rows) == 72  # 70 real + 2 inserted gap rows
    assert sorted(five.flags["rule"]) == ["missing_slot", "missing_slot"]
    assert set(five.flags["ts_utc"]) == {pd.Timestamp("2026-10-13T07:15")}
    assert len(hourly.rows) == 6
    inc = hourly.rows[hourly.rows["flag_rules"] == "incomplete_hour"]
    assert len(inc) == 2 and (inc["n_samples"] == 11).all() and (inc["quality"] == "warning").all()
    # Hourly value is the plain mean of the 5-minute values in that hour.
    dk1_06 = raw[(raw.PriceArea == "DK1") & raw.Minutes5UTC.str.startswith("2026-10-13T06")]["CO2Emission"].mean()
    got = hourly.rows[(hourly.rows.price_area == "DK1") & (hourly.rows.ts_utc == pd.Timestamp("2026-10-13T06:00"))]
    assert got["co2_g_per_kwh"].iloc[0] == round(dk1_06, 2)


def test_consumption_fixture_is_clean():
    client = fetch.EnergiDataServiceClient(FIXTURES / "raw", offline=True)
    raw = client.fetch("ProductionConsumptionSettlement", START, END).frame
    res = validate.validate_consumption(raw, pd.Timestamp(START), pd.Timestamp(END))
    assert res.flags.empty and len(res.rows) == 6
    assert (res.rows["wind_mwh"] > 0).all()
