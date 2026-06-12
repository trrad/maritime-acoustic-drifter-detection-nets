## Context

The maritime pipeline was designed with truth-separation as a structural
invariant: `import-linter` forbids `pf_float.py` from importing
`current_fields` or `scenario_truth_schema`. That contract operates at
the symbol level and works under the assumption that the PF's inputs are
constructed from non-truth-bearing sources.

`map_payload.climatology_from_field(field: CurrentField, ...) -> ClimatologyGrid`
violates that assumption through a perfectly-valid signature. It takes a
truth-side object, derives an onboard artifact, and pickles it into
`onboard_map.pkl`. `pf_float.py` reads that pickle via `ScenarioReader`
— no forbidden import touched — and the truth-advected climatology
becomes the PF's predict-step prior. For near-constant truth fields the
climatology mean ≈ `field.velocity_at(lat, lon, t_any)` everywhere, so
the PF's "prediction" is truth's advection, and the archived 49.9 m
LoRa-only RMSE was an artifact of that confound, not a measurement of
real inference.

The fix has to be structural. Import-linter alone did not catch this —
the violation was not an import boundary, it was a data-flow boundary
crossing inside a module (`map_payload`) that legitimately imports both
`CurrentField` and the onboard storage type. The charter's
enforcement-over-instruction principle demands mechanism: a climatology
construction function whose signature forbids taking a `CurrentField` at
all.

Real-world drifter deployments do not have this problem. The operator
loads a prior from a historical data product (separate institution,
different time window, independent data pipeline) and faces a
submesoscale truth environment that the prior cannot resolve. Pulling
that workflow forward forces the pipeline's architecture to match
operational reality and eliminates the synthetic-era shortcut.

**Which historical data product should the climatology come from?** An
earlier draft of this change started with monthly-mean-of-hindcast — the
mean-velocity field averaged over all Octobers in the SalishSeaCast
hindcast. That proxy is indefensibly crude in tidally-dominated coastal
waters: M2 alone drives ±0.3–0.5 m/s oscillations at 12.42 h period,
and a prior that reports "expected current is October's mean" gives
the PF a systematically-wrong expectation between LoRa fixes. A
subsequent change was drafted to layer tidal harmonics on top as a
separate product (DFO WebTide / TPXO). That split was a design mistake:
the hindcast we're already downloading IS hourly and contains the full
tidal signal. Running `utide.solve()` per grid cell on the hindcast
time series produces per-constituent amplitudes and phases AND a
residual non-tidal background in one pass — no separate tidal product
needed. SalishSeaCast does not publish pre-computed harmonic
constituents as a downloadable NetCDF, but the analysis is a
minutes-scale one-time fixture-prep cost. The resulting harmonic
climatology is the correct operator-usable prior from day one.

## Goals / Non-Goals

**Goals:**
- Delete `climatology_from_field(field, ...)`. No alternative signature
  taking a `CurrentField` is safe.
- `ClimatologySource` is a Protocol with a time-parameterized read
  (`velocity_at(lat, lon, t)`). The primary concrete implementation is
  `HarmonicClimatology`, carrying both a non-tidal background (monthly
  residual means + variance) and a per-grid-cell tidal-harmonic table.
- Ship a fixture-prep helper that runs `utide` on a hindcast NetCDF and
  produces a canonical harmonic-table NetCDF. Run it once per bundled
  fixture; commit the resulting NetCDF.
- Ship a real-data path end-to-end: `RealCurrentField` reads a NetCDF
  nowcast (truth); `HarmonicClimatology` reads the pre-analyzed harmonic
  NetCDF (onboard prior); scenario generator composes them independently.
- Bundle one full Salish (primary) and one full offshore-VI (secondary)
  fixture so the real-data path is the default for dashboard runs and
  synthetic is an explicit opt-in for CI.
- Make provenance / independence invariants structurally enforceable:
  import-linter contracts + AST + signature introspection + runtime
  divergence test + visible dashboard overlays.
- Thread `t_sec` through the PF's climatology read call so the harmonic
  evaluation happens cleanly and a future fleet-learned climatology
  drops in behind the same interface.
- Add data-provenance and temporal-honesty invariants to
  `docs/simulation_integrity.md` with enforcement mechanisms attached.

