#!/usr/bin/env bash
# Run the science sweep as a sequence of per-(σ_m, density) chunks. Each
# chunk is a separate invocation of _fleet_sweep_v0.py — fresh workers,
# fresh JAX state, no multi-cell GPU memory accumulation. Each chunk
# writes to its own run_dir; the morning summary script reads across
# them.
#
# Empirically: with N_PROCS=8 workers and the platform allocator, a
# single invocation handles ~5 cells before per-worker GPU memory grows
# past ~1.7 GiB and pushes total usage past 16 GiB. 8 cells per chunk
# (2 surfacing × 2 cadence × 2 mission_h, with single σ_m and density)
# is right at the edge but usually fits because per-worker memory grows
# gradually rather than per-cell.
#
# Usage:
#   ./_run_chunked_sweep.sh <run_id_prefix>
#
# Outputs run dirs at figures/sweep_runs/<run_id_prefix>__s<sigma>_<density>/

set -u

PREFIX="${1:-science_sweep_chunked}"
TS=$(date +%Y%m%d_%H%M%S)
LOG_ROOT=/tmp/sweep_chunks_${TS}
mkdir -p "$LOG_ROOT"

cd "$(dirname "$0")"

DENSITIES=(D6_12_subset D6_24_extended)
SIGMAS_M=(20 50 100 200)
POLICIES=(fixed_6h post_event_30m_12h)

# Empirically observed: a single _fleet_sweep_v0.py invocation OOMs on
# the 6th cell when 8 workers each accumulate ~1.4-2.2 GB GPU memory
# across cells (5 cells succeed, 6th fails). Chunk by (density, σ_m,
# policy) → 4 cells per chunk (2 cadence × 2 mission_h), comfortably
# under the 5-cell ceiling. 16 chunks total for 64 cells.
echo "=== chunked sweep: prefix=$PREFIX, ts=$TS ==="
echo "  densities: ${DENSITIES[*]}"
echo "  σ_m:       ${SIGMAS_M[*]}"
echo "  policies:  ${POLICIES[*]}"
echo "  per chunk: 2 cadence × 2 mission_h = 4 cells"
echo "  total: $((${#DENSITIES[@]} * ${#SIGMAS_M[@]} * ${#POLICIES[@]})) chunks × 4 cells = $((${#DENSITIES[@]} * ${#SIGMAS_M[@]} * ${#POLICIES[@]} * 4)) cells"
echo "  logs: $LOG_ROOT/"
echo ""

T_START=$(date +%s)
N_CHUNKS=$((${#DENSITIES[@]} * ${#SIGMAS_M[@]} * ${#POLICIES[@]}))
CHUNK_IDX=0

for density in "${DENSITIES[@]}"; do
    for sigma in "${SIGMAS_M[@]}"; do
        for policy in "${POLICIES[@]}"; do
            CHUNK_IDX=$((CHUNK_IDX + 1))
            run_id="${PREFIX}__${density}__s${sigma}__${policy}__${TS}"
            log="$LOG_ROOT/chunk_${density}_s${sigma}_${policy}.log"
            echo ""
            echo "=== chunk $CHUNK_IDX/$N_CHUNKS: density=$density σ_m=${sigma}m policy=$policy ($(date)) ==="
            echo "  run_id: $run_id"
            echo "  log:    $log"

            XLA_PYTHON_CLIENT_PREALLOCATE=false \
            XLA_PYTHON_CLIENT_ALLOCATOR=platform \
            FLEET_USE_JAX_MPC=1 \
            FLEET_SWEEP_LORA_SIGMAS_M="$sigma" \
            FLEET_SWEEP_CONTROL_CADENCES_S=7200,14400 \
            FLEET_SWEEP_RUN_HOURS_LIST=168,336 \
            FLEET_SWEEP_CAMPAIGN_MODE=single \
            FLEET_SWEEP_ONLY_DENSITIES="$density" \
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
echo "=== ALL CHUNKS DONE in $((T_TOTAL / 60)) min ($T_TOTAL s) ==="
echo "  logs:    $LOG_ROOT/"
echo "  outputs: figures/sweep_runs/${PREFIX}__*"
