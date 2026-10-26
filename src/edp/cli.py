"""Command line: `edp run`, `edp scope2`, `edp serve`."""

from __future__ import annotations

import argparse
import json

from edp import pipeline, scope2, store


def main(argv: list[str] | None = None) -> None:
    defaults = pipeline.Settings()
    p = argparse.ArgumentParser(prog="edp", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="fetch -> validate -> store -> report")
    run.add_argument("--start", default=defaults.start, help="UTC, inclusive (YYYY-MM-DDTHH:MM)")
    run.add_argument("--end", default=defaults.end, help="UTC, exclusive")
    run.add_argument("--db", default=defaults.db_path)
    run.add_argument("--cache-dir", default=defaults.cache_dir)
    run.add_argument("--report-dir", default=defaults.report_dir)
    run.add_argument("--site-csv", default=defaults.site_csv)
    run.add_argument("--site-area", default=defaults.site_area, choices=["DK1", "DK2"])
    run.add_argument("--offline", action="store_true", help="use cached responses only")
    run.add_argument("--refresh", action="store_true", help="ignore the cache and refetch")

    s2 = sub.add_parser("scope2", help="location-based scope 2 for a site CSV")
    s2.add_argument("csv")
    s2.add_argument("--area", default="DK1", choices=["DK1", "DK2"])
    s2.add_argument("--db", default=defaults.db_path)

    serve = sub.add_parser("serve", help="start the FastAPI app")
    serve.add_argument("--port", type=int, default=8000)

    a = p.parse_args(argv)
    if a.cmd == "run":
        s = pipeline.Settings(a.start, a.end, a.db, a.cache_dir, a.report_dir, a.site_csv, a.site_area, a.offline, a.refresh)
        summary = pipeline.run(s)
        print(json.dumps({k: summary[k] for k in ("run_id", "window_utc", "row_counts", "total_flags",
                                                  "intensity_by_area", "scope2_synthetic_site")}, indent=2))
        print(f"\nReports written to {a.report_dir}/ (dashboard.html, quality_report.md, summary.json)")
    elif a.cmd == "scope2":
        engine = store.get_engine(a.db)
        result = scope2.calculate(scope2.read_site_csv(a.csv), store.hourly_intensity(engine, a.area), a.area)
        print(json.dumps(result.to_dict(), indent=2))
    elif a.cmd == "serve":
        import uvicorn

        uvicorn.run("edp.api:app", port=a.port)


if __name__ == "__main__":
    main()