**Non-Goals:**
- Adding a submesoscale truth layer. That's Change 2 and composes on
  top of `RealCurrentField`.
- Pre-packaged harmonic products (DFO WebTide, TPXO9-atlas, FES2014).
  The canonical NetCDF schema is designed to accept them in a future
  additive change, but Change 1 ships with in-house utide-analyzed
  harmonics only.
- Fixing the land-polygon side-issue. Investigate during scenario-gen
  work; defer full fix if non-trivial.
- Retiring `SyntheticEddyField`. The synthetic path stays for CI
  reproducibility.
- Fetch-on-demand fixture construction. Fixtures are checked in.
- Re-establishing a real-data RMSE baseline. That's residual M1-hardening
  after Changes 1 + 2.
- Nodal-modulation and shallow-water overtide corrections. M2/S2/K1/O1
  cover > 90% of tidal energy for M1 scales; out of scope.

## Decisions

### D1 — Delete `climatology_from_field`, don't deprecate

**Decision:** The function is removed outright. No deprecation shim.

**Rationale:** The function's signature — accepting `CurrentField` and
returning `ClimatologyGrid` — is the bug. Any callable that still
accepts `CurrentField` and returns a PF-readable artifact preserves the
leak surface. Two call sites to migrate; no external consumers.

### D2 — `ClimatologySource` is a `@runtime_checkable` Protocol with two required methods

**Decision:** `ClimatologySource` is defined via `typing.Protocol` with
BOTH a scalar method `velocity_at(lat_deg, lon_deg, t_sec) -> 4-tuple`
and a vectorized method `velocity_at_vectorized(lats_deg, lons_deg,
t_sec) -> 4 arrays`. Both are required for Protocol conformance. The
primary concrete implementation is `HarmonicClimatology`. Future
implementations (alternative harmonic products, fleet-learned
`FleetFusedClimatology` in M2+) satisfy the Protocol by duck-typing.

**Rationale:** The PF's predict hot path calls the vectorized form
(500 particles per tick). If only `velocity_at` is on the Protocol,
the PF's call site to `velocity_at_vectorized` either fails type-check
under pyright strict or requires a `cast`/`isinstance` narrowing —
both defeat the Protocol abstraction. Keeping both methods on the
Protocol means any `ClimatologySource`-typed reference can be fed to
the PF without downcasting. `CurrentField` doesn't need this because
`velocity_at` there is called scalar-per-particle already.

**Test doubles and simple consumers** can implement
`velocity_at_vectorized` with a one-line loop over the scalar method.
The module SHALL export a `loop_vectorize_velocity_at(source, lats,
lons, t_sec)` helper that does exactly that, so test doubles can write
`def velocity_at_vectorized(self, lats, lons, t_sec): return
loop_vectorize_velocity_at(self, lats, lons, t_sec)`. Performance-
sensitive implementations (`HarmonicClimatology`) override with a
native vectorized implementation.

**Alternatives considered:**
- Single scalar-only Protocol, PF does per-particle Python loop —
  rejected: violates `maritime-pf-float`'s "Vectorized Over Particles"
  requirement; noticeably slow at 500 particles per tick.
- Single scalar-only Protocol, PF narrows via `isinstance(climatology,
  HarmonicClimatology)` — rejected: leaks concrete-type knowledge into
  the PF; every new `ClimatologySource` implementation requires a PF
  edit.
- Two separate Protocols (`ClimatologySource` + `VectorizedClimatologySource`),
  PF accepts the union — rejected: same downcasting pain; buys nothing
  over single-Protocol-with-two-methods.

### D3 — `HarmonicClimatology` carries background + harmonics, one class

**Decision:** The single concrete class composes two storage channels
internally:

- Non-tidal background — gridded monthly residual means
  (`mean_vx_ms`, `mean_vy_ms`) + variance (`var_vx_ms2`, `var_vy_ms2`)
  over `(month, lat, lon)`. Derived from the hindcast *after* detiding.
- Tidal harmonic table — per-constituent amplitude and phase over
  `(constituent, lat, lon)` for both `u` and `v` components.

