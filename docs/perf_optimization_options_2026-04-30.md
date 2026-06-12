# Perf optimization options for the maritime fleet sim
**Date:** 2026-04-30
**Source profile:** `/tmp/fleet_sim_profile.prof` — 24h × 1 drifter,
fixed_6h policy, MPC controller (horizon_n=12, beam_width=200,
posterior_cvar scoring with 5 draws), no multiprocessing.

## What the profile shows

**Wall: 267s for 24h × 1 drifter.** Extrapolates linearly to ~31 min
per 168h drifter, matching observed. With 16 drifters in parallel on
12 workers the wall per cell is ~65 min — 4 of which is worker init,
~60 min is sim.

The hot path is dominated by **MPC field sampling**, not by PF or
controller logic:

```
choose_depth (MPC)                          177s (66% wall)
└── get_current_at_batched_draw              152s
    └── truth_field.sample_batched           119s
        └── scipy RegularGridInterpolator    164s cum, 86s self
            └── _evaluate_linear              69s self  ← THE HOT LOOP

PF predict                                   43s (16% wall)
einsum (bias-field projections)              26s (10% wall)
posterior_draws resampling (per-plan)        20s (7%)
cholesky (bias posterior draws)              10s
```

**Single biggest cost:** scipy's `RegularGridInterpolator` —
1,088,560 calls, 164s cumulative. Most of that is its Python-level
bilinear-interpolation loop in `_evaluate_linear`. Each call has
significant Python overhead: `_find_indices`, `_find_out_of_bounds`,
`_prepare_xi`, list comprehensions, np.array reconstructions.

The MPC's beam search IS already vectorized across (beam × K_depths
× horizon_step), so the call count isn't the issue per se — it's
that each batched call still routes through scipy RGI which is
slow per element.

## Why this matters for the research roadmap

To sweep across (polygon, density, regime, horizon, surfacing
policy) at 65 min/cell, with even 50 cells per axis combo we're
looking at 50+ hours per axis sweep, days per multi-axis study. JAX
or similar is not optional for the planned parameter space —
it's the gating constraint.

## Optimization options, ranked

### A. Custom batched bilinear interpolator (numpy)
**Effort:** 1–2 days. **Speedup:** 3–5×.

Replace scipy's `RegularGridInterpolator` for the field-sampling
hot path with a custom numpy implementation that takes batched
`(N,)` lat/lon/depth arrays and returns batched `(N,)` u/v values
in one vectorized op. The math is straightforward:

```python
def sample_batched_v2(lats, lons, depths, t_sec, ...):
    # 1. searchsorted into lat_axis, lon_axis, depth_axis (vectorized)
    # 2. compute bilinear weights (4 weights × N points)
    # 3. gather corner values via fancy indexing
    # 4. weighted sum
    # 5. handle out-of-bounds with NaN
```

The win comes from eliminating per-call Python overhead (the
`_evaluate_linear` 69s self-time), not from algorithmic
improvement. Sets up future JAX work because the new function
has a clean batched signature.

Risk: subtle differences from RGI's convention (cell-centered vs
corner-centered, edge handling, NaN propagation). Need
correctness tests against scipy on a fixed batch.

### B. Numba JIT on the hot loops
**Effort:** 2–3 days. **Speedup:** 10–20× on inner loop, 3–5×
overall (since not everything is JIT-able cleanly).

Apply `@numba.njit` to:
1. The custom batched interpolator from (A)
2. The MPC's per-substep dynamics rollout (`ballast_dynamics.step`)
3. The PF predict step's per-particle advection

Constraints: numba doesn't play well with our nested object types
(`KnowledgeSource` protocol, `BallastState` dataclass). Need to
pass primitive numpy arrays into JIT'd functions. Means refactoring
the dynamics-rollout call signature.

Risk: numba's compilation cache misses on type changes can hide
in interactive use; unit tests are mandatory. Also numba doesn't
support all numpy features (e.g., `np.searchsorted` with `side='right'`
took a while to land).

### C. JAX rewrite of MPC + PF + field sampling
**Effort:** 3–4 weeks. **Speedup:** 50–100× on a GPU,
maybe 5–10× CPU-only.

The big architectural lift. Rewrite the field sampling, MPC inner
loop, PF predict, and bias filter in `jax.numpy` with `jax.jit`,
`jax.vmap`. The win comes from:
- Single GPU kernel for the whole beam-search rollout
- `vmap` over particles + beam + draws + depths simultaneously
- Eliminates Python overhead entirely on the inner loop
- `pmap` over drifters across multiple GPU devices (or CPU cores)
  if available

