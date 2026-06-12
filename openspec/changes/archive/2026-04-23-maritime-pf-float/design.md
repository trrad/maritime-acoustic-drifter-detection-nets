## Context

M1 needs a float64 reference PF that consumes the scenario JSONL and
produces position / velocity / heading estimates per node. The 6 D POC
proved out the bootstrap (SIR) shape in Python; this is the maritime-scale
extension: up to 25 D per node, ten nodes (100s-1000s in later
deployments), six sensor types, an imperfect onboard map, and truth
separation enforced at the module-boundary level.

The key design question is what to include. This revision differs from the
earlier draft in three places:

1. **No spec-level RMSE thresholds.** Earlier draft asserted anchors
   < 100 m, ballast < 200 m, pure drifters < 400 m as binding spec
   requirements. The numbers were intuition-based, not grounded in
   operational need or prior measurement. Dropped per AGENTS.md "no
   unprincipled numeric thresholds in specs." The PF now emits
   `pf_summary.json` with per-class RMSE aggregates as a measurement
   report we inspect; binding thresholds get written once grounded.
2. **No `focus_node_ids` concept.** Earlier draft made the schema
   pick 3 privileged nodes whose particle clouds get emitted every
   tick; everyone else got mean + cov_diag only. That doesn't scale —
   at 1000 nodes, the privileged-subset design wastes the fleet's real
   geometry. Replaced: main estimate stream always emits mean +
   cov_diag for all nodes; particle clouds live in a configurable
   sidecar with tick / particle / node thinning knobs.
3. **Truth separation via AST tools.** Earlier draft had a test
   scanning the source text of `pf_float.py` for the substring
   `"ScenarioTruthReader"` — regex enforcement of exactly the sort
   AGENTS.md rules out. Replaced by an `import-linter` contract
   (delivered by `project-infra-import-linter`) plus type signatures
   that accept only observation types.

## Goals / Non-Goals

**Goals:**
- A bootstrap (SIR) particle filter that runs at 15 / 21 / 25 D per
  node class in float64, vectorized over particles.
- Per-node independence: one `PFFloat` instance per node. No shared
  state, no cross-node fusion.
- Truth separation at three layers: module split (no truth-bearing
  module is importable from PF code, enforced by import-linter),
  function signatures accept only observation types (enforced by
  pyright), and onboard-map reconstruction (PF never sees truth map).
- Scalable particle emission: main stream always emits
  mean + cov_diag for every node; sidecar emits particle clouds
  subject to tick / particle / node thinning. Default thinning gives
  a reasonable size at M1 scale and stays tunable at fleet scale.
- Typed `ParticleStreamReader` / `ParticleStreamWriter`. JSONL
  backing today; a binary swap later changes the impl, not the
  producers or consumers.
- LoRa TOA observations consumed only where the range partner is an
  anchor (known reference position). Drifter-to-drifter ranges are
  recorded but dropped in M1 — fleet coordination is M2+.
- PF dynamics uses climatological currents (reconstructed from
  header-derived onboard map config), not the truth current field.
  The mismatch is deliberate.
- `pf_summary.json` measurement output alongside the estimate stream.
  Not asserted against — reported for human inspection.

**Non-Goals:**
- Spec-level RMSE thresholds. Measure and report; assert later.
- LNS8 or any delta-encoded state representation. Pure float64.
- Fleet-level state fusion, cross-node covariance sharing,
  distributed PF. M2+.
- Drifter-to-drifter range fusion. M2+.
- Regularized PF, auxiliary PF, variants beyond vanilla bootstrap.
- GPU / parallel acceleration.
- Adaptive resampling (ESS threshold for conditional resample). M1
  resamples every tick (simplest baseline).

## Decisions

### D1: Two delta specs in one change

**Choice:** `specs/maritime-pf-estimate-schema/spec.md` (main estimate
stream + particle sidecar contract) and `specs/maritime-pf-float/spec.md`
(the implementation).

**Why:** Same pattern as `maritime-scenario-gen` → schema + generator.
The schema is load-bearing for everything downstream that consumes PF
output (dashboard, M2 validation, M2 LNS8 PF, later comparison tools);
the float implementation is one producer. Consolidating the main and
sidecar stream contracts in one schema delta keeps the "PF output"
contract coherent.

### D2: Per-node independent PFs

**Choice:** Ten (or more, at fleet scale) independent `PFFloat`
instances, one per node. Each consumes only its own node's
observations. No shared particle clouds, no cross-node posterior.

