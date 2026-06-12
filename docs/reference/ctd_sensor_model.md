# CTD sensor model — T/S observations for plume-aware PF

**Status:** implemented (M2 of Phase 2.1, 2026-04-24).
Code: `experiments/harmonic_prototype/rbpf_prototype/sensors.py::CTDSensor`,
`experiments/harmonic_prototype/truth_field.py::TracerField`,
`experiments/harmonic_prototype/salishseacast_cache.py::PHYSICS_DATASET`
+ `fetch_bbox_months(..., include_tracers=True)`. Driver wiring:
`22_rbpf_v2_bias_learning.py` adds a `grid+ctd` config row to the
canonical Phase-2 sweep (no_learn → grid → grid+ctd).

Bias-learner integration is still v1 (per-leg LoRa-displacement Kalman
update); the salinity-residual-as-`δ_plume`-observation channel
described in §3 is a v3 addition queued for after the M2 sweep
characterises the CTD reweight's PFerr tightening.

Adds a temperature/conductivity sensor to the prototype drifter. The
node measures (T, S) at its current depth at a configurable cadence.
Two uses in the PF/bias-learning stack:

1. **Direct water-mass-based PF reweight** — compare observed (T, S)
   against the SalishSeaCast-predicted (T, S) at each particle's
   current (lat, lon, depth, t). Particles in water masses consistent
   with the observation get higher likelihood. Fires every tick even
   when submerged → tightens PF *between* LoRa surface events.

2. **Direct observation of the plume-offset error mode** — salinity
   residual (observed S − SalishSeaCast-predicted S) *identifies
   which plume-edge side the node is on*. A drifter in fresher-than-
   forecast water is inside a plume the model mis-placed. The
   residual parametrises the plume-front offset δ_plume (along-strait
   scalar), which is the dominant summer forecast-error mode in
   central SoG. This shifts bias learning from a 640-parameter grid
   basis to a 1–3 parameter physically-structured prior (see
   `noise_model_design.md` §6 and the Phase-2 discussion).

## 1. Why T/S instead of horizontal currents

The node's horizontal motion IS the current, so a "current sensor"
measures ambient flow only when the node isn't slipping. For a
near-Lagrangian passive ballast drifter, slip velocity is < 1 cm/s
(D'Asaro 2003 on Lagrangian floats; Laxague et al. 2018 on drogued
surface drifters) — the flow sensor's signal is at the slip-noise
floor, unusable. This was the conclusion of the earlier subagent
research logged in the parent Phase-2 plan and is why the plan
dropped a flow sensor as a between-ping observation.

T/S has no equivalent problem — it's a scalar property of the water
the node is sitting in, independent of whether the node moves with it.
Measurable with modern compact sensors to:
- Temperature: ± 0.005 °C (0.002 °C typical on SBE-class sensors;
  ± 0.01 °C on matchbox-scale).
- Conductivity / practical salinity: ± 0.005 PSU (SBE); ± 0.02 PSU
  compact.

SalishSeaCast validates T/S at the Salish-Sea-wide scale (Oldford
et al. 2025 HOTSSea validation, T bias 0–30 m of −0.39 °C in SoG
North, Willmott skill 0.97). Forecast error in T is bounded;
mis-placement of sharp gradients (thermoclines, plume front) is the
dominant source of local error.

**SalishSeaCast T/S bias numbers (Soontiens & Allen 2017, paper read
locally)** — these calibrate the expected model-vs-observation baseline
the CTD sensor observes:

- Salinity bias at Victoria (0-150 m, 40-day deep-water renewal window):
  -0.59 g/kg (base NEMO), improved to **-0.38 g/kg with Hollingsworth
  correction**. Best configuration at Steveston: -0.29 g/kg.
- Temperature bias at Victoria: +0.32 to +0.48 °C base; -0.11 °C with
  Hollingsworth correction (the outlier case; all others are +0.3-0.5 °C
  warm).
- Implication for CTD sensor model: the drifter's salinity residual
  `r_S = S_obs - S_SSC_predicted` has a **systematic bias of order
  -0.3 to -0.6 g/kg** at mid-basin SoG summer, on top of the
  plume-offset signal we actually want to observe. The sensor-model
  bias learner (future work) should separate (a) structural model bias
  from (b) plume-front-position error.
- Sensor noise σ_S = 0.02 PSU is << bias magnitude, so bias dominates
  the first surface-event residual; plume-offset signal is differential
  (spatial gradient), making it separable over a leg's trajectory.

## 2. Plume as the dominant observable error mode

