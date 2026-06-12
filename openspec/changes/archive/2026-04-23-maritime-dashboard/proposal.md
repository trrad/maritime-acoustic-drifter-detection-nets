## Why

The final MVP piece. The scenario generator, the PF, and all the numerical verification tests prove that the pipeline does what the spec says. The dashboard proves it's also what we think we mean — visually. A pure drifter wandering into a coastline on the map; a PF estimate trail that lags truth by the wrong direction; a current-field arrow pointing the wrong way; LoRa link lines flickering on and off in an obviously wrong pattern — these are bugs that RMSE tolerances don't catch. The dashboard is the cheap, high-leverage sanity check before we commit to M2 and the LNS8 port.

The plan doc pins the UX: pure HTML5 Canvas + vanilla JS, embedded CSS, pre-baked Natural Earth coastline from `/coast.json`, no Leaflet/D3/Plotly. Pan/zoom in ~60 lines of JS. This is deliberate scope discipline — the dashboard is a research tool, not a product, and every external dep adds a maintenance and onboarding tax. When a better-fit library shows up, we can swap.

## What Changes

- Introduce `experiments/12_maritime_dashboard.py` — Python CLI that:
  - Accepts `--scenario <path>`, `--estimates <path>`,
    `--particles <path>` (optional), `--port <int>` (default 8911),
    and `--no-open` (optional).
  - Opens the scenario via `ScenarioTruthReader` (imported from the
    dedicated `rtl.vectors.maritime.scenario_truth_schema` module —
    dashboard is an allowed truth consumer per the charter), PF main
    estimates via `PFEstimateReader`, and (if provided and the file
    exists) the particle sidecar via `ParticleStreamReader`.
  - Loads the bundled coastline GeoJSON (reused from `maritime-geo`).
  - Starts a local HTTP server on `localhost:<port>` that serves a
    single HTML page with all data inlined as JSON.
  - Shuts down on Ctrl-C.
  - On missing optional particles file, warns explicitly on stderr
    and proceeds without drill-down — errors are surfaced, not
    silently handled.
- Single self-contained HTML file (embedded in the Python script or
  emitted as a static asset) with:
  - Canvas-based map rendering: coastline polygons (pan/zoom), node
    icons per class, truth trails, PF estimate trails.
  - Per-node particle-cloud drill-down for any node present in the
    sidecar (no privileged "focus node" concept — drill-down follows
    whatever nodes the sidecar contains, which is driven by the
    `--thin-nodes` knob on `run_pf_float.py`).
  - LoRa link lines (from scenario `lora_links` — different styling
    for success / dropped / out-of-range statuses).
  - Time slider at the bottom that scrubs all layers together.
  - Legend + UI controls (per-node particle-cloud toggles when
    sidecar is loaded; trails / LoRa links visibility toggles).
- A small `tests/maritime/test_dashboard.py` that launches the server on a free port, fetches the HTML, and asserts the page contains the expected data markers (schema version, node count, tick count). No browser automation, no pixel-level visual testing in M1.

## Capabilities

### New Capabilities

- `maritime-dashboard`: Local HTML5/Canvas dashboard for visualizing maritime scenarios + PF estimates. Pure HTML + vanilla JS + Python HTTP server, no external JS dependencies. Renders coastline, per-class node icons, truth trails, PF estimate trails, focus-node particle clouds, LoRa link attempts (with success/drop/out-of-range styling), and a time slider that scrubs all layers together.

### Modified Capabilities

(none)

## Impact

- **New files**: `experiments/12_maritime_dashboard.py`,
  `tests/maritime/test_dashboard.py`. HTML + JS lives inside the
  Python script as a heredoc string (consistent with `11_pf_dashboard.py`
  for the 6D POC); swap to a separate file if the heredoc grows
  unwieldy.
- **Dependencies on earlier changes**: `maritime-scenario-gen`
  (consumes `ScenarioTruthReader` from `scenario_truth_schema`);
  `maritime-pf-float` (consumes `PFEstimateReader` and optional
  `ParticleStreamReader` from `pf_estimates_schema`); `maritime-geo`
  (coastline GeoJSON + point-in-polygon utilities);
  `maritime-map-payload` (bathymetry grid for map shading — optional
  for M1).
- **Downstream consumers**: humans. No code consumes the dashboard output.
- **Frozen baseline**: untouched. `experiments/11_pf_dashboard.py` (the 6D POC dashboard) stays frozen. This is a new file (`12_...`).
- **Simulation integrity charter**: the visual-validation layer the charter gestures at. `ScenarioTruthReader` access is justified here (dashboard is explicit validation tooling, named as such in the charter's forward contracts for reader types).