**Why:** The honest M1 model of what a real onboard PF can compute
with only LoRa beyond local sensors. Fleet-level coordination adds
real value but also real complexity. Deferring it to M2 keeps M1
focused on "does a bootstrap PF at 25 D converge at all?"

**Implication:** Drifter-to-drifter LoRa ranges are useless in M1 —
the partner's position is unknown. Those observations stay in the
JSONL (for the dashboard and for M2 reprocessing) but the PF drops
them.

### D3: Bootstrap (SIR) with systematic resampling every tick

**Choice:** Vanilla bootstrap filter. Predict via dynamics + process
noise; weight by observation likelihood; resample every tick via
systematic resampling; estimate via weighted mean.

**Why:** Matches the 6 D POC. Systematic resampling is the standard
low-variance choice. Adaptive resampling is a tuning knob; we skip
it in M1 to keep the spec clean.

### D4: PF dynamics uses climatology, not truth current field

**Choice:** Particle predict step uses `climatology_from_field` output
(reconstructed deterministically from header config) as the expected
current, plus a larger process-noise covariance to cover the
climatology-vs-truth gap.

**Why:** Honest operational model. A real node doesn't have
`CurrentField.velocity_at(lat, lon, t)` — it has a stored climatology
map. The PF must cope with the difference.

**Import-linter enforcement:** `rtl.vectors.maritime.pf_*` and
`rtl.vectors.maritime.run_pf_*` are forbidden from importing
`rtl.vectors.maritime.current_fields`. The PF can't accidentally
reach for the truth field even if a developer thought "just for
debugging."

**Process-noise values** (module constants, tunable, sourced later
from measurement):
- Position: 1 m/√s
- Velocity: 0.05 m/s/√s
- Heading: 1 deg/√s
- Current estimate: 0.01 m/s/√s
- IMU bias: match truth-side dynamics values

Larger than truth-side process noise — the PF is less certain about
its world than the truth propagator is.

### D5: LoRa TOA consumed only for anchor ranges

**Choice:** In the weight step, the PF processes a `lora_toa`
observation only if the partner is one of the anchor node_ids listed
in `header.anchor_positions`. Drifter-to-drifter ranges are silently
dropped — this is a documented M1 filter path per D4 (the per-node
independent model has no drifter-position prior to range against).

**Why:** To compute a range likelihood, the PF needs a position for
the partner. `ScenarioHeader.anchor_positions` is the authoritative
non-truth source for anchor (lat, lon) tuples — it is populated by
the scenario generator from each anchor's `MooredPoseSpec` and
models the real operational flow where anchor positions are
surveyed before drop and known to every consumer (not truth that
the PF must be walled off from). Drifters' positions are estimated
by other PF instances — not accessible in the per-node-independent
M1 model.

### D6: Vectorized numpy across particles

**Choice:** Particles stored as `(n_particles, state_dim)` float64
array. Predict, weight, resample, and estimate operate on the whole
array without Python-level loops.

**Why:** Standard numpy idiom. Keeps runtime sub-minute for M1
scale. When the LNS8 port comes in M2, the same vectorized structure
maps to LNS8 operations on the same shape.

### D7: Observation likelihood is Gaussian on reported value

**Choice:** For each observation, the likelihood is
`Normal(observation.value; h(particle_state), observation.noise_sigma)`
where `h` is the observation function for the sensor and
`noise_sigma` comes from the observation record (not from the profile,
not from a PF hyperparameter).

**Why:** Self-describing observations — the JSONL carries σ. If the
sensor σ changes, the PF's likelihood tracks it. The PF's observation
model matches the generator's noise model exactly, which is the
correct M1 baseline.

### D8: `n_particles = 500` default

**Choice:** Module constant `DEFAULT_N_PARTICLES = 500`, overridable
via CLI flag.

**Why:** Matches the 6 D POC's comfortable operating point. At
25 D × 500 particles × float64 = 100 KB per PF × N nodes × T ticks.

### D9: Main estimate stream — mean + cov_diag + n_effective for every node

**Choice:** Main stream `pf_estimates.jsonl` emits one record per
`(node_id, tick)`: `mean` (length state_dim), `cov_diag` (length
state_dim, non-negative), `n_effective` (positive float). No
particles, no weights. For all nodes, every tick.

**Why:** Every consumer of the main stream needs position + uncertainty
— dashboard for trails and ellipses, M2 validation for comparisons,
future LNS8 PF for parity checks. Mean + cov_diag is bounded in size
O(state_dim) per node per tick — predictable at fleet scale.

