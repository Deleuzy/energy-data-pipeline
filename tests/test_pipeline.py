import json
from pathlib import Path

from sqlalchemy import inspect

from edp import pipeline, store


def test_full_pipeline_offline(settings):
    summary = pipeline.run(settings)
    engine = store.get_engine(settings.db_path)
    tables = set(inspect(engine).get_table_names())
    assert {"raw_co2emis", "co2_hourly", "consumption_hourly", "quality_flags", "fetch_log"} <= tables
    counts = store.read_sql(engine, "SELECT COUNT(*) n FROM raw_co2emis")["n"].iloc[0]
    assert counts == 70
    assert summary["total_flags"] == 4  # 2 missing 5-min slots + 2 incomplete hours
    s2 = summary["scope2_synthetic_site"]
    # Site has 4 hours; 09:00 is outside the stored window, so it is reported as not covered.
    assert s2["total_kwh"] == 65 and s2["uncovered_kwh"] == 5
    assert s2["periods_not_covered"] == ["2026-10-13T09:00"]
    out = Path(settings.report_dir)
    assert json.loads((out / "summary.json").read_text())["run_id"] == summary["run_id"]
    assert "missing_slot" in (out / "quality_report.md").read_text()
    assert "SYNTHETIC" in (out / "dashboard.html").read_text()


def test_rerun_replaces_instead_of_duplicating(settings):
    pipeline.run(settings)
    pipeline.run(settings)
    engine = store.get_engine(settings.db_path)
    assert store.read_sql(engine, "SELECT COUNT(*) n FROM raw_co2emis")["n"].iloc[0] == 70
    assert store.read_sql(engine, "SELECT COUNT(*) n FROM runs")["n"].iloc[0] == 1


def test_dagster_assets_materialize_offline(settings):
    import dagster as dg

    from edp import definitions

    res = definitions.PipelineSettings(**{k: v for k, v in settings.__dict__.items()})
    result = dg.materialize(definitions.all_assets, resources={"settings": res})
    assert result.success
    assert result.output_for_node("report")["total_flags"] == 4
