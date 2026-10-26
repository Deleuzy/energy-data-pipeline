# Data-quality report

Run `20261026T134926Z-5056e6`, window 2026-10-01T00:00 to 2026-10-15T00:00 (UTC, end exclusive).

## Sources

| dataset | fetched_at | n_records | url |
|---|---|---|---|
| CO2Emis | 2026-10-26T13:37:47+00:00 | 5000 | https://api.energidataservice.dk/dataset/CO2Emis?start=2026-10-01T00%3A00&end=2026-10-15T00%3A00&timezone=UTC&columns=Minutes5UTC%2CPriceArea%2CCO2Emission&sort=Minutes5UTC+ASC%2CPriceArea+ASC&offset=0&limit=5000 |
| CO2Emis | 2026-10-26T13:37:47+00:00 | 3062 | https://api.energidataservice.dk/dataset/CO2Emis?start=2026-10-01T00%3A00&end=2026-10-15T00%3A00&timezone=UTC&columns=Minutes5UTC%2CPriceArea%2CCO2Emission&sort=Minutes5UTC+ASC%2CPriceArea+ASC&offset=5000&limit=5000 |
| DeclarationGridEmission | 2026-10-26T13:49:13+00:00 | 2016 | https://api.energidataservice.dk/dataset/DeclarationGridEmission?start=2026-10-01T00%3A00&end=2026-10-15T00%3A00&timezone=UTC&columns=HourUTC%2CPriceArea%2CFuelAllocationMethod%2CVersion%2CCO2PerkWh&sort=HourUTC+ASC%2CPriceArea+ASC&offset=0&limit=5000 |
| ProductionConsumptionSettlement | 2026-10-26T13:37:48+00:00 | 672 | https://api.energidataservice.dk/dataset/ProductionConsumptionSettlement?start=2026-10-01T00%3A00&end=2026-10-15T00%3A00&timezone=UTC&columns=HourUTC%2CPriceArea%2CGrossConsumptionMWh%2COffshoreWindLt100MW_MWh%2COffshoreWindGe100MW_MWh%2COnshoreWindLt50kW_MWh%2COnshoreWindGe50kW_MWh%2CSolarPowerLt10kW_MWh%2CSolarPowerGe10Lt40kW_MWh%2CSolarPowerGe40kW_MWh%2CSolarPowerSelfConMWh&sort=HourUTC+ASC%2CPriceArea+ASC&offset=0&limit=5000 |

## Rows per table and quality label

- `raw_co2emis`: 8062
- `raw_declared_emission`: 2016
- `raw_production_consumption`: 672
- `co2_5min_validated`: {'error': 2, 'ok': 8062}
- `co2_hourly`: {'ok': 670, 'warning': 2}
- `co2_declared_hourly`: {'ok': 672}
- `consumption_hourly`: {'ok': 672}

## Flags by rule

| dataset | price_area | rule | severity | count |
|---|---|---|---|---|
| CO2Emis | DK1 | missing_slot | error | 1 |
| CO2Emis | DK2 | missing_slot | error | 1 |
| CO2Emis_hourly | DK1 | incomplete_hour | warning | 1 |
| CO2Emis_hourly | DK2 | incomplete_hour | warning | 1 |

## Hourly CO2 intensity (usable hours)

| price_area | usable_hours | mean_g_per_kwh | min_g_per_kwh | max_g_per_kwh | consumption_weighted_g_per_kwh | gross_consumption_gwh |
|---|---|---|---|---|---|---|
| DK1 | 336.0 | 111.4 | 36.5 | 231.5 | 110.7 | 971.8 |
| DK2 | 336.0 | 60.7 | 16.3 | 163.2 | 62.4 | 585.7 |

## Scope 2 example (SYNTHETIC site, real intensity)

- Method: hourly, location-based, price area DK1
- Electricity: 12083.21 kWh, covered 12083.21 kWh (100.0 %)
- Emissions: 1333.482 kg CO2 (weighted intensity 110.36 g/kWh)
- Hours not covered: 0

## Real-time vs declared CO2 (125% method, Initial version)

| price_area | hours_compared | mean_realtime_g_per_kwh | mean_declared_g_per_kwh | mean_diff_g_per_kwh | mean_abs_diff_g_per_kwh |
|---|---|---|---|---|---|
| DK1 | 336.0 | 111.4 | 86.1 | 25.3 | 28.4 |
| DK2 | 336.0 | 60.7 | 65.2 | -4.4 | 19.1 |

## Flag detail (first 200)

| dataset | price_area | ts_utc | rule | severity | value | detail |
|---|---|---|---|---|---|---|
| CO2Emis | DK1 | 2026-10-13T07:15:00 | missing_slot | error | nan | no record for this 5min slot |
| CO2Emis | DK2 | 2026-10-13T07:15:00 | missing_slot | error | nan | no record for this 5min slot |
| CO2Emis_hourly | DK1 | 2026-10-13T07:00:00 | incomplete_hour | warning | 64.36 | mean of 11 of 12 usable 5-minute values |
| CO2Emis_hourly | DK2 | 2026-10-13T07:00:00 | incomplete_hour | warning | 118.0 | mean of 11 of 12 usable 5-minute values |