`velocity_at(lat, lon, t_sec)` returns the residual background mean at
that `(month, lat, lon)` cell plus `Σ_i amp_i · cos(ω_i · t_sec - phase_i)`
for the time-varying tidal contribution. Variance channel is
pass-through from the residual background.

**Rationale:** A single class simplifies the Protocol's surface and
avoids the earlier two-class (`MonthlyMeanClimatology` +
`TidalHarmonicClimatology`) split which was an artifact of pre-built
global products (CMEMS climatology) not including tides. A degenerate
`HarmonicClimatology` with zero constituents reduces to pure
monthly-mean — supports CI / synthetic paths and regions without
usable hindcast records. The design doesn't carry a dedicated
"monthly-mean-only" class; that's a configuration of the harmonic
class.

**Alternatives considered:**
- Two classes (`MonthlyMeanClimatology` + `TidalHarmonicClimatology`)
  — rejected: the tidal component is inseparable from a proper
  climatology in operational drifter contexts; two classes doubles the
  construction + testing + provenance surface.
- Subclass-based (`TidalHarmonicClimatology(MonthlyMeanClimatology)`)
  — rejected: inheritance is the wrong abstraction; composition is
  internal to one class.

### D4 — Harmonic analysis at fixture-prep time via `utide`

**Decision:** `build_climatology_from_harmonic_analysis(hindcast_path,
bbox, constituents=["M2","S2","K1","O1"], analysis_window_start,
analysis_window_end)`:

1. Load the hindcast NetCDF via `xarray`; subset to `bbox` and
   `[analysis_window_start, analysis_window_end]`.
2. For each grid cell `(i, j)`, extract the (time, u, v) series; run
   `utide.solve(time, u, v, lat=cell_lat, constit=constituents,
   nodal=False, trend=False, method="ols")`.
3. Collect the returned coefficients (`Lsmaj`, `Lsmin`, `g`, `theta`,
   per constituent) and convert to `(amp_vx, phase_vx, amp_vy,
   phase_vy)` via the ellipse → component decomposition standard in
   oceanography.
4. Compute residual monthly means by reconstructing tides via
   `utide.reconstruct(time, coef)`, subtracting from the raw time
   series, and averaging per month.
5. Write the canonical harmonic NetCDF (schema in D5).

The helper is a standalone fixture-prep utility — not in the
scenario-gen hot path, not imported at PF runtime. `utide` is an
optional dev/fixture-prep dependency.

**Rationale:** `utide` is the de-facto Python standard for ocean tidal
analysis (direct port of MATLAB UTide; wesleybowman/UTide on GitHub;
used widely in the oceanographic modeling community). In-house
harmonic fitting would work but would miss edge cases (nodal
corrections, inference relationships between closely-spaced
constituents) that `utide` handles correctly. Adding a fixture-prep
dependency is a small price for correctness; the production runtime
path doesn't pay it.

**Alternatives considered:**
- In-house least-squares fit to the fixed constituent set — rejected:
  correct to first order but loses `utide`'s nodal / inference /
  Rayleigh-threshold features; fragile.
- Offloading harmonic analysis to a MATLAB script (`t_tide`) — rejected:
  adds a non-Python toolchain to fixture prep.

### D5 — Canonical harmonic-table NetCDF schema

**Decision:** Single canonical schema for the bundled harmonic NetCDF
(regardless of source product):

```
Dimensions:
  constituent  (n_constituents)
  month        (12)
  lat          (n_lats)
  lon          (n_lons)

Coordinates:
  constituent[constituent]  str            # ["M2", "S2", "K1", "O1"]
  month[month]              int            # 1..12
  lat[lat]                  float64        # degrees
  lon[lon]                  float64        # degrees

Variables — harmonic (per constituent, per cell):
  amp_vx[constituent, lat, lon]    float64  # m/s
  amp_vy[constituent, lat, lon]    float64  # m/s
  phase_vx[constituent, lat, lon]  float64  # radians, in [0, 2π)
  phase_vy[constituent, lat, lon]  float64  # radians

Variables — non-tidal background (per month, per cell):
  mean_vx_ms[month, lat, lon]    float64    # residual mean after detiding
  mean_vy_ms[month, lat, lon]    float64
  var_vx_ms2[month, lat, lon]    float64    # residual variance
  var_vy_ms2[month, lat, lon]    float64

Attrs:
  product_family            str     # e.g., "salishseacast_hindcast_utide",
                                    # "cmems_anfc_utide", "dfo_webtide"
  dataset_id                str     # source identifier
  analysis_window_start     str     # ISO-8601
  analysis_window_end       str     # ISO-8601
  analysis_tool             str     # e.g., "utide-0.3.0" or "webtide-0.7.1"
  constituent_frequencies_rad_s  str  # JSON dict {name: ω} for audit
```

