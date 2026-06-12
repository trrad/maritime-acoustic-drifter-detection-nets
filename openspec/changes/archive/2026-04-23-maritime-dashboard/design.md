## Context

The 6D POC's dashboard is `experiments/11_pf_dashboard.py` — a Python HTTP server that serves a single HTML page with scenario + reference PF trace inlined as JSON, rendered with Canvas + vanilla JS. That pattern worked. It's what the maritime dashboard extends to: more layers (coastline, map shading, LoRa links, per-class node icons, particle clouds for focus nodes), more nodes (10 instead of 1), and a time slider that scrubs everything.

The external-deps question is live. Canvas + vanilla JS for M1 keeps the install surface zero and the file self-contained; when we move beyond ~10 nodes to ~100, or add per-dim time-series panels, we may want deck.gl or MapLibre. Not yet.

## Goals / Non-Goals

**Goals:**
- Python CLI (`experiments/12_maritime_dashboard.py`) that takes `--scenario` + `--pf` + optional `--port`, serves `localhost:<port>`, and prints the URL to stdout.
- Single HTML page with inlined JSON data (no async fetch; everything is there when the page loads). Page size grows with scenario length; at 15 min × 10 nodes × 1 Hz, we're in the low-MB range — fine for a dev tool on localhost.
- Canvas-based map view with pan (drag) and zoom (wheel). Coastline rendered as filled polygons. Node icons differ per class (anchor, ballast drifter, pure drifter).
- Truth trails (solid) + PF estimate trails (dashed or lighter) per node. Click a node → its trail highlights, others dim.
- Focus-node particle clouds: rendered as low-alpha dots at each tick the slider sits on. Weights influence dot alpha.
- LoRa links: line per attempted pair at the current tick. Green = success, yellow = dropped, red = out-of-range.
- Time slider scrubs all layers together. Playback at 1×, 4×, 16× (simple JS `setInterval`). Keyboard shortcuts: space to play/pause, arrow keys to single-step.
- A `test_dashboard.py` that launches the server in a background process, fetches `/`, asserts expected tokens (schema version, node IDs, tick count) are in the HTML. Confirms the page boots and includes data; does not check rendering.

**Non-Goals:**
- Browser automation / visual regression tests. Too much infra for research-tool scope.
- External JS libraries (Leaflet, D3, Plotly, Observable Plot, deck.gl). When we exceed what Canvas + vanilla JS can do cleanly, revisit.
- Async API endpoints. Everything inlined on page load.
- Mobile / responsive layout. Dev tool, 1080p+ only.
- Multi-scenario comparison. One scenario + one PF per dashboard invocation.
- Per-dimension time-series panels (e.g., heading over time). Single map view is enough for M1's visual validation mission.
- Bathymetry contour overlay beyond a simple color ramp. Fancy isobaths are M3.

## Decisions

### D1: HTML/JS is embedded in the Python script, not a separate file

**Choice:** The HTML + CSS + JS lives as a triple-quoted Python string inside `12_maritime_dashboard.py`. The script injects JSON data at runtime (string replacement or templating) and serves the resulting blob.

**Why:** One-file deliverable is easier to understand, run, and modify. The 6 D POC uses the same pattern in `11_pf_dashboard.py`. When the HTML grows past ~500 lines, we split — not before.

**Trade-off:** Syntax highlighting of HTML-inside-Python is poor in most editors. Acceptable.

### D2: Data inlined into HTML at startup; no runtime fetch

**Choice:** The Python script reads the full scenario + PF estimate files into memory, serializes them to JSON, and inlines that JSON into the HTML via `<script type="application/json" id="scenarioData">`. Browser reads `document.getElementById('scenarioData').textContent` on load. No WebSocket, no XHR.

**Why:** Zero-latency scrubbing (all data is already in memory), no server complexity beyond "serve one HTML page", trivially debuggable with browser DevTools ("view source" shows everything). Load time proportional to scenario size; at 10 nodes × 900 ticks × ~1 KB/tick = ~10 MB parsed JSON — fine.

