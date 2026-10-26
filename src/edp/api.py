"""FastAPI app serving the stored, validated data.

GET  /intensity?area=DK1&start=...&end=...   hourly intensity with quality labels
GET  /quality                                 flag counts for the stored run
POST /scope2?area=DK1                         upload a site CSV, get location-based scope 2
GET  /dashboard                               the static HTML report of the last run
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from edp import pipeline, scope2, store, validate

DB_PATH = os.environ.get("EDP_DB", pipeline.Settings().db_path)
REPORT_DIR = Path(os.environ.get("EDP_REPORT_DIR", pipeline.Settings().report_dir))

app = FastAPI(title="Energy data pipeline", version="0.1.0")


def _engine():
    if not Path(DB_PATH).exists():
        raise HTTPException(503, "No database yet. Run `edp run` first.")
    return store.get_engine(DB_PATH)


@app.get("/intensity")
def intensity(
    area: str = Query("DK1", pattern="^DK[12]$"),
    start: str | None = Query(None, description="UTC, inclusive, e.g. 2026-10-01T00:00:00"),
    end: str | None = Query(None, description="UTC, exclusive"),
):
    df = store.hourly_intensity(_engine(), area, start, end)
    df["ts_utc"] = df["ts_utc"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    df = df.astype(object).where(df.notna(), None)
    return {
        "source": "Energinet Energi Data Service, dataset CO2Emis (5-min values averaged per hour)",
        "unit": "g CO2/kWh",
        "hours": len(df),
        "records": df.to_dict("records"),
    }


@app.get("/quality")
def quality():
    engine = _engine()
    flags = store.read_sql(engine, "SELECT * FROM quality_flags")
    run = store.read_sql(engine, "SELECT * FROM runs").to_dict("records")
    return {"run": run[0] if run else None, "total_flags": len(flags),
            "by_rule": validate.summarize_flags(flags).to_dict("records")}


@app.post("/scope2")
async def scope2_upload(area: str = Query("DK1", pattern="^DK[12]$"), file: UploadFile = File(...)):
    try:
        site = scope2.read_site_csv(await file.read())
    except (ValueError, UnicodeDecodeError) as e:
        raise HTTPException(422, str(e))
    result = scope2.calculate(site, store.hourly_intensity(_engine(), area), area)
    return result.to_dict()


@app.get("/dashboard")
def dashboard():
    path = REPORT_DIR / "dashboard.html"
    if not path.exists():
        raise HTTPException(404, "No dashboard yet. Run `edp run` first.")
    return FileResponse(path)