The schema is source-agnostic: a future change ingesting DFO WebTide or
TPXO9 tables transforms them into this same schema at fixture-prep time.

**Rationale:** One consumer-side loader regardless of source; product
polymorphism at the ingest layer, not the consumer layer.

### D6 — Constituent frequencies hardcoded, phases stored

**Decision:** `rtl/vectors/maritime/climatology_source.py` contains:
```python
CONSTITUENT_FREQUENCIES_RAD_S: dict[str, float] = {
    "M2": 2 * pi / (12.4206 * 3600),
    "S2": 2 * pi / (12.0000 * 3600),
    "K1": 2 * pi / (23.9345 * 3600),
    "O1": 2 * pi / (25.8193 * 3600),
    # extensible
}
```
The NetCDF stores amplitudes and phases per (constituent, cell). At
`velocity_at` call time, `ω_i` is looked up by constituent name. The
NetCDF's `constituent_frequencies_rad_s` attr is a redundant audit
trail — the loader asserts it matches the code's lookup to catch
schema drift.

**Rationale:** Frequencies are astronomical constants, not data. The
redundant attr lets a future loader catch "fixture was built against a
different ω for M2" bugs.

### D7 — Scenario-gen CLI: `--current-source {synthetic, real}` + two explicit path flags

**Decision:** Two CLI paths:
- `synthetic` (default) → `SyntheticEddyField` +
  `build_synthetic_climatology` (seeded pseudo-climatology, no truth
  reference).
- `real` → `load_real_current_field(truth_path)` +
  `build_climatology_from_harmonic_netcdf(clim_path, bbox)`. Both path
  flags REQUIRED. Independence + temporal-honesty validated.

### D8 — Synthetic climatology is seeded-deterministic, not derived from truth

**Decision:** When `--current-source synthetic`, the generator builds a
`HarmonicClimatology` with:
- Zero harmonic constituents (empty list) — synthetic path has no
  tidal signal to decompose.
- Seeded-random background means + variance (smoothed low-amplitude
  Gaussian fields) from `(seed, bbox, resolution)`. Does NOT reference
  the synthetic truth field.

**Rationale:** Preserves provenance-independence in the CI path.
Preserves byte-identical scenario reproducibility. Lets the synthetic
path exercise the same `ClimatologySource` consumer code as the real
path.

### D9 — `RealCurrentField` uses `xarray` + `RegularGridInterpolator`

**Decision:** `load_real_current_field(path)` opens the NetCDF via
`xarray.open_dataset(path)`, eagerly materializes `(u, v, lat, lon,
time)`, constructs a `scipy.interpolate.RegularGridInterpolator` for
each of u and v, populates product-family metadata from NetCDF attrs.

### D10 — Independence validation: inode + NetCDF metadata

**Decision:** At scenario-gen time when both real-data paths are
provided: compare inodes; fail on match. Compare `(product_family,
dataset_id)` from NetCDF attrs; fail on match (same product via
different filenames). This ensures the two data products are genuinely
independent, not aliased.

### D11 — Sidecar for dashboard overlays

**Decision:** At scenario-gen time, sample the truth field at every
tick and the climatology at every tick onto a `dashboard_current_grid_npts
× npts` (default 12×12) grid across the bbox. Bundle as
`current_field_grid.npz`:

```
truth_grid_u[t, i, j]   (n_ticks, n_grid, n_grid)
truth_grid_v[t, i, j]
clim_grid_u[t, i, j]    (n_ticks, n_grid, n_grid)  # time-varying because harmonic
clim_grid_v[t, i, j]
grid_lats[n_grid]
grid_lons[n_grid]
```