**Trade-off:** Not streaming. A 2-hour scenario would be 80 MB. Acceptable for M1 (15 min) and M2 (up to 1 hour). Beyond that, add paging.

### D3: Canvas rendering with a small scene-graph, not direct drawing per event

**Choice:** On each time-slider change, the JS clears the canvas and re-renders from scratch:
1. Draw coastline polygons (clipped to the current view)
2. Draw bathymetry as a pre-computed color ramp (optional; M1 can skip)
3. Draw truth trails up to the current tick
4. Draw PF estimate trails up to the current tick
5. Draw focus-node particle clouds at the current tick
6. Draw active LoRa link lines at the current tick
7. Draw node icons at the current tick position

**Why:** Rendering the full scene per slider change is O(ticks_drawn × nodes), which at 900 × 10 = 9000 primitives is well within Canvas's one-frame budget. Avoids incremental-diff complexity. Pan/zoom is a view transform applied once before drawing.

### D4: Pan/zoom via plain mouse events + a view transform

**Choice:** Track `viewX`, `viewY`, `zoom` as page-level state. `mousedown + mousemove` updates `viewX/Y`; `wheel` updates `zoom` centered on the cursor. All drawing multiplies lat/lon through `latlonToCanvas(lat, lon, viewX, viewY, zoom)`.

**Why:** Classic 60-lines-of-JS pan/zoom. No Leaflet needed. Handles a ~50 km bbox (the M1 test scale) cleanly.

### D5: Node icons are simple shapes, not external sprites

**Choice:**
- Anchor: filled triangle + Iridium mast line
- Ballast drifter: filled circle
- Pure drifter: hollow circle

All with a 1-px border. Color-by-class kept consistent across trails and icons.

**Why:** No asset pipeline, no icon pack. Readable at M1 scale. Can upgrade to SVG icons later if needed.

### D6: Dashboard imports from scenario_truth_schema

**Choice:** The dashboard is a validation tool by charter definition;
it legitimately needs truth to show the user "this is what the PF
thinks vs. what actually happened." It imports `ScenarioTruthReader`
from `rtl.vectors.maritime.scenario_truth_schema` (the dedicated
truth-access module introduced by `maritime-scenario-gen`).

**Why:** Explicit charter allowance. The physical module split
(`scenario_schema` for observation types, `scenario_truth_schema` for
truth types) makes the allowance visible at the import site and
enforceable by the import-linter contract registered by
`maritime-pf-float`. The contract forbids PF modules from importing
truth; it does NOT include the dashboard in its `source_modules`,
so the dashboard is free to import truth — and that allowance is
declarative, not by convention.

### D7: Dashboard does not compute RMSE or run validation logic

**Choice:** The dashboard renders; it doesn't judge. No RMSE badges, no pass/fail indicators. A separate `maritime-validate` change (M2) owns judgment.

**Why:** Separation of concerns. Putting RMSE logic in the dashboard would duplicate what belongs in a validation module; visualization should be orthogonal to judgment. The human operator reads the map and forms their own conclusion, which is the right signal for "did we inadvertently break something?"

### D8: Simple browser test only

**Choice:** `test_dashboard.py` forks the server as a subprocess on a port-0-chosen free port, fetches `/` via `urllib`, asserts the HTML contains the schema_version string, all 10 node_ids, and the tick count matches the scenario. Kills the subprocess.

**Why:** Catches "page doesn't boot" and "data didn't get injected" — the most common failure modes. Doesn't try to render or interact. If we need deeper UX tests later, we add Playwright; for M1, this is proportionate.

### D9: Particle sidecar is optional, drill-down is per-node

**Choice:** The CLI accepts an optional `--particles <path>` pointing
to the PF particle sidecar stream (`maritime-pf-estimate-schema`
sidecar format). If provided, the dashboard loads it via
`ParticleStreamReader`, discovers which nodes have particle records
via `reader.node_ids_present()`, and offers per-node drill-down
toggles for those nodes. Nodes without particle records have no
drill-down; no privileged-subset concept.

