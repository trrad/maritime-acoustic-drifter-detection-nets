# Offshore Vancouver Island — 2024-10-15 bundled fixture

CMEMS ocean current data for the `maritime-real-current-data` OpenSpec
change. Two independent products: the truth-side nowcast and the
onboard-side climatology. **These MUST come from different product
families** — see data-provenance invariant in `docs/simulation_integrity.md`.

## Contents

| File | Product ID | Role | Size | Coverage |
|------|-----------|------|------|----------|
| `truth_cmems_forecast_3h.nc` | `cmems_mod_glo_phy_anfc_0.083deg_PT1H-m` | **Truth** (this day's actual currents) | 26 KB | 5×5 grid × 4 hourly steps |
| `climatology_cmems_monthly_climatology.nc` | `cmems_mod_glo_phy_my_0.083deg-climatology_P1M-m` | **Onboard climatology** (multi-year monthly means) | 27 KB | 5×5 grid × 12 monthly slices |

## Bbox and window

- Bbox: `(47.4°N, -126.6°W, 47.8°N, -126.2°W)` — open Pacific SW of
  Vancouver Island. ~45 km offshore of Cape Flattery / Tofino.
- Truth window: `2024-10-15 12:00–15:00 UTC` (3-hour slice).
- Climatology: time-invariant; consumer selects the relevant month
  (e.g., index 9 for October) when building the onboard prior.
- Depth: surface slice (0.494 m is the shallowest native level).
- Variables: `uo` (eastward velocity, m/s), `vo` (northward velocity, m/s).

## Why *not* Salish Sea

Initial fetch target was Salish Sea (48.5°N, -123.5°W) — user-local,
known strong tidal regime. CMEMS global 1/12° masks this bbox
entirely as land (Salish archipelago unresolved at that resolution).
All-NaN returned. See plan notes on coastal-data-source follow-up
for downstream regional-product evaluation (CIOPS-West, NOAA WCOFS,
local ROMS runs).

## Regeneration / fetch commands

Requires: `copernicusmarine` package + valid Copernicus Marine
account (free registration at <https://data.marine.copernicus.eu/register>).

One-time login:

```bash
copernicusmarine login
```

Fetch both files:

```bash
# Truth — operational nowcast/forecast, hourly
copernicusmarine subset \
    --dataset-id cmems_mod_glo_phy_anfc_0.083deg_PT1H-m \
    --variable uo --variable vo \
    --minimum-longitude -126.6 --maximum-longitude -126.2 \
    --minimum-latitude 47.4 --maximum-latitude 47.8 \
    --minimum-depth 0 --maximum-depth 1 \
    --start-datetime 2024-10-15T12:00:00 --end-datetime 2024-10-15T15:00:00 \
    --output-directory rtl/vectors/maritime/data/real_currents/offshore_vi_2024_10_15 \
    --output-filename truth_cmems_forecast_3h.nc \
    --overwrite

# Climatology — GLORYS12V1 monthly climatology (pre-built by CMEMS)
copernicusmarine subset \
    --dataset-id cmems_mod_glo_phy_my_0.083deg-climatology_P1M-m \
    --variable uo --variable vo \
    --minimum-longitude -126.6 --maximum-longitude -126.2 \
    --minimum-latitude 47.4 --maximum-latitude 47.8 \
    --minimum-depth 0 --maximum-depth 1 \
    --output-directory rtl/vectors/maritime/data/real_currents/offshore_vi_2024_10_15 \
    --output-filename climatology_cmems_monthly_climatology.nc \
    --overwrite
```

## Observed structure (sanity check)

- Truth `uo`: -0.24 to 0.19 m/s, mean -0.08 (weak westward on average).
- Truth `vo`: 0.09 to 0.61 m/s, mean 0.40 (strong northward — California Current / North Pacific Current).
- Climatology `uo`: -0.008 to 0.053 m/s (all months, much smaller magnitudes).
- Climatology `vo`: -0.074 to 0.073 m/s (all months).

The climatology time-average is ~5× smaller in magnitude than this
specific day's truth flow. This gap is what makes the PF's inference
problem real: the onboard prior under-estimates today's actual
advection by hundreds of meters per hour. LoRa fixes are what
constrain it back.

## Licenses & citation

Both products are Copernicus Marine Service data (free, open access
with citation). Citation string:

> E.U. Copernicus Marine Service Information; Global Ocean Physics
> Analysis and Forecast (GLOBAL_ANALYSISFORECAST_PHY_001_024);
> <https://doi.org/10.48670/moi-00016>
>
> E.U. Copernicus Marine Service Information; Global Ocean Physics
> Reanalysis (GLOBAL_MULTIYEAR_PHY_001_030);
> <https://doi.org/10.48670/moi-00021>