Both grids are tick-indexed — the harmonic climatology evolves through
tidal phases with the time slider, same as truth.

**Rationale:** Client-side tick-indexed rendering is simpler than
having the dashboard re-evaluate harmonics in JS. The sidecar cost is
n_ticks × n_grid² × 4 floats — at 1440 ticks × 144 cells × 4 × 8 bytes
= ~6 MB per scenario. Acceptable.

### D12 — Import-linter contracts

**Decision:** Add to `[tool.importlinter]`:
- "Climatology source does not access current fields":
  `source_modules = ["rtl.vectors.maritime.climatology_source"]`,
  `forbidden_modules = ["rtl.vectors.maritime.current_fields",
  "rtl.vectors.maritime.current_fields_real"]`.
- "Real current field does not access climatology":
  `source_modules = ["rtl.vectors.maritime.current_fields_real"]`,
  `forbidden_modules = ["rtl.vectors.maritime.climatology_source"]`.

### D13 — AST + signature + divergence tests

**Decision:** `tests/maritime/test_climatology_provenance.py`:
1. AST walk of `climatology_source.py` → no `Import` / `ImportFrom`
   resolving to `current_fields*`.
2. Signature introspection of `build_climatology_from_harmonic_analysis`,
   `build_climatology_from_harmonic_netcdf`, `build_synthetic_climatology`
   → no `CurrentField`-typed parameter.
3. Bundled-fixture divergence: loaded `(RealCurrentField,
   HarmonicClimatology)` pair differs by > 0.05 m/s at some
   `(lat, lon, t_sec)` — rules out "climatology equals truth".

### D14 — Temporal honesty: analysis window metadata check

**Decision:** At scenario-gen time (real mode), read the harmonic
NetCDF's `analysis_window_end` attr. Assert it is strictly before the
scenario's `--created-at`. Fail on violation. Secondary offshore-VI
fixture may have a looser-bounded CMEMS window; either re-derive with
explicit year bounds at fixture prep, or accept a waiver flag with a
documented caveat in the fixture README.

### D15 — Charter update lands in this change

**Decision:** `docs/simulation_integrity.md` gains the data-provenance
and temporal-honesty invariants as part of this change's file set,
paired with enforcement-mechanism references.

**Rationale:** Principle + mechanism ship together per the
"enforcement over instruction" rule.

### D16 — `utide` as optional fixture-prep dependency

**Decision:** `utide` is added to an optional-dependency group in
`pyproject.toml` (e.g., `[project.optional-dependencies.fixture-prep]`).
The production runtime does not import it; scenario generation, PF run,
dashboard render, and tests exercising the canonical NetCDF load path
do not require `utide`. Only the `build_climatology_from_harmonic_analysis`
helper and its targeted tests (gated behind `pytest.importorskip("utide")`)
depend on it. CI installs the base group; fixture-prep developers
install the optional group.

**Rationale:** Minimizes the baseline install footprint. Keeps
production code paths `utide`-free so deployment environments (FPGA
harness, LNS8 runs, dashboard-only runs) don't carry a transitive
fixture-prep dependency.

## Key Type Contracts

### Requirement: HarmonicClimatology (new, in maritime-climatology-source)