### D10: Particle sidecar — thinned, on-demand

**Choice:** A separate `pf_particles.jsonl` (or whatever path the user
passes to `--particles-out`) holds particle-level records
`(t, t_sec, node_id, particles, weights)` subject to three orthogonal
thinning knobs:

- `--thin-ticks N` (default 1): only write records where `tick % N == 0`.
- `--thin-particles K` (default 50): random-sample K particles from
  the n_particles array for each emitted record.
- `--thin-nodes IDS` (default all): restrict sidecar to a comma-separated
  subset of node IDs.
- `--no-particles` disables the sidecar entirely.

Thinning filters compose with AND — setting `--thin-ticks 10 --thin-nodes n01,n05`
means "only n01 and n05, only every 10 ticks, with 50 particles sampled
per emission."

**Size math:** default thinning at M1 scale (10 nodes × 900 ticks × 50
particles × 25 dims × 8 B) ≈ 9 MB. At 1000 nodes same cadence ≈ 900 MB;
user bumps `--thin-ticks 10` for 90 MB.

**Why:** Earlier design hard-coded a 3-of-10 focus-node subset in the
schema. That breaks at fleet scale. The sidecar + thinning scales because
the user configures density for their deployment. Dashboard opens the
sidecar file, reads which `node_id` values appear, and offers drill-down
for those; no privileged subset baked into the schema.

### D11: Typed ParticleStream interface

**Choice:** `pf_estimates_schema.py` exports two types:
`ParticleStreamWriter` (used by `run_pf_float.py`) and
`ParticleStreamReader` (used by dashboard). Both are typed interfaces
with a JSONL implementation for M1. The producer and consumer import
the interface types, not the JSONL implementation.

**Why:** When the sidecar grows beyond JSONL's performance sweet spot
(trigger: file > 500 MB uncompressed, or dashboard open latency > 3 s
at operating scale), swap the impl to gzipped JSONL first, binary
(parquet / npz / hdf5) later. Interface types mean
`pf_float.py` and the dashboard don't change — only the impl class.

### D12: Truth separation — scope boundary is the onboard library, not the reporting CLI

**Choice:**

1. **Module boundary**: `maritime-scenario-gen` splits `scenario_schema`
   into `scenario_schema` (obs-only) and `scenario_truth_schema`
   (truth types). The onboard-simulated PF library cannot import
   `scenario_truth_schema`.
2. **Import-linter contract** (delivered by
   `project-infra-import-linter`): `rtl.vectors.maritime.pf_float`
   is the sole `source_module`; it is forbidden from importing
   `rtl.vectors.maritime.scenario_truth_schema` OR
   `rtl.vectors.maritime.current_fields`. CI fails on violation.
   `run_pf_float.py` is **intentionally not** in the contract's
   `source_modules` — it is the final reporting layer that owns
   `pf_summary.json` (which contains per-class RMSE), and RMSE
   against truth requires `ScenarioTruthReader`. The operational
   boundary we want is "the node-level algorithm cannot reach
   truth"; a workstation-side orchestrator that runs the algorithm
   and reports its performance against truth afterwards is on the
   allowed side of that line.
3. **Type signatures**: `PFFloat.step(..., observations: Iterable[ObservationRecord], ...)`.
   No overload accepts a truth view. Pyright strict flags any
   attempt to pass truth data as a type error — so even though
   `run_pf_float.py` is allowed to *read* truth for the summary,
   truth cannot flow *into* `PFFloat` from there.

Earlier draft's "scan `pf_float.py` source for the substring
`ScenarioTruthReader`" test is **dropped** — regex is not AST, and
the import-linter contract catches the same class of bug at the
correct abstraction level.

**Why:** AGENTS.md. Enforcement over instruction, with tools that
understand the import graph at the AST level. The narrowed scope
also resolves a contradiction in an earlier draft: with
`run_pf_float.py` in the forbidden-source list, `pf_summary.json`'s
per-class RMSE claim was unrealizable (RMSE requires truth). Scoping
the contract to the library only keeps the RMSE report honest while
preserving the invariant that matters: the algorithm simulated as if
it were running on the node never sees truth.

### D13: pf_summary.json — measurement, not assertion

**Choice:** Alongside `pf_estimates.jsonl`, `run_pf_float.py` emits
`pf_summary.json` — per-class RMSE aggregates (median, mean, p95)
over the final 25% of the run, ESS trajectory stats, and a completion
flag. The summary is a measurement report for human inspection.
Tests assert only sanity invariants (file exists, values finite,
`ess > 0`, structure parses).

