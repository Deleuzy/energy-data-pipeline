"""Dagster definitions: the same pipeline steps as software-defined assets.

    dagster dev -m edp.definitions

Window and paths come from the `settings` resource (defaults in pipeline.Settings).
"""

import dagster as dg

from edp import pipeline


class PipelineSettings(dg.ConfigurableResource):
    start: str = pipeline.Settings.start
    end: str = pipeline.Settings.end
    db_path: str = pipeline.Settings.db_path
    cache_dir: str = pipeline.Settings.cache_dir
    report_dir: str = pipeline.Settings.report_dir
    site_csv: str = pipeline.Settings.site_csv
    site_area: str = pipeline.Settings.site_area
    offline: bool = False
    refresh: bool = False

    def to_settings(self) -> pipeline.Settings:
        return pipeline.Settings(**self.model_dump())


def _fetch_meta(result) -> dict:
    return {
        "records": len(result.frame),
        "pages": len(result.pages),
        "source_url": dg.MetadataValue.url(result.pages[0].url),
        "fetched_at": result.pages[0].fetched_at,
        "from_cache": all(p.from_cache for p in result.pages),
    }


@dg.asset(group_name="fetch", description="Energinet CO2Emis, 5-minute CO2 intensity per price area (raw).")
def co2emis_raw(context: dg.AssetExecutionContext, settings: PipelineSettings):
    result = pipeline.fetch_dataset(settings.to_settings(), "CO2Emis")
    context.add_output_metadata(_fetch_meta(result))
    return result


@dg.asset(group_name="fetch", description="Energinet ProductionConsumptionSettlement, hourly (raw).")
def production_consumption_raw(context: dg.AssetExecutionContext, settings: PipelineSettings):
    result = pipeline.fetch_dataset(settings.to_settings(), "ProductionConsumptionSettlement")
    context.add_output_metadata(_fetch_meta(result))
    return result


@dg.asset(group_name="fetch", description="Energinet DeclarationGridEmission, settled-method hourly CO2 (raw).")
def declared_emission_raw(context: dg.AssetExecutionContext, settings: PipelineSettings):
    result = pipeline.fetch_dataset(settings.to_settings(), "DeclarationGridEmission")
    context.add_output_metadata(_fetch_meta(result))
    return result


@dg.asset(group_name="validate", description="Rule-based checks; every row kept with a quality label.")
def validated(context: dg.AssetExecutionContext, settings: PipelineSettings, co2emis_raw, declared_emission_raw, production_consumption_raw):
    out = pipeline.validate_all(co2emis_raw, declared_emission_raw, production_consumption_raw, settings.to_settings())
    flags = out["flags"]
    context.add_output_metadata(
        {
            "total_flags": len(flags),
            "errors": int((flags["severity"] == "error").sum()),
            "warnings": int((flags["severity"] == "warning").sum()),
            "by_rule": dg.MetadataValue.json(flags["rule"].value_counts().to_dict()),
        }
    )
    return out


@dg.asset(group_name="store", description="Raw, validated and flag tables written to SQLite.")
def sqlite_db(context: dg.AssetExecutionContext, settings: PipelineSettings, co2emis_raw, declared_emission_raw, production_consumption_raw, validated):
    s = settings.to_settings()
    run_id = pipeline.store_all(co2emis_raw, declared_emission_raw, production_consumption_raw, validated, s)
    context.add_output_metadata({"run_id": run_id, "db_path": s.db_path})
    return run_id


@dg.asset(group_name="report", description="Quality report, scope 2 example (synthetic site) and HTML dashboard.")
def report(context: dg.AssetExecutionContext, settings: PipelineSettings, sqlite_db):
    s = settings.to_settings()
    summary = pipeline.build_report(s, sqlite_db, pipeline.scope2_example(s))
    context.add_output_metadata(
        {
            "total_flags": summary["total_flags"],
            "scope2_kg_co2_synthetic_site": summary["scope2_synthetic_site"]["emissions_kg_co2"],
            "dashboard": dg.MetadataValue.path(f"{s.report_dir}/dashboard.html"),
        }
    )
    return summary


all_assets = [co2emis_raw, declared_emission_raw, production_consumption_raw, validated, sqlite_db, report]

full_pipeline = dg.define_asset_job("full_pipeline", selection=dg.AssetSelection.assets(*all_assets))

defs = dg.Definitions(
    assets=all_assets,
    jobs=[full_pipeline],
    resources={"settings": PipelineSettings()},
)