```python
# rtl/vectors/maritime/climatology_source.py
from typing import Protocol, runtime_checkable

@runtime_checkable
class ClimatologySource(Protocol):
    def velocity_at(
        self, lat_deg: float, lon_deg: float, t_sec: float
    ) -> tuple[float, float, float, float]:
        """Returns (mean_vx_ms, mean_vy_ms, var_vx_ms2, var_vy_ms2)
        at a single (lat, lon, t) point. Reference semantics."""
        ...

    def velocity_at_vectorized(
        self,
        lats_deg: np.ndarray,        # shape (n,)
        lons_deg: np.ndarray,        # shape (n,)
        t_sec: float,                # scalar — same time for all query points
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Vectorized form of velocity_at. Returns four arrays of shape (n,)
        aligned with the input lats/lons. Called per-tick by the PF
        predict hot path."""
        ...

def loop_vectorize_velocity_at(
    source: ClimatologySource,
    lats_deg: np.ndarray,
    lons_deg: np.ndarray,
    t_sec: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Helper: reference implementation of velocity_at_vectorized built
    on a scalar velocity_at. Test doubles and simple non-performance-
    sensitive implementations use this. Not called by the hot path."""
    n = lats_deg.shape[0]
    out_mvx = np.empty(n); out_mvy = np.empty(n)
    out_vvx = np.empty(n); out_vvy = np.empty(n)
    for i in range(n):
        mvx, mvy, vvx, vvy = source.velocity_at(
            float(lats_deg[i]), float(lons_deg[i]), t_sec
        )
        out_mvx[i] = mvx; out_mvy[i] = mvy
        out_vvx[i] = vvx; out_vvy[i] = vvy
    return out_mvx, out_mvy, out_vvx, out_vvy

CONSTITUENT_FREQUENCIES_RAD_S: dict[str, float] = {
    "M2": 2 * np.pi / (12.4206 * 3600),
    "S2": 2 * np.pi / (12.0000 * 3600),
    "K1": 2 * np.pi / (23.9345 * 3600),
    "O1": 2 * np.pi / (25.8193 * 3600),
}

@dataclass
class HarmonicClimatology:
    # Non-tidal background (per month, per cell).
    lats: np.ndarray               # (n_lats,) degrees
    lons: np.ndarray               # (n_lons,) degrees
    months: np.ndarray             # (12,) int32, 1..12
    mean_vx_ms: np.ndarray         # (12, n_lats, n_lons)
    mean_vy_ms: np.ndarray
    var_vx_ms2: np.ndarray
    var_vy_ms2: np.ndarray

    # Harmonic constituents (per constituent, per cell).
    constituents: list[str]        # e.g., ["M2", "S2", "K1", "O1"]
    amp_vx_ms: np.ndarray          # (n_constituents, n_lats, n_lons)
    amp_vy_ms: np.ndarray
    phase_vx_rad: np.ndarray
    phase_vy_rad: np.ndarray

    # Provenance.
    source_path: str
    product_family: str
    dataset_id: str
    analysis_window: tuple[str, str]   # ISO-8601

    def __post_init__(self) -> None:
        # Shape consistency, monotone lats/lons, variance non-negativity,
        # amplitudes non-negative, phases wrapped to [0, 2π), constituents
        # resolvable from CONSTITUENT_FREQUENCIES_RAD_S.
        ...

    @property
    def omega_rad_s(self) -> np.ndarray:  # (n_constituents,)
        return np.array([CONSTITUENT_FREQUENCIES_RAD_S[c] for c in self.constituents])

    def velocity_at(
        self, lat_deg: float, lon_deg: float, t_sec: float
    ) -> tuple[float, float, float, float]:
        # Spatial nearest-neighbor + month dispatch for background.
        # Harmonic sum over constituents evaluated at t_sec.
        ...

    def velocity_at_vectorized(
        self, lats: np.ndarray, lons: np.ndarray, t_sec: float
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        # Per-particle lookup for PF predict-stage vectorization.
        # Per-tick scalar t_sec; month dispatched once; spatial lookup
        # vectorized over particles; harmonic sum vectorized over
        # (constituents × particles).
        ...

def build_climatology_from_harmonic_netcdf(
    path: str,
    bbox: tuple[float, float, float, float],
) -> HarmonicClimatology:
    # Loads canonical harmonic NetCDF; subsets to bbox; returns
    # HarmonicClimatology. Signature MUST NOT include any CurrentField-
    # typed parameter.
    ...

def build_synthetic_climatology(
    seed: int,
    bbox: tuple[float, float, float, float],
    resolution_deg: float = 0.05,
) -> HarmonicClimatology:
    # Constructs a HarmonicClimatology with ZERO constituents and seeded
    # pseudo-random background. Does NOT reference any CurrentField.
    ...

# Fixture-prep helper — optional dependency on utide.
def build_climatology_from_harmonic_analysis(
    hindcast_path: str,
    bbox: tuple[float, float, float, float],
    analysis_window_start: str,
    analysis_window_end: str,
    constituents: list[str] = ["M2", "S2", "K1", "O1"],
    output_path: str = ...,
) -> str:
    # Runs utide.solve() per grid cell on the hindcast; writes canonical
    # harmonic NetCDF to output_path; returns the written path. Not a
    # runtime helper — run once at fixture-prep time.
    # Imports utide lazily inside the function body so the module stays
    # importable without utide installed.
    ...
```

