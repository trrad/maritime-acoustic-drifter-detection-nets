#!/usr/bin/env bash
# D6_24_extended retry: re-run the failed half of science_v1 at 2 cells
# per chunk instead of 4. N=24 drifters use more per-worker GPU memory
# than N=12, so the empirical 5-cell per-pool ceiling becomes a 2-3
# cell ceiling. The 4-cells-per-chunk layout (which worked for N=12)
# OOM'd at the 3rd cell within each chunk for N=24.
#
# Usage: ./_run_d6_24_retry.sh [run_id_prefix]
#
# Splits each (σ_m, policy) further by cadence → 2 cells per chunk
# (just 2 mission_h values per chunk). 32 chunks of 2 cells = 64 cells
# total for D6_24, but each chunk is fast (~25-50 min) and pool-fresh.

set -u

PREFIX="${1:-science_v1_d6_24_retry}"
TS=$(date +%Y%m%d_%H%M%S)
LOG_ROOT=/tmp/sweep_chunks_d6_24_${TS}
mkdir -p "$LOG_ROOT"

cd "$(dirname "$0")"

DENSITY=D6_24_extended
SIGMAS_M=(20 50 100 200)
POLICIES=(fixed_6h post_event_30m_12h)
CADENCES=(7200 14400)

echo "=== D6_24 retry: prefix=$PREFIX, ts=$TS ==="
echo "  density: $DENSITY"
echo "  σ_m:     ${SIGMAS_M[*]}"
echo "  policies: ${POLICIES[*]}"
echo "  cadences: ${CADENCES[*]}"
echo "  per chunk: 2 mission_h = 2 cells"
echo "  total: $((${#SIGMAS_M[@]} * ${#POLICIES[@]} * ${#CADENCES[@]})) chunks × 2 cells = $((${#SIGMAS_M[@]} * ${#POLICIES[@]} * ${#CADENCES[@]} * 2)) cells"
echo "  logs: $LOG_ROOT/"
echo ""

T_START=$(date +%s)
N_CHUNKS=$((${#SIGMAS_M[@]} * ${#POLICIES[@]} * ${#CADENCES[@]}))
CHUNK_IDX=0

for sigma in "${SIGMAS_M[@]}"; do
    for policy in "${POLICIES[@]}"; do
        for cadence in "${CADENCES[@]}"; do
            CHUNK_IDX=$((CHUNK_IDX + 1))
            run_id="${PREFIX}__${DENSITY}__s${sigma}__${policy}__c${cadence}__${TS}"
            log="$LOG_ROOT/chunk_s${sigma}_${policy}_c${cadence}.log"
            echo ""
            echo "=== chunk $CHUNK_IDX/$N_CHUNKS: σ_m=${sigma}m policy=$policy cad=${cadence}s ($(date)) ==="
            echo "  run_id: $run_id"
            echo "  log:    $log"

            XLA_PYTHON_CLIENT_PREALLOCATE=false \
            XLA_PYTHON_CLIENT_ALLOCATOR=platform \
            FLEET_USE_JAX_MPC=1 \
            FLEET_SWEEP_LORA_SIGMAS_M="$sigma" \
            FLEET_SWEEP_CONTROL_CADENCES_S="$cadence" \
            FLEET_SWEEP_RUN_HOURS_LIST=168,336 \
            FLEET_SWEEP_CAMPAIGN_MODE=single \
            FLEET_SWEEP_ONLY_DENSITIES="$DENSITY" \
            FLEET_SWEEP_ONLY_POLICIES="$policy" \
            FLEET_SWEEP_N_PROCS=8 \
            FLEET_SWEEP_RUN_ID="$run_id" \
            uv run --with xarray,netCDF4,numpy,matplotlib,scipy,filterpy --with "jax[cuda12]" \
                python _fleet_sweep_v0.py > "$log" 2>&1
            rc=$?
            if [ $rc -ne 0 ]; then
                echo "  ⚠ chunk failed (exit $rc) — logs in $log"
                echo "  continuing to next chunk"
            else
                elapsed=$(( $(date +%s) - T_START ))
                echo "  ✓ chunk done at +${elapsed}s"
            fi
        done
    done
done

T_END=$(date +%s)
T_TOTAL=$((T_END - T_START))
echo ""
echo "=== ALL D6_24 CHUNKS DONE in $((T_TOTAL / 60)) min ($T_TOTAL s) ==="
echo "  logs:    $LOG_ROOT/"
echo "  outputs: figures/sweep_runs/${PREFIX}__*"