If `--particles` is not provided, or the file doesn't exist, the
dashboard warns on stderr (explicit, not silent) and proceeds
without drill-down. The main map renders normally.

**Why:** Earlier design hard-coded `focus_node_ids` into the schema —
3 privileged nodes whose particle clouds were always emitted. That
didn't scale. The sidecar + thinning model (owned by
`maritime-pf-estimate-schema`) replaces it with configurable emission
that the PF CLI drives. The dashboard is the consumer: it reads
what's there, offers drill-down for the nodes that happen to have
particles, and doesn't bake scale assumptions into its UI.

**Trade-off:** The CLI argument surface grows by one flag. Worth it —
the alternative is reading scenario-adjacent files via convention,
which is fragile.

## Risks / Trade-offs

- **[Risk] Full-scene redraw per slider tick is slow at high node/tick counts** → Acceptable for M1 scale (10 × 900). If we hit a perf wall, add a dirty-region optimization or swap to deck.gl.
- **[Risk] Inlined JSON balloons page size at long durations** → Acceptable for M1 / M2. Add paging or a streaming API if scenarios grow to hours.
- **[Trade-off] Node icons are non-standard (no nautical chart symbols)** → Acceptable. The dashboard is for us, not for an operator. Cosmetic upgrade if we ever demo externally.
- **[Trade-off] No per-dimension time-series panels in M1** → Acceptable. The map view surfaces position / heading / trail-shape issues; per-dimension issues (velocity noise, bias drift) can be inspected offline with pytest + matplotlib.

## Key Type Contracts

```python
# experiments/12_maritime_dashboard.py

def serve_dashboard(
    scenario_path: Path,
    estimates_path: Path,
    particles_path: Path | None = None,
    port: int = 8911,
    open_browser: bool = True,
) -> None:
    """Start the dashboard server on localhost:<port>. Blocks until Ctrl-C.

    Reads scenario via ScenarioTruthReader (from scenario_truth_schema),
    PF main stream via PFEstimateReader, and optional particle sidecar
    via ParticleStreamReader. Loads bundled coastline, inlines everything
    as JSON into a single HTML page, serves it, and (optionally) opens
    the default browser.

    If particles_path is None or the file does not exist, the dashboard
    still serves — without per-node drill-down data.
    """

def build_dashboard_html(
    scenario_header: ScenarioHeader,
    scenario_ticks: Sequence[TruthTickView],
    pf_header: PFEstimateHeader,
    pf_estimates: Sequence[PFEstimateRecord],
    particle_header: PFEstimateHeader_Particles | None,
    particle_records: Sequence[ParticleRecord],       # empty when sidecar absent
    coastline_geojson: str,
) -> str:
    """Assemble the self-contained HTML page. Deterministic output for given inputs."""

def main(argv: Sequence[str] | None = None) -> int:
    """argparse CLI entry point: --scenario, --estimates, [--particles], [--port], [--no-open]."""
```

Runtime invariants:
- `serve_dashboard` prints the URL to stdout before blocking.
- `build_dashboard_html` produces the same byte string for the same (scenario, pf, coast) inputs — useful for snapshot testing.
- The server responds to `GET /` with the HTML page (Content-Type: text/html; charset=utf-8).
- The server does not respond to other paths in M1 (404). If we later add a `/coast.json` route to avoid inlining the coastline, that's additive.

## Integrity-Charter Mapping

- **Validation tooling access to truth** — the charter explicitly allows the dashboard (as "validation tooling") to import `ScenarioTruthReader`. This change is the first consumer of that allowance.
- **Visual validation** — the charter notes that visual inspection catches sim bugs that numeric tolerances don't. This change delivers the mechanism. Concrete examples of bugs it would catch: a node whose truth trail passes through a coastline polygon (dynamics doesn't check land — dashboard reveals it); a PF estimate trail leading its truth trail by 180° (bias sign error in observation model); LoRa link lines that pulse on/off in a pattern inconsistent with the TDMA period (scheduling bug).
- **No new integrity-level coverage** — this change doesn't add charter levels or enforcement; it exposes the existing levels to human eyeballs.