### Requirement: RealCurrentField (new, in maritime-real-current-data)

```python
# rtl/vectors/maritime/current_fields_real.py
@dataclass
class RealCurrentField:
    lats: np.ndarray
    lons: np.ndarray
    times_sec: np.ndarray
    u_ms: np.ndarray          # (n_times, n_lats, n_lons)
    v_ms: np.ndarray
    source_path: str
    product_family: str
    dataset_id: str

    def velocity_at(
        self, lat_deg: float, lon_deg: float, t_sec: float
    ) -> tuple[float, float]:
        ...

# Satisfies CurrentField Protocol.

def load_real_current_field(path: str) -> RealCurrentField:
    # Signature MUST NOT include any ClimatologySource-typed parameter.
    ...
```

### Requirement: RegionalMap.climatology typed to ClimatologySource

```python
# rtl/vectors/maritime/map_payload.py — MODIFIED
@dataclass
class RegionalMap:
    bathymetry: BathymetryGrid
    land_polygons: list[np.ndarray]
    shipping_lanes: list[np.ndarray]
    climatology: ClimatologySource   # was: ClimatologyGrid

    def current_climatology_at(
        self, lat_deg: float, lon_deg: float, t_sec: float
    ) -> tuple[float, float, float, float]:
        return self.climatology.velocity_at(lat_deg, lon_deg, t_sec)

    def __post_init__(self) -> None:
        # isinstance(self.climatology, ClimatologySource) raises on failure.
        ...

# REMOVED:
# def climatology_from_field(field: CurrentField, ...) -> ClimatologyGrid: ...
```

### Requirement: PF predict passes t_sec

```python
# rtl/vectors/maritime/pf_float.py — MODIFIED predict-stage
# Per-tick t_sec is scalar. Dispatch month once (inside HarmonicClimatology).
# Vectorize spatial lookup + harmonic sum over particles.
lats, lons = enu_to_latlon(particles[:, idx.east], particles[:, idx.north], ...)
cur_vx, cur_vy, var_vx, var_vy = self._onboard_map.climatology.velocity_at_vectorized(
    lats, lons, t_sec
)
# Downstream velocity sampling unchanged (Stage 3 per-tick velocity model).
```

### Construction invariants preserved

- `RegionalMap.climatology: ClimatologySource` via `@runtime_checkable`
  Protocol — construction with a non-conforming object fails at
  `isinstance` check in `__post_init__`.
- `HarmonicClimatology.__post_init__`: shape consistency, variance
  non-negativity, amplitudes non-negative, phases in `[0, 2π)`,
  constituents resolvable, months `1..12`.
- `RealCurrentField.__post_init__`: monotone sorted axes; shape
  consistency; no NaN/inf in u/v.

## Risks / Trade-offs

**[Risk] `utide` fixture-prep may be slow or fail on some grid cells
(e.g., cells in dry or data-sparse areas).** → Mitigation: the helper
catches per-cell failures, logs them, fills with zero amplitudes for
that cell, and writes a `cell_ok[lat, lon]` boolean mask to the NetCDF
attrs. Consumers can read the mask; bad cells return pure-background
velocity (no harmonic contribution). Fixture-prep total wall-clock is
order minutes for the Salish bbox × 17 years hourly data — acceptable.

**[Risk] `utide`'s ellipse-form output (`Lsmaj`, `Lsmin`, `theta`, `g`)
needs conversion to per-component amplitude/phase. Conversion is
standard oceanography but a bug here produces silently-wrong
harmonics.** → Mitigation: unit test takes a synthetic u/v signal
with known M2 amplitude + phase, runs `utide.solve()`, runs the
ellipse→component conversion, and asserts recovery within 1e-3 m/s.
If that round-trip fails, the conversion is wrong, not the harmonic
data.

**[Risk] Breaking API change to `RegionalMap.climatology` ripples
through tests.** → Mitigation: `_pf_float_helpers.make_uniform_climatology`
grows a `ClimatologySource`-conforming wrapper (constructed as a
degenerate `HarmonicClimatology` with zero constituents and uniform
background). Existing tests using the helper remain unchanged.

