#!/usr/bin/env bash
# Phase 2 parallel sweep launcher (Phase 2.1, layered noise M1).
#
# Grid: σ_fc ∈ {0, 8, 12, 15} cm/s × seeds ∈ {42, 43, 44, 45, 46}
#       × 8 stations × 4 policies × {no_learn, grid}
#
# σ_fc = 0 is the noise-free ceiling; {8, 12, 15} covers central basin
# → plume-adjacent → wind-event under the 5-component layered model
# (docs/reference/noise_model_design.md §3).
#
# 20 processes run in parallel; each handles all stations × policies ×
# configs for its (σ, seed) combo. Each takes ~55-65 min on one CPU
# core (plus ~3.5 min for the padded-cube noise build). On a 32-core
# box, all 20 fit concurrently → ~65 min wall clock.
#
# Results: figures/25_rbpf_v2_bias_learning_sigmaXX_seedYYY.json.
# Aggregate with `python 22_aggregate_sweep.py` after all finish.
#
# Log per-process: /tmp/phase2_sweep_sigmaXX_seedYYY.log.

set -u

SIGMAS=(0.00 0.08 0.12 0.15)
SEEDS=(42 43 44 45 46)

cd "$(dirname "$0")"

# Warm disk caches (bathymetry + velocity month) sequentially so
# parallel processes don't storm ERDDAP with concurrent fetches (it
# rate-limits to 1 request at a time → 429s otherwise).
echo "warming SalishSeaCast caches ($(date))"
uv run --with xarray,netCDF4,numpy,scipy python - <<'EOF'
from salishseacast_cache import (
    bbox_from_latlon, bbox_latlon_arrays, fetch_bbox_months,
)
bbox = bbox_from_latlon(49.15, 49.45, -123.95, -123.50)
_ = bbox_latlon_arrays(bbox)
_ = fetch_bbox_months(bbox, ["2023-04"], verbose=False)
print("caches warm")
EOF

echo "launching $((${#SIGMAS[@]} * ${#SEEDS[@]})) processes ($(date))"
for sigma in "${SIGMAS[@]}"; do
    for seed in "${SEEDS[@]}"; do
        tag="sigma${sigma#0.}_seed${seed}"
        log="/tmp/phase2_sweep_${tag}.log"
        SIGMA_FC_MS="$sigma" NOISE_SEED="$seed" \
            uv run --with xarray,netCDF4,numpy,matplotlib,scipy,filterpy \
            python 22_rbpf_v2_bias_learning.py > "$log" 2>&1 &
    done
done

echo "waiting for all processes ..."
wait
echo "done ($(date))"
echo "aggregating ..."
uv run --with matplotlib,numpy python 22_aggregate_sweep.py
