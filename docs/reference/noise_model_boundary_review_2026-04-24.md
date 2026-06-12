# Domain-Practitioner Review: layered noise-field boundary handling

**Reviewed:** `experiments/harmonic_prototype/submesoscale.py::build_layered_noise_field` (Phase 2.1, M1 in-flight).
**Date:** 2026-04-24.

**Personas consulted:**
- DFO/IOS physical oceanographer, 18 years in SoG, co-author on the Halverson/Pawlowicz/Gower CODAR validation lineage. Ran a drifter program that missed plume dispersion by 40% because of lumped-surface-error mis-specification.
- Applied mathematician, 12 years in geophysical Monte Carlo / random-field synthesis, spectral + SPDE methods. Spent two weeks debugging a coastal-dispersion simulator with exactly this `mode="nearest"` artefact.

## Severity 1: Design has a physical misspecification deeper than the numerical bug

- **[Oceanographer] Lumping plume + wind-slab + submesoscale + inertial into one `surf·exp(-z/L_z)` is physically wrong.** The Fraser plume is a buoyant slab with a sharp halocline base at 3–10 m (Kastner 2018), not an exponential decay to 15 m. `exp(-z/15)` puts ~50% of plume-scale error at 10 m depth, where in reality the plume is gone and the drifter is in return flow. Standard fix: split into `ε_plume` with L_z ≈ 3–5 m (or a tanh / step with a 5 m base), and `ε_submeso_wind` with L_z ≈ mixed-layer depth.

- **[Oceanographer] April 2023 ≠ "summer" — and L_z = 15 m mis-labels either.** April is pre-freshet, plume influence in the central basin is minimal, and the mixed-layer depth is 15–25 m from winter. So either (a) commit to a July scenario with a shallow plume, or (b) relabel this as April with a deeper, non-plume-dominated `surf` layer.

- **[Oceanographer] Inertial/near-inertial oscillations are missing and they matter at our timescale.** At 49°N the inertial period is 16.5 h; wind bursts inject a surface-trapped rotating signal with ~10 cm/s amplitude persisting 1–2 days. A stationary isotropic Gaussian cannot represent this, and 72 h is exactly the PF's learning window — so the missing mode bias the slow-vs-fast decomposition the PF is trying to learn.

## Severity 2: Parameter values are defensible only in a narrow sense

- **[Oceanographer] σ_coh = 3 cm/s is too low for SoG barotropic+baroclinic-tide residual.** Central-basin residual (not total) tidal + baroclinic-tide error in SalishSeaCast is 4–6 cm/s in CODAR comparisons, and it is tidal-phase coherent — not a 36 h Gaussian. Keep 36 h for "slow bias" but bump σ_coh to 5 cm/s, or add a deterministic tidal-residual term.

- **[Oceanographer] σ_s = 5 km for both coh and surf is wrong and is exactly the bug that caused the 40% plume-dispersion miss three months ago.** Submesoscale fronts and plume edges have 1–3 km cross-front correlation (anisotropic, shorter than barotropic), not 5 km. Lumping onto 5 km isotropic Gaussian means front-position error averages out across the 25 km box instead of staying localised; the PF sees a smoother, more learnable "bias" than a real drifter ensemble would. Give `surf` its own (σ_s ≈ 2 km, possibly anisotropic); keep coh at 5–10 km.

## Severity 1: The numerical boundary fix we were leaning toward is still wrong

- **[Numerics] Option A (`mode="reflect"`) is NOT stationary — it glues a mirror plane to every face.** Var[reflect] ≈ 2·Var[interior] at the boundary (kernel integrates the same realisation twice); one kernel-radius in, there's non-trivial covariance with the ghost copy. Swaps variance inflation for variance inflation + induced anisotropy. Only `mode="wrap"` is stationary by construction, and only if you accept periodicity.

- **[Numerics] Option C (current implementation — boundary-zero + interior renormalise) creates a spatial variance bowl.** Multiplying by `target / rms(interior)` pins the global second moment but does nothing to the local variance profile. The inner edge of the "surviving interior" sits ~1σ from a region where local variance is depressed, so after global rescaling the edge is *above* target and the centre is *below*. A 3σ sliding-window `Var[noise]` map would show a bowl, not a plateau. This is worse than it looks from the global RMS diagnostic.

- **[Numerics] Option B (pad-with-independent-noise, filter, crop at ≥ 3σ) is the right answer.** Memory argument isn't serious: padded cube is ≈ 2× float32 ≈ 90 MB. The pad MUST be fresh white noise (not reflect, not zero). With independent-noise padding and a 3σ crop, the interior is indistinguishable from an infinite-domain stationary filtered field to kernel truncation (~1%). Standard move in production atmospheric-dispersion codes.

- **[Numerics] Shifting sim-start from t=0 to an interior time is fine layered on B; it is "theatre" layered on C.** B fixes the local variance profile; shifting is then a no-op for variance but still corrects the correlation structure near t=0.

## Severity 3: Suboptimal but acceptable

- **[Numerics] Option D (spectral synthesis) is equivalent to B in Fourier space for Gaussian kernels; only worth doing if we later want to parameterise the spectrum directly (Matérn, anisotropic, −5/3). Not worth a rewrite *just* to fix the boundary bug.**

## First objections raised

- **Oceanographer:** "lumping the Fraser plume into a 15 m exponential erases the halocline that makes the plume a plume."
- **Numerics:** "you're renormalising a field whose local variance isn't spatially uniform — the global RMS is the wrong statistic to pin."

## Synthesis recommendation

Two independent classes of problem surface:

1. **Numerical:** adopt **Option B** (pad-with-independent-noise, filter, crop at 3σ) for the boundary fix. Reject A (induces anisotropy), C (variance bowl), and status quo (40% interior deflation). B is the standard move, minor memory cost, clean at the edge of the operating region.

2. **Physical:** the deeper issue is that `coh + surf·exp(-z/L_z) + white` is under-specified for the actual physics. Three separable improvements:
   - Split `surf` into `ε_plume` (L_z ≈ 3–5 m, tanh/step base) and `ε_submeso+wind` (L_z ≈ ML depth, 20–30 m in April).
   - Give each component its own (σ_s, σ_t): plume is anisotropic and ~2 km scale, submeso is ~5 km, barotropic is 5–10 km.
   - Add an inertial/near-inertial term (16.5 h rotating signal, ~10 cm/s amplitude, surface-trapped).

The physical issues are more serious than the boundary bug for the PF's learning behaviour — the PF is learning `coh + surf`, and if `surf` conflates multiple physical mechanisms with wrong σ_s, the PF recovers a plausible-looking but physically-meaningless bias. The boundary bug only inflates magnitudes uniformly; the misspecification bends the learning target.

**Pragmatic choice:** if the Phase-2.1 plan's purpose is "can the PF learn *any* bias structure under realistic forecast-error magnitudes," the v1 layered model with B-fix is defensible as a first pass; document the physical limitations in `noise_model_design.md`, and flag a v2 refinement (split surf, add inertial) for after M2. If the purpose is "validate PF performance against SoG reality," the v1 structure is not adequate and the refinement has to happen before the canonical sweep.