JAX-specific challenges:
- Random number streams (PF resampling, posterior draws) need
  `jax.random.PRNGKey` plumbing instead of numpy generators
- Variable-length / data-dependent control flow (event-triggered
  surfacing) requires `jax.lax.cond` or static-shape masks
- `filterpy.systematic_resample` is numpy-only; would need to
  reimplement in JAX
- NEMO + bias + noise field data must fit on a GPU device
  (probably fine — bbox is small, ~100MB)
- The `RegularGridInterpolator` equivalent in JAX is something
  like `jax.scipy.ndimage.map_coordinates` or a custom
  bilinear-interp kernel

Risk: this is a multi-week effort with significant architectural
churn. Worth it only if the parameter-space goals are firm —
which the user just said they are.

### D. Algorithmic reductions
**Effort:** half a day each. **Speedup:** 1.5–3× cumulative.

Doesn't fix the underlying RGI cost but reduces call count:
1. **Cache posterior draws** across consecutive MPC plans — the
   bias posterior moves slowly between plans (30 min cadence vs
   bias correlation length ~hours), so 5 draws per plan can often
   be reused for 2–3 plans. Saves ~7% wall.
2. **Coarsen dt_sec for non-critical sweeps** — e.g., go from 600s
   to 1200s for parameter-space-mapping sweeps that don't need
   high temporal fidelity. Halves PF + MPC tick count. ~1.5×.
3. **Reduce beam_width or horizon for screening sweeps** — we
   already know `b=200, h=12` is at the closes-to-brute-force
   sweet spot from the site-authority work. For first-pass
   screens of polygon×density combos, `b=100, h=8` would lose
   ~2-5% of MPC quality but save ~50% of MPC wall.
4. **Profile-guided micro-optimizations** — e.g., `bias_field.indices`
   is 322K calls at 16.6s cumulative; `column_stack` and `asarray`
   in the inner loop add up; `precompute_posterior_draws` could
   incrementally update rather than full-resample.

These are useful as quick wins regardless of which path (A–C) is
chosen.

## Recommended path

**Phase α (1–2 weeks): Land A + numba-light B + (D.1, D.4).**
This gets us 5–10× speedup with low architectural risk and sets
up the data flow for a future JAX migration. Expected per-cell
wall: ~6–13 min instead of ~60 min. That's enough to make a
50-cell polygon×density sweep tractable in 1–2 days.

**Phase β (3–4 weeks, after Phase α validates the new field-
sampling path): JAX-ify the MPC + PF on GPU.** Target the full
rewrite with the clean batched interfaces from Phase α as the
boundary between Python orchestration and JAX-jitted hot loops.
Expected per-cell wall: ~30–60s on a single GPU. Makes the
multi-axis sweeps the user described (polygon × density × regime
× horizon × policy) routine — hundreds of cells per overnight run.

**Phase γ (ongoing): Multi-host parallelism.** Once JAX is in
place, scaling beyond one box is `jax.pmap` plus a job dispatcher.
Useful when the parameter space genuinely exceeds what fits on
one machine.

## Concrete next iteration

Phase α steps in execution order:

1. Write `truth_field.sample_batched_v2` as a custom numpy
   bilinear interpolator with batched lat/lon/depth/t inputs.
   Unit-test against `RegularGridInterpolator` on a fixed batch
   for parity (within 1e-9). Replace the call in
   `_RealCurrents.sample_batched` and `_NemoPrior.sample_batched`.
2. Same for the bias-field interpolator (`bias_field.indices` /
   `gather` paths) — vectorize the per-particle indexing.
3. Profile again. Measure speedup. Identify next-largest hotspot.
4. Apply numba `@njit` to the new batched interpolators and to
   `ballast_dynamics.step` if it's still hot.
5. Add D.1 (posterior-draws cache) — likely just 20–30 LOC.
6. Re-run the smoke profile, validate end-to-end correctness on
   a 24h sim against the existing baseline (within numerical
   tolerance), then re-run a single Phase 3 cell to confirm
   total wall savings and no behavioral change.

After Phase α lands, decide whether the timing pressures
justify the Phase β JAX investment, or whether the parameter
space can be addressed with the faster CPU pipeline alone.