**[Risk] NetCDF format polymorphism for `RealCurrentField` is brittle —
new product families require loader updates.** → Mitigation: the
sniffer is a small dispatch table with an explicit "unknown product
family" error path. Changes 1 ships with CIOPS / CMEMS support; future
additions are one-function edits.

**[Risk] Primary Salish hindcast fetch may hit SalishSeaCast ERDDAP
timeouts.** → Mitigation: one-time operation; if it fails, fall back
to CMEMS hourly hindcast for the Salish bbox (lower resolution but
covers the region). Document in fixture README.

**[Risk] Dashboard climatology overlay being tick-varying is a visible
contract change from earlier drafts that described it as
time-invariant.** → Mitigation: the spec delta explicitly calls out
tick-varying; the visualization improvement (tidal phase visible in
the prior) is part of the value proposition.

**[Risk] Charter update + contracts + production code + utide dep +
fixture-prep helper all shipping together makes the change large.** →
Mitigation: tasks.md phases implementation so the Protocol + concrete
class + tests land first, then the call-site migration, then the
deletion of `climatology_from_field`, then fixture-prep + Salish
fetch. Each phase's tests pass independently.

**[Risk] PF predict-step gains a per-tick harmonic sum computation
(previously nearest-cell lookup). Modest hot-path cost.** → Mitigation:
scalar `t_sec` + vectorized over particles + small constituent count
(4) → ~2000 trig ops per predict call, negligible against existing
predict work. Benchmark during implementation; if unacceptable, cache
per-tick `Σ_i amp_i · cos(ω_i · t_sec - phase_i)` arrays once per
tick.

## Migration Plan

1. Introduce `climatology_source.py` (`ClimatologySource` Protocol,
   `HarmonicClimatology`, loader, synthetic helper) and
   `current_fields_real.py` alongside existing code.
2. Add `ClimatologySource`-conforming wrapper to
   `_pf_float_helpers.make_uniform_climatology` — existing tests pass.
3. Migrate `RegionalMap.climatology` type annotation; add runtime
   `isinstance` check.
4. Migrate `PFFloat.predict` to the new vectorized helper; thread
   `t_sec`.
5. Rewrite two `test_map_payload.py` tests against `HarmonicClimatology`
   + bundled fixture.
6. Delete `climatology_from_field`; delete its imports.
7. Wire scenario-gen CLI: `--current-source`, `--current-data-path`,
   `--climatology-data-path`, independence + temporal-honesty checks.
8. Fetch primary Salish hindcast; run `build_climatology_from_harmonic_analysis`
   to produce the bundled harmonic NetCDF. Commit the NetCDF to the
   fixture directory.
9. (Analogously for secondary offshore-VI fixture if time permits.)
10. Add import-linter contracts. Run `lint-imports`.
11. Add AST + signature + divergence tests. Run full pytest.
12. Update `docs/simulation_integrity.md` + `docs/status.md`.
13. Regenerate dashboard sidecar emission path for tick-varying
    climatology. Regenerate golden trace (synthetic-path byte-identity).
14. `/opsx:verify` → `/opsx:sync` → `/opsx:archive`.

## Open Questions

- **`utide.solve()` per-cell vs `utide`'s bulk-fit over a rectangular
  time × space slab**: check during implementation whether `utide` has
  a vectorized API; fall back to per-cell loop if not. Either works;
  per-cell is clearer but potentially slow.
- **Phase convention**: verify `utide` returns phases referenced to
  UTC / Greenwich zero; normalize at fixture-prep if not.
- **Constituent selection**: default M2/S2/K1/O1 covers > 90% of
  tidal energy at most operational latitudes. Consider adding N2
  (larger amplitude near spring tides) if Salish analysis shows
  significant unexplained residual at 12.66 h period.
- **Dashboard grid density**: default 12×12; tune during final
  verification if visual clarity suffers.
- **Cell-failure handling during utide fit**: fill with zero harmonic
  or mask and pure-background fallback? Default: zero harmonic + mask
  in NetCDF attrs.