**Why:** We want the numbers visible so we can learn what the PF
actually does at 21D bootstrap with a climatology-vs-truth gap. We
don't want the numbers driving "tune until they pass" loops. The
spec commits to the measurement existing; binding thresholds come
later with grounding.

## Risks / Trade-offs

- **[Risk]** 25 D bootstrap PF with 500 particles may not converge in
  operationally useful time. **Mitigation:** `pf_summary.json` makes
  this visible; first diagnostic is ESS; if collapse is the issue,
  bump particle count; if climatology-vs-truth mismatch dominates,
  revisit process-noise covariance. The M1 spec commits to the
  pipeline running and measuring, not to a specific convergence
  number.
- **[Risk]** Sidecar + thinning knobs add CLI surface. **Mitigation:**
  defaults are sensible at M1 scale; `--no-particles` disables the
  feature cleanly for users who don't need it.
- **[Risk]** Import-linter contract config drift — a contract rule
  edited in error could silently disable enforcement. **Mitigation:**
  the `project-infra-import-linter` spec requires that the CI check
  run; any contract config change shows up in PR diff.
- **[Trade-off]** Drifter-to-drifter LoRa dropped in M1. Reduces
  observation budget for pure drifters. Accepted — fleet coordination
  is an explicit M2 scope expansion.
- **[Trade-off]** Main stream always emits full mean + cov_diag for
  every node, even ones whose estimates are stable. Small cost; keeps
  the consumer contract uniform.

## Key Type Contracts

```python
# pf_estimates_schema.py

PF_ESTIMATE_SCHEMA_VERSION: str = "1.0"
SUPPORTED_PF_ESTIMATE_VERSIONS: frozenset[str] = frozenset({"1.0"})

@dataclass(frozen=True, slots=True)
class PFEstimateHeader:
    schema_version: str
    scenario_path: str
    scenario_seed: int
    pf_impl: str                            # "float64_bootstrap"
    n_particles: int
    node_ids: tuple[str, ...]               # all nodes in the fleet
    created_at_utc: str

@dataclass(frozen=True, slots=True)
class PFEstimateRecord:
    t: int
    t_sec: float
    node_id: str
    mean: tuple[float, ...]                 # length = layout.state_dim
    cov_diag: tuple[float, ...]             # length = layout.state_dim
    n_effective: float                      # effective sample size

# NOTE: particles + weights are NOT on PFEstimateRecord — they live in
# the sidecar (ParticleRecord).

@dataclass(frozen=True, slots=True)
class PFEstimateHeader_Particles:
    """Header for the particle sidecar. Separate file, separate reader."""
    schema_version: str                     # shares PF_ESTIMATE_SCHEMA_VERSION
    parent_estimate_path: str               # main stream this sidecar accompanies
    scenario_seed: int
    n_particles_full: int                   # underlying PF particle count
    thin_ticks: int                         # thinning config used to write
    thin_particles: int                     # subsampled particle count per record
    thin_nodes: tuple[str, ...] | None      # None = all nodes
    created_at_utc: str

@dataclass(frozen=True, slots=True)
class ParticleRecord:
    t: int
    t_sec: float
    node_id: str
    particles: tuple[tuple[float, ...], ...]    # shape (thin_particles, state_dim)
    weights: tuple[float, ...]                  # length thin_particles, sums to 1

class PFEstimateReader:
    def __init__(self, path: str | Path): ...
    def header(self) -> PFEstimateHeader: ...
    def __iter__(self) -> Iterator[PFEstimateRecord]: ...

class ParticleStreamReader:
    def __init__(self, path: str | Path): ...
    def header(self) -> PFEstimateHeader_Particles: ...
    def __iter__(self) -> Iterator[ParticleRecord]: ...
    # Convenience for dashboard: list node_ids that appear in the file
    def node_ids_present(self) -> frozenset[str]: ...

class ParticleStreamWriter:
    """Abstract writer. JSONL impl for M1; binary swappable later."""
    def write_header(self, header: PFEstimateHeader_Particles) -> None: ...
    def write_record(self, record: ParticleRecord) -> None: ...
    def close(self) -> None: ...

def make_jsonl_particle_writer(path: str | Path) -> ParticleStreamWriter: ...

# pf_float.py

@dataclass(frozen=True, slots=True)
class PFFloatConfig:
    n_particles: int = 500
    process_noise_pos_m_per_sqrt_s: float = 1.0
    process_noise_vel_ms_per_sqrt_s: float = 0.05
    process_noise_heading_deg_per_sqrt_s: float = 1.0
    process_noise_current_ms_per_sqrt_s: float = 0.01

class PFFloat:
    """One PF per node. Independent. Observation types only — no truth."""
    def __init__(
        self,
        node_id: str,
        layout: StateLayout,
        initial_state_mean: numpy.ndarray,
        initial_state_cov_diag: numpy.ndarray,
        onboard_map: RegionalMap,
        anchor_positions: Mapping[str, tuple[float, float]],
        enu_origin_lat_deg: float,         # extends the outline: predict's
        enu_origin_lon_deg: float,         # enu_to_latlon needs an origin
        config: PFFloatConfig,
        rng: numpy.random.Generator,
    ) -> None: ...

    def predict(self, dt_sec: float) -> None: ...
    def weight(self, observations: Iterable[ObservationRecord]) -> None: ...
    def resample(self) -> None: ...
    def estimate(self, t: int, t_sec: float) -> PFEstimateRecord: ...  # t/t_sec required by the record
    def step(self, dt_sec: float, observations: Iterable[ObservationRecord], t: int, t_sec: float) -> PFEstimateRecord: ...

    @property
    def particles(self) -> numpy.ndarray: ...     # (n_particles, state_dim)
    @property
    def weights(self) -> numpy.ndarray: ...       # (n_particles,)
    @property
    def effective_sample_size(self) -> float: ...

# run_pf_float.py

@dataclass(frozen=True, slots=True)
class ThinningConfig:
    thin_ticks: int = 1
    thin_particles: int = 50
    thin_nodes: tuple[str, ...] | None = None  # None = all
    disabled: bool = False

def main() -> int:
    """CLI entry:
        --scenario <path>
        --out <path>                    # main estimate stream
        --particles-out <path>          # optional sidecar; required unless --no-particles
        --thin-ticks N                  # default 1
        --thin-particles K              # default 50
        --thin-nodes IDS                # default all
        --no-particles                  # disable sidecar
        --n-particles N                 # default 500
        --summary-out <path>            # default: alongside main stream as pf_summary.json
    """
```

