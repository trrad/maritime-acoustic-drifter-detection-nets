## Context

Post Stage 1 (ballast depth pin) and Stage 2 (CLI surface
formalization), two pathologies remain in the drifter dynamics
pipeline:

**Truth side** — `dynamics.py::propagate_truth`:
- `DRIFTING_SURFACE_POSE` / `BALLAST_DRIFTING_POSE` branches run
  `new_state[3] += vel_noise[0]` every tick, with
  `vel_noise = rng.normal(0.0, 0.005 * sqrt(dt))`. Over 12 h
  (720 ticks at dt=60 s), `σ_v ≈ 0.005 * sqrt(720 * 60) ≈ 1.0 m/s` —
  the truth drifter's residual velocity is a free-running Brownian
  motion with σ ~ 1 m/s by end of day, which dwarfs typical
  deployment-region currents (0.1–0.5 m/s).
- Position advection: `new_state[0] += (new_state[3] + current_vx) * dt`.
  The residual enters position linearly, so drifter position also
  accumulates Brownian error — RMS ≈ σ_v * sqrt(N) * dt → hundreds of
  meters per 12 h from the residual alone, ON TOP OF the proper
  current-driven advection.

**PF side** — `pf_float.py::_advect_horizontal_and_velocity`:
- Same RW pathology: `particles[:, idx.vx] += vel_noise[:, 0]` with
  `vel_noise ~ N(0, 0.05 * sqrt(dt))`. Default PFFloatConfig sets
  `process_noise_vel_ms_per_sqrt_s = 0.05` — 10× truth, which is
  correct if the residual were physically meaningful. It isn't.

**The key observation:** the drifter velocity residual — `vx` relative
to climatology / field-current mean — has no physical mechanism for
persistence in M1. Turbulence, wind gusts, internal waves perturb the
drifter, but those perturbations are tick-uncorrelated at the 60 s
scale. Treating the residual as a RW falsely assumes tick-to-tick
correlation. Treating it as a fresh Gaussian per tick is faithful.

## Goals / Non-Goals

**Goals:**
- Truth drifter velocity residual stays bounded — `|residual| < σ_perturb`
  on any tick, no accumulation over a 12 h run.
- PF particle velocity residual per tick is drawn from a climatology-
  variance-scaled Gaussian, giving the particle cloud physically
  reasonable spread without the runaway RW.
- End-to-end milestone: in a LoRa-only PF run with 2-anchor geometry
  in range, pure-drifter position RMSE is below 100 m median over a
  1 h run (down from the current RW-polluted baseline of several
  hundred meters).

**Non-Goals:**
- Changing the meaning of the velocity state dim. The residual
  semantic is preserved — `vx` is still "drifter velocity minus
  climatology / field-current mean at the drifter's position". The
  position formula `pos += (vx + cur_vx) * dt + pos_noise` is
  unchanged on both truth and PF sides.
- Removing the velocity state dim. Dead reckoning across
  observation-silent ticks (which is the M1 norm outside LoRa
  cycles) still needs a velocity slot to integrate forward.
- IMU observation model. Stage 4 handles IMU likelihood; Stage 3
  stays out of that path.
- Changing the vz axis. vz is pinned at 0 for pure drifters and at
  its initial value for ballast drifters (post Stage 1). No change
  here.
- Regenerating the golden trace as part of this change. The
  regeneration is a deliberate follow-up commit with its own message,
  not a side effect. (The gen-scenario script's byte output shifts
  because truth dynamics shift — this is expected.)

## Decisions

### D1. Velocity residual is re-sampled per tick, not accumulated
Both truth and PF replace the `state[3] += vel_noise[0]` idiom with
`state[3] = rng.normal(0.0, σ(position))` (PF) or
`state[3] = rng.normal(0.0, σ_perturb)` (truth).

This is the cleanest fix: no state semantic change, no position-
formula change, bounded evolution.

Alternative considered: keep RW but clamp via a Langevin-style
mean-reversion term. Rejected as over-engineering — M1 has no
observation of the residual, so a sophisticated temporal model buys
nothing.

### D2. Truth velocity perturbation scale
`DRIFTER_VEL_PERTURBATION_MS = 0.02` (m/s), applied as the stddev of a
per-tick Gaussian sample. At dt=60 s this gives a velocity residual
envelope of ±0.06 m/s (3σ), small compared to typical current
magnitudes (0.1–0.5 m/s).

The constant `VEL_PROCESS_NOISE_MS_PER_SQRT_S` is removed from the
module (it has no remaining use). If other modules import it, they
are updated to use the new name.

### D3. PF particle velocity perturbation scale
Per-particle σ computed at the particle's lat/lon via climatology:

