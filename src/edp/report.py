"""Data-quality report (Markdown + JSON) and a static HTML dashboard.

Every number written here is computed from the SQLite database of the run.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.offline import get_plotlyjs_version
from sqlalchemy.engine import Engine

from edp import validate
from edp.scope2 import Scope2Result
from edp.store import read_sql

SERIES = {"DK1": "#2a78d6", "DK2": "#eb6834"}
STATUS = {"warning": "#fab219", "error": "#d03b3b"}
INK, INK_2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e6e5e1", "#fcfcfb"


def summarize(engine: Engine, run_id: str, s2: Scope2Result) -> dict:
    run = read_sql(engine, "SELECT * FROM runs").iloc[0].to_dict()
    fetches = read_sql(engine, "SELECT dataset, url, fetched_at, n_records, sha256, from_cache FROM fetch_log")
    flags = read_sql(engine, "SELECT * FROM quality_flags")
    tables = {}
    for t in ["raw_co2emis", "raw_declared_emission", "raw_production_consumption", "co2_5min_validated",
              "co2_hourly", "co2_declared_hourly", "consumption_hourly"]:
        q = read_sql(engine, f"SELECT {'quality, ' if 'raw' not in t else ''}COUNT(*) n FROM {t}"
                     + (" GROUP BY quality" if "raw" not in t else ""))
        tables[t] = {r["quality"]: int(r["n"]) for _, r in q.iterrows()} if "quality" in q else int(q["n"].iloc[0])

    intensity = read_sql(
        engine,
        """SELECT h.price_area, h.co2_g_per_kwh, c.gross_consumption_mwh
           FROM co2_hourly h LEFT JOIN consumption_hourly c
             ON c.ts_utc = h.ts_utc AND c.price_area = h.price_area AND c.quality != 'error'
           WHERE h.quality != 'error' AND h.co2_g_per_kwh IS NOT NULL""",
    )
    areas = {}
    for area, g in intensity.groupby("price_area"):
        both = g.dropna(subset=["gross_consumption_mwh"])
        areas[area] = {
            "usable_hours": int(len(g)),
            "mean_g_per_kwh": round(float(g["co2_g_per_kwh"].mean()), 1),
            "min_g_per_kwh": round(float(g["co2_g_per_kwh"].min()), 1),
            "max_g_per_kwh": round(float(g["co2_g_per_kwh"].max()), 1),
            "consumption_weighted_g_per_kwh": round(
                float((both["co2_g_per_kwh"] * both["gross_consumption_mwh"]).sum()
                      / both["gross_consumption_mwh"].sum()), 1
            ) if len(both) else None,
            "gross_consumption_gwh": round(float(both["gross_consumption_mwh"].sum()) / 1000, 1),
        }

    comparison = _declared_comparison(engine)
    summary_flags = validate.summarize_flags(flags)
    return {
        "run_id": run_id,
        "window_utc": [run["window_start"], run["window_end"]],
        "sources": fetches.to_dict("records"),
        "row_counts": tables,
        "flag_counts": summary_flags.to_dict("records"),
        "total_flags": int(len(flags)),
        "intensity_by_area": areas,
        "realtime_vs_declared": comparison,
        "scope2_synthetic_site": s2.to_dict(),
    }


def _declared_comparison(engine: Engine) -> dict:
    """Real-time CO2Emis hourly mean minus declared 125% value, per area, over hours both have."""
    df = read_sql(
        engine,
        """SELECT h.price_area, h.co2_g_per_kwh AS realtime, d.co2_declared_g_per_kwh AS declared
           FROM co2_hourly h JOIN co2_declared_hourly d
             ON d.ts_utc = h.ts_utc AND d.price_area = h.price_area
           WHERE h.quality != 'error' AND d.quality != 'error'
             AND h.co2_g_per_kwh IS NOT NULL AND d.co2_declared_g_per_kwh IS NOT NULL""",
    )
    out = {}
    for area, g in df.groupby("price_area"):
        diff = g["realtime"] - g["declared"]
        out[area] = {
            "hours_compared": int(len(g)),
            "mean_realtime_g_per_kwh": round(float(g["realtime"].mean()), 1),
            "mean_declared_g_per_kwh": round(float(g["declared"].mean()), 1),
            "mean_diff_g_per_kwh": round(float(diff.mean()), 1),
            "mean_abs_diff_g_per_kwh": round(float(diff.abs().mean()), 1),
        }
    return out


def _md_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_none_\n"
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(str(v) for v in row) + " |" for row in df.itertuples(index=False)]
    return "\n".join(lines) + "\n"


def markdown(summary: dict, flags: pd.DataFrame) -> str:
    s2 = summary["scope2_synthetic_site"]
    out = [
        "# Data-quality report",
        "",
        f"Run `{summary['run_id']}`, window {summary['window_utc'][0]} to {summary['window_utc'][1]} (UTC, end exclusive).",
        "",
        "## Sources",
        "",
        _md_table(pd.DataFrame(summary["sources"])[["dataset", "fetched_at", "n_records", "url"]]),
        "## Rows per table and quality label",
        "",
    ]
    for t, v in summary["row_counts"].items():
        out.append(f"- `{t}`: {v}")
    out += ["", "## Flags by rule", "", _md_table(pd.DataFrame(summary["flag_counts"])), "## Hourly CO2 intensity (usable hours)", ""]
    out.append(_md_table(pd.DataFrame(summary["intensity_by_area"]).T.reset_index(names="price_area")))
    out += [
        "## Scope 2 example (SYNTHETIC site, real intensity)",
        "",
        f"- Method: {s2['method']}, price area {s2['price_area']}",
        f"- Electricity: {s2['total_kwh']} kWh, covered {s2['covered_kwh']} kWh ({s2['coverage_pct']} %)",
        f"- Emissions: {s2['emissions_kg_co2']} kg CO2 (weighted intensity {s2['weighted_intensity_g_per_kwh']} g/kWh)",
        f"- Hours not covered: {len(s2['periods_not_covered'])}",
        "",
        "## Real-time vs declared CO2 (125% method, Initial version)",
        "",
        _md_table(pd.DataFrame(summary["realtime_vs_declared"]).T.reset_index(names="price_area")),
        "## Flag detail (first 200)",
        "",
        _md_table(flags.drop(columns=["run_id"]).head(200)),
    ]
    return "\n".join(out)


def _layout(fig: go.Figure, title: str, ytitle: str) -> go.Figure:
    fig.update_layout(
        title=dict(text=title, x=0, font=dict(size=15, color=INK)),
        paper_bgcolor=SURFACE, plot_bgcolor=SURFACE, font=dict(color=INK_2, size=12),
        margin=dict(l=56, r=16, t=48, b=40), height=340, hovermode="x unified",
        legend=dict(orientation="h", y=1.12, x=1, xanchor="right"),
    )
    fig.update_xaxes(showgrid=False, linecolor=GRID)
    fig.update_yaxes(title=ytitle, gridcolor=GRID, zeroline=False, rangemode="tozero")
    return fig


def charts(engine: Engine, s2: Scope2Result) -> list[str]:
    hourly = read_sql(engine, "SELECT * FROM co2_hourly ORDER BY ts_utc")
    flags = read_sql(engine, "SELECT * FROM quality_flags")
    cons = read_sql(engine, "SELECT * FROM consumption_hourly ORDER BY ts_utc")
    out = []

    fig = go.Figure()
    for area, g in hourly.groupby("price_area"):
        fig.add_scatter(x=g["ts_utc"], y=g["co2_g_per_kwh"], name=area, mode="lines",
                        line=dict(color=SERIES.get(area), width=2),
                        hovertemplate="%{y:.0f} g/kWh")
    for sev, symbol in (("warning", "triangle-up"), ("error", "x")):
        g = hourly[hourly["quality"] == sev]
        if len(g):
            fig.add_scatter(x=g["ts_utc"], y=g["co2_g_per_kwh"].fillna(0), mode="markers",
                            name=f"{sev} ({len(g)} h)", marker=dict(color=STATUS[sev], size=9, symbol=symbol,
                            line=dict(color=SURFACE, width=2)),
                            text=g["price_area"] + ": " + g["flag_rules"], hovertemplate="%{text}")
    out.append(_layout(fig, "Hourly CO2 intensity of consumed electricity (Energinet CO2Emis, hourly mean)", "g CO2/kWh"))

    if len(flags):
        c = flags.groupby(["rule", "severity"]).size().rename("n").reset_index().sort_values("n")
        fig = go.Figure(go.Bar(
            x=c["n"], y=c["rule"] + " (" + c["severity"] + ")", orientation="h",
            marker=dict(color=[STATUS[s] for s in c["severity"]], line=dict(color=SURFACE, width=2)),
            text=c["n"], textposition="outside", hovertemplate="%{y}: %{x}<extra></extra>",
        ))
        _layout(fig, "Quality flags by rule (all datasets)", "")
        fig.update_layout(hovermode="closest", height=120 + 40 * len(c), showlegend=False)
        fig.update_yaxes(rangemode=None, title=None)
        fig.update_xaxes(title="flags")
        out.append(fig)

    declared = read_sql(engine, "SELECT * FROM co2_declared_hourly WHERE quality != 'error' ORDER BY ts_utc")
    fig = go.Figure()
    for area, g in hourly.groupby("price_area"):
        fig.add_scatter(x=g["ts_utc"], y=g["co2_g_per_kwh"], name=f"{area} real-time", mode="lines",
                        line=dict(color=SERIES.get(area), width=2), hovertemplate="%{y:.0f} g/kWh")
    for area, g in declared.groupby("price_area"):
        fig.add_scatter(x=g["ts_utc"], y=g["co2_declared_g_per_kwh"], name=f"{area} declared", mode="lines",
                        line=dict(color=SERIES.get(area), width=1.5, dash="dot"), hovertemplate="%{y:.0f} g/kWh")
    out.append(_layout(fig, "Real-time (solid) vs declared 125% (dotted) CO2 intensity", "g CO2/kWh"))

    fig = go.Figure()
    for area, g in cons.groupby("price_area"):
        fig.add_scatter(x=g["ts_utc"], y=g["gross_consumption_mwh"], name=area, mode="lines",
                        line=dict(color=SERIES.get(area), width=2), hovertemplate="%{y:.0f} MWh")
    out.append(_layout(fig, "Gross electricity consumption per hour (ProductionConsumptionSettlement)", "MWh"))

    d = s2.detail
    if d is not None and len(d):
        fig = go.Figure(go.Bar(x=d["period"].astype(str), y=d["kg_co2"], marker=dict(color=SERIES["DK1"]),
                               hovertemplate="%{y:.2f} kg CO2<extra></extra>"))
        _layout(fig, f"SYNTHETIC example site: hourly scope 2 emissions ({s2.price_area})", "kg CO2")
        fig.update_layout(hovermode="closest", bargap=0.1)
        out.append(fig)

    return [f.to_html(full_html=False, include_plotlyjs=False) for f in out]


def dashboard_html(summary: dict, figures: list[str], flags: pd.DataFrame) -> str:
    s2 = summary["scope2_synthetic_site"]
    area_rows = "".join(
        f"<tr><td>{a}</td><td>{v['usable_hours']}</td><td>{v['mean_g_per_kwh']}</td><td>{v['min_g_per_kwh']}</td>"
        f"<td>{v['max_g_per_kwh']}</td><td>{v['consumption_weighted_g_per_kwh']}</td></tr>"
        for a, v in summary["intensity_by_area"].items()
    )
    flag_rows = "".join(
        f"<tr><td>{html.escape(str(r.dataset))}</td><td>{r.price_area}</td><td>{r.ts_utc}</td><td>{r.rule}</td>"
        f"<td class='{r.severity}'>{r.severity}</td><td>{'' if pd.isna(r.value) else r.value}</td>"
        f"<td>{html.escape(str(r.detail))}</td></tr>"
        for r in flags.head(300).itertuples()
    )
    sources = "".join(
        f"<li><code>{html.escape(x['dataset'])}</code>, {x['n_records']} records, fetched {x['fetched_at']}: "
        f"<a href='{html.escape(x['url'])}'>request URL</a></li>" for x in summary["sources"]
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Grid CO2 Quality</title>
<script src="https://cdn.jsdelivr.net/npm/plotly.js-dist-min@{get_plotlyjs_version()}/plotly.min.js"></script>
<style>
:root {{ --surface:{SURFACE}; --ink:{INK}; --ink2:{INK_2}; --grid:{GRID}; }}
body {{ margin:0; background:var(--surface); color:var(--ink); font:15px/1.5 system-ui, sans-serif; }}
main {{ max-width:1100px; margin:0 auto; padding:24px 16px 64px; }}
h1 {{ font-size:24px; margin:0 0 4px; }} h2 {{ font-size:17px; margin:32px 0 8px; }}
.muted {{ color:var(--ink2); }} .tiles {{ display:flex; flex-wrap:wrap; gap:12px; margin:16px 0; }}
.tile {{ border:1px solid var(--grid); border-radius:8px; padding:12px 16px; min-width:150px; }}
.tile b {{ display:block; font-size:22px; }}
table {{ border-collapse:collapse; width:100%; font-size:13px; }} th, td {{ border-bottom:1px solid var(--grid); padding:4px 8px; text-align:left; }}
.scroll {{ overflow-x:auto; max-height:420px; overflow-y:auto; }}
.warning::before {{ content:"▲ "; color:{STATUS['warning']}; }} .error::before {{ content:"✕ "; color:{STATUS['error']}; }}
.synthetic {{ background:#fff4d6; border-radius:4px; padding:2px 6px; font-weight:600; }}
</style></head><body><main>
<h1>Danish grid CO2 intensity: data-quality report</h1>
<p class="muted">Run <code>{summary['run_id']}</code> · window {summary['window_utc'][0]} to {summary['window_utc'][1]} UTC (end exclusive) ·
real public data from Energinet's Energi Data Service. Flagged rows are kept and shown, not dropped.</p>
<div class="tiles">
<div class="tile"><span class="muted">Quality flags</span><b>{summary['total_flags']}</b></div>
{''.join(f"<div class='tile'><span class='muted'>{a} mean intensity</span><b>{v['mean_g_per_kwh']} g/kWh</b></div>" for a, v in summary['intensity_by_area'].items())}
<div class="tile"><span class="muted"><span class="synthetic">SYNTHETIC</span> site scope 2</span><b>{s2['emissions_kg_co2']:,.1f} kg CO2</b></div>
</div>
{''.join(f'<div>{f}</div>' for f in figures)}
<h2>Intensity by price area (hours without error flags)</h2>
<div class="scroll"><table><tr><th>Area</th><th>Hours</th><th>Mean g/kWh</th><th>Min</th><th>Max</th><th>Consumption-weighted g/kWh</th></tr>{area_rows}</table></div>
<h2>Scope 2, location-based: <span class="synthetic">SYNTHETIC</span> example site</h2>
<p>{s2['method']} · price area {s2['price_area']} · {s2['total_kwh']:,.0f} kWh of which {s2['coverage_pct']} % had a usable hourly intensity ·
<b>{s2['emissions_kg_co2']:,.1f} kg CO2</b> · weighted intensity {s2['weighted_intensity_g_per_kwh']} g/kWh ·
{len(s2['periods_not_covered'])} hours not covered. The site's consumption is invented; the intensity is real.</p>
<h2>Flags (first 300 of {summary['total_flags']})</h2>
<div class="scroll"><table><tr><th>Dataset</th><th>Area</th><th>Time UTC</th><th>Rule</th><th>Severity</th><th>Value</th><th>Detail</th></tr>{flag_rows}</table></div>
<h2>Sources</h2><ul>{sources}</ul>
</main></body></html>"""


def write_reports(engine: Engine, out_dir: Path, run_id: str, s2: Scope2Result) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = summarize(engine, run_id, s2)
    flags = read_sql(engine, "SELECT * FROM quality_flags ORDER BY dataset, price_area, ts_utc")
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    (out_dir / "quality_report.md").write_text(markdown(summary, flags))
    (out_dir / "dashboard.html").write_text(dashboard_html(summary, charts(engine, s2), flags))
    return summary