Construction / runtime invariants:
- `PFFloat.__init__` rejects an `initial_state_mean` shape that doesn't
  match `(layout.state_dim,)` or a `cov_diag` with negative entries.
- `PFFloat.weight` raises `ValueError` on any observation whose
  `sensor` name is not one of the six M1 sensor types
  (`"gps"`, `"imu"`, `"baro"`, `"mag"`, `"bathy_probe"`, `"lora_toa"`).
  An unknown sensor name is a pipeline bug (schema says one thing,
  consumer says another); failing loudly makes that bug visible.
- `lora_toa` with a non-anchor partner is NOT a drop — it is a
  documented filter inside the `lora_toa` handler: the handler checks
  partner identity and, for non-anchor partners, returns no likelihood
  contribution. This is the handler's specified M1 behavior, not a
  swallowed error. (M2's fleet-coordination scope expansion lifts the
  filter.)
- `PFFloat` imports only from `scenario_schema` (observation types);
  no `scenario_truth_schema` import. Enforced by import-linter contract.
- `PFFloat` does not import `current_fields`. Enforced by import-linter
  contract.
- `PFEstimateReader` / `ParticleStreamReader` raise `ValueError` on
  unknown `schema_version`.
- `ParticleStreamWriter` impl is injected — `pf_float.py` depends on
  the protocol, not the JSONL class.

## Integrity-Charter Mapping

- **Truth separation** — module split (scenario_schema /
  scenario_truth_schema, delivered by maritime-scenario-gen) +
  import-linter contract (delivered by project-infra-import-linter) +
  type signatures. No source-text scan, no naming convention.
- **Level 3 (Compute Budget)** — not enforced in M1 (float64 has no
  cycle budget). M2 LNS8 PF will reference `NodeProfile.compute.cycles_per_step`.
- **Level 4 (Imperfect Map)** — PF consumes the onboard map via
  `ScenarioReader(path).onboard_map()` (sidecar-backed, defined by
  `maritime-scenario-gen`). The PF never calls `make_onboard_map` and
  never accesses the truth map; the import-linter contract in
  `project-infra-import-linter` enforces this.
- **Forward contract: fleet coordination** — M1 drifter-to-drifter
  ranges are dropped; the spec flags this as M1-only scope.