```python
# Inside predict, after lat_arr/lon_arr and cur_vx/cur_vy are ready:
var_vx = climatology.var_vx_ms2[lat_idx, lon_idx]
var_vy = climatology.var_vy_ms2[lat_idx, lon_idx]
sigma_vx = np.sqrt(var_vx) + floor
sigma_vy = np.sqrt(var_vy) + floor
particles[:, idx.vx] = rng.normal(0.0, sigma_vx, size=n)
particles[:, idx.vy] = rng.normal(0.0, sigma_vy, size=n)
```

`floor = config.process_noise_vel_ms_per_sqrt_s` (default 0.02). The
config field is preserved so the CLI `--predict-noise-vel` override
continues to work; its semantic is now "the minimum velocity residual
sampling σ even when climatology variance is zero".

Rationale for the floor: a synthetic climatology (e.g. in a unit
test) may report `var_vx = 0` everywhere. Without a floor, the
particle velocity collapses to exactly 0 and the particle cloud
loses its velocity degree of freedom. With a small floor, the cloud
stays non-degenerate.

### D4. Existing vel_noise buffer
The `vel_noise` array drawn at the top of `predict` was previously
used to RW `vx, vy, vz`. Under the new model:

- `vx, vy` are re-sampled (not added to) — the draw is unused for
  these axes.
- `vz` is not updated in M1 (pure drifters pin depth at 0, ballast
  drifters pin depth at initial).

To preserve RNG stream order (seeded-determinism contract), the
`vel_noise` draw is preserved at the top of predict. Its values are
discarded in the new code path. Explicit comment.

Alternative considered: drop the `vel_noise` draw. Rejected because
it changes RNG stream order across the predict call, which may cause
existing seeded-determinism tests to fail in confusing ways. Keeping
the draw is cheap and preserves test stability.

### D5. Tick-uncorrelated vs. persistence
Velocity residuals are **uncorrelated tick-to-tick**. Each tick's
sample is independent of the last. Rationale: at 60 s resolution,
M1's deployment regime (LoRa TDMA cycles ~1 h), tick-correlation
buys nothing — the PF's belief about "what is the drifter's velocity
residual right now" is dominated by the climatology prior, not by
memory of last tick's residual.

This is a deliberate simplification; M2 with an observing velocity
sensor (Doppler profiler) could reintroduce persistence with a
proper measurement model.

### D6. State-dim kept, not projected out
Velocity is still a state slot (`(vx, vy, vz)` at indices 3-5). Dead
reckoning in sensor-silent ticks requires reading the current
velocity belief to advance position. The residual model keeps
compatibility with that.

## Key Type Contracts

- `propagate_truth(node: Node, dt_sec: float, env: PhysicsEnv, rng: np.random.Generator) -> np.ndarray`
  — signature unchanged; behavior changes for the two drifting
  branches.
- `PFFloat.predict(dt_sec: float) -> None` — signature unchanged; the
  `_advect_horizontal_and_velocity` helper computes velocity afresh.
- `PFFloatConfig.process_noise_vel_ms_per_sqrt_s: float` — field
  preserved; interpretation changes to "per-tick sampling σ floor".
  Default changes from 0.05 to 0.02.
- `DRIFTER_VEL_PERTURBATION_MS: float = 0.02` — new module-level
  constant in `dynamics.py` replacing the retired
  `VEL_PROCESS_NOISE_MS_PER_SQRT_S`.

## Risks / Trade-offs

- [Golden-trace divergence] → Truth byte output shifts because
  velocity evolution shifts. Mitigation: explicit follow-up commit
  regenerating the golden trace, with a commit message that points
  to this change.
- [Existing tests assume RW semantics] → Tests like
  `test_dynamics.py::test_drifting_pose_accumulates_velocity_noise`
  (if it exists under that name) will fail and need rewriting. The
  apply step discovers and reworks them.
- [PF sampling at dt=60 s is the same σ as at dt=1 s] →
  Intentionally. Per-tick sampling has no dt dependence; the
  `process_noise_vel_ms_per_sqrt_s` field name is slightly misleading
  under the new semantic (the `_per_sqrt_s` scaling no longer
  applies). Mitigation: document the semantic in the config
  docstring; defer rename to a follow-up simplify pass.
- [Dashboard visual change] → Drifter trajectories will look
  smoother (no RW jitter on top of current-driven motion).
  Mitigation: run the dashboard on a before/after pair of runs and
  visually confirm trails now follow streamlines.

## Migration Plan

1. Land delta specs + new substance tests + code changes in this
   change's `/opsx:apply`.
2. Rework existing velocity-related tests as part of apply.
3. `/opsx:verify` → `/opsx:sync` → `/opsx:archive`.
4. Follow-up commit: regenerate golden trace with a pointed commit
   message.
5. Manual dashboard check.

## Open Questions

- Should `process_noise_vel_ms_per_sqrt_s` be renamed to drop the
  `_per_sqrt_s` suffix? Deferred — scope here is semantic change.