The Fraser plume is the single largest summer forecast-error mode in
mid-SoG (see `noise_model_design.md` §3 and Agent C's analysis). Its
relevant properties:

- **Vertical extent**: 2–8 m thick surface layer at peak freshet
  (Halverson & Pawlowicz 2008).
- **Salinity contrast**: plume S ≈ 10–25 PSU depending on distance
  from river mouth; ambient SoG surface S ≈ 28–30 PSU. Gradient at
  the plume front: several PSU / km (Halverson's 13-year ferry
  salinity record).
- **Front position variability**: plume area swings 10× over the
  annual cycle (200–500 km² low flow → 1000–1500 km² high flow,
  Halverson & Pawlowicz 2008). Plume *location* is wind-sensitive
  (Pawlowicz et al. 2017 — plume *position* is highly sensitive to
  wind speed and direction while plume *area* is nearly
  wind-insensitive).
- **Persistence**: advection timescale at plume-edge speeds of
  30–50 cm/s is 1–2 days across a 10–30 km along-strait extent.

A drifter at surface or shallow depth observing S < SalishSeaCast-
predicted S at its current (lat, lon, t) is in a plume that's under-
forecast at that position — i.e., the actual plume front lies further
from the river mouth than the model thinks, δ_plume > 0. Conversely
S > predicted → δ_plume < 0. This is a direct scalar observation of
the error mode.

Temperature has similar but weaker signal: plume waters are slightly
warmer than ambient (freshwater runoff from lower-elevation
watersheds in late spring / early summer). T is useful as secondary
confirmation but salinity is the primary plume indicator.

## 3. Sensor model (observation equation)

At tick t, node at (lat, lon, depth, t):

```
S_obs = S_true(lat, lon, depth, t) + η_S(t) + w_S
T_obs = T_true(lat, lon, depth, t) + η_T(t) + w_T
```

with `η` = instrument drift / calibration offset (slow, small; usually
near-zero for a freshly-calibrated sensor over a mission timescale)
and `w` = per-observation Gaussian noise.

**Important distinction (deployment vs simulator):**

- **Instrument noise** σ_S_instrument ≈ 0.02 PSU, σ_T_instrument ≈ 0.01 °C
  (compact sensor specs).
- **Forecast bias** σ_S_forecast ≈ 0.5 g/kg, σ_T_forecast ≈ 0.4 °C
  (Soontiens & Allen 2017 SoG sub-region biases, see §1).

In **real deployment**, σ_S_total² = σ_S_instrument² + σ_S_forecast²
≈ σ_S_forecast² (forecast bias dominates by 25×). Using σ_S_instrument
alone in the PF likelihood causes degenerate filtering — every
particle's predicted (T, S) at SalishSeaCast's grid differs from the
sensor reading by the systematic bias, which is 15–35× σ_S_instrument,
giving log-likelihood ≈ −300 across the whole ensemble.

In **the current simulator** (Phase 2.1 prototype, commit `b0d1868`):
`S_true(lat, lon, depth, t) = TracerField.sample(...)` — just the
SalishSeaCast value at that position with no bias injected on top.
σ_S_instrument = 0.02 is mathematically correct here because the truth
IS the SalishSeaCast prediction. But this means the prototype's CTD
contribution to PFerr (51% drop in single-station smoke) is upper-
bounded by an idealised setup that doesn't represent deployment.

**Step 2 work** (queued, see `bias_inference_architecture.md` §8 and
the active plan): inject a layered tracer-bias field into the simulator
truth (parity with the velocity layered noise) to deliver deployment-
realistic CTD residuals; ALSO add a per-particle (T, S) bias state to
the bias-Kalman so the system can learn the systematic bias rather
than treating it as noise. The mechanism-specific channel that comes
out of this — salinity residual × ∂S/∂x → δ_plume update — is what
makes per-component bias decomposition (the v3 latent prior) identifiable.

For the simulator `S_true` comes from SalishSeaCast's `salinity` field
(see §5) plus the same coherent noise component as currents (Fraser
plume mis-placement is correlated across T, S, and currents — it's one
latent variable, the plume front).

### Likelihood for the PF

Per particle i at (lat_i, lon_i, depth_i, t):

```
log L_i = -0.5 · [(S_obs - S_model(lat_i, lon_i, depth_i, t))² / σ_S²
                + (T_obs - T_model(lat_i, lon_i, depth_i, t))² / σ_T²]
```

Standard Gaussian log-likelihood. Added to the existing LoRa-range
log-likelihood at surface events and applied at every tick submerged.

Expected effect: PFerr reduction during submerged legs. A particle
in the wrong water mass is exponentially down-weighted every tick.
LoRa's O(10 m) range σ is tighter in absolute localization terms once
the node surfaces, but CTD is the *only* tight PF observation while
submerged, so it should meaningfully reduce the PF cluster's lateral
growth between surface events.

### Likelihood for the bias-field Kalman

Salinity residual `r_S = S_obs - S_predicted_by_SalishSeaCast(lat, lon,
depth, t)` is an observation of the plume-offset latent variable
δ_plume:

```
r_S ≈ (∂S_true/∂δ_plume) · δ_plume + w_S
```

with the Jacobian `∂S_true/∂δ_plume` being the spatial salinity
gradient at the node's current position — large near plume fronts
(several PSU/km), small in clean basin water.

This makes salinity an *adaptive* observation of plume offset:
the observation is informative when the node is near a plume front
(|∂S/∂x| large) and uninformative in clean basin water. Bias learning
automatically down-weights salinity from stations where it carries
no information. This is a key win over a generic grid-basis bias
learner: structure in the observation model maps cleanly to structure
in the latent variable.

For generic (not plume-specific) bias learning — i.e. before we
refactor to the (δ_plume, η_wind) physically-structured prior — the
CTD observation is most useful as the PF reweight (use #1). Full
integration with physically-structured bias learning is deferred.

## 4. Implementation plan

**Data fetching** (new):
- Add T, S dataset IDs to `salishseacast_cache.py`:
  - `ubcSSg3DPhysicsFields1hV21-11` — carries `salinity`
    (reference salinity, g kg⁻¹), `temperature` (conservative
    temperature, °C), and `sigma_theta` (potential density, kg m⁻³)
    on the 40-depth × 898×398 grid.
- Variable names confirmed against live ERDDAP catalog
  (2026-04-24).
- Extend `fetch_bbox_months` to return a dataset with T, S in addition
  to u, v — or build a sibling fetcher `fetch_bbox_months_tracers`.

**Interpolator** (new):
- Extend `truth_field.TruthField` to carry `TempField` and `SaltField`
  interpolators with the same (t, lat, lon, depth) signature as
  velocity.

**Sensor** (new, in `rbpf_prototype/sensors.py`):
```python
@dataclass
class CTDSensor:
    sigma_T: float = 0.01   # °C
    sigma_S: float = 0.02   # PSU
    cadence_sec: float = 600.0  # once per tick

    def sample(self, T_truth, S_truth, rng):
        return (T_truth + rng.normal(0, self.sigma_T),
                S_truth + rng.normal(0, self.sigma_S))

    def log_likelihood_per_particle(self, T_model_per_particle,
                                     S_model_per_particle, z_T, z_S):
        return (-0.5 * ((T_model_per_particle - z_T) / self.sigma_T) ** 2
                -0.5 * ((S_model_per_particle - z_S) / self.sigma_S) ** 2)
```

**Experiment wiring** (extend `rbpf_prototype/experiment.py`):
- Every tick submerged, fire the CTD sensor and apply log-likelihood
  to each particle.
- Particles sample `T_model, S_model` at (particle_lat, particle_lon,
  state.depth, t).

**Bias-learning integration** (deferred):
- Current v1 grid bias learner updates at surface events only, from
  LoRa-observed position displacement. Adding CTD-residual-based
  updates requires either (a) extending the grid bias to include a
  salinity-offset state per cell — over-parametrised and probably
  unstable — or (b) refactoring to the (δ_plume, η_wind)
  physically-structured latent-variable parametrisation that agent C's
  analysis recommended. (b) is the right direction; deferred to Phase
  2.5.

## 5. Open questions / TODOs

1. **SalishSeaCast T/S ERDDAP dataset ID** — resolved:
   `ubcSSg3DPhysicsFields1hV21-11` (verified 2026-04-24).
2. **Plume-front spatial gradient of S** — we cite "several PSU/km"
   from Halverson's ferry record (Halverson & Pawlowicz 2008) but
   don't have a direct number for the mean gradient. Needs pulling
   from the paper's figures or a derived quantity from the
   SalishSeaCast S field itself.
3. **Sensor noise calibration** — σ_T = 0.01 °C and σ_S = 0.02 PSU
   are optimistic for a matchbox-scale sensor; real compact CTDs
   (e.g. RBR Coda³ T.D, Seabird SBE 39plus) may be 2–4× noisier in
   field conditions. Treat as a parameter, sweep.
4. **Temperature as independent plume signal** — T is secondary but
   could be useful for winter when S contrast is smaller. Deferred.

## References

- Halverson, M.J., & Pawlowicz, R. (2008). Estuarine forcing of a
  river plume by river flow and tides. *JGR-Oceans*, 113, C09033.
  DOI 10.1029/2008JC004844.
  File: `2008_halverson_fraser_plume_estuarine_forcing.pdf`.
- Oldford, G., Jarníková, T., Christensen, V., & Dunphy, M. (2025).
  HOTSSea v1: a NEMO-based physical hindcast of the Salish Sea
  (1980–2018). *Geosci. Model Dev.*, 18, 211–237.
  DOI 10.5194/gmd-18-211-2025.
  File: `2025_oldford_hotssea_salish_hindcast.pdf`.
- Soontiens, N., & Allen, S.E. (2017). Modelling sensitivities to
  mixing and advection in a sill-basin estuarine system. *Ocean
  Modelling*, 112, 17–32. DOI 10.1016/j.ocemod.2017.02.008.
  File: `references/soontiens2017.pdf`. T/S bias numbers calibrate
  the CTD sensor's expected model-vs-observation baseline (see §1).

**Cited from abstract-only** (paywalled, flagged so we don't overclaim):

- Halverson, M.J., & Pawlowicz, R. (2016). Tide, wind, and river
  forcing of the surface currents in the Fraser River plume.
  *Atmosphere-Ocean*, 54(2), 131–152.
- Pawlowicz, R., Di Costanzo, R., Halverson, M.J., Devred, E., &
  Johannessen, S.C. (2017). Advection, surface area, and sediment
  load of the Fraser River plume under variable wind and river
  forcing. *Atmosphere-Ocean*, 55(4–5), 293–313.
