## ADDED Requirements

### Requirement: CLI Invocation
The system SHALL provide a Python CLI at
`experiments/12_maritime_dashboard.py` that accepts:

- `--scenario <path>` (required): input scenario JSONL.
- `--estimates <path>` (required): PF main estimate stream JSONL.
- `--particles <path>` (optional): particle sidecar JSONL. If the
  file exists, the dashboard enables per-node drill-down for nodes
  present in the sidecar. If omitted or the file does not exist,
  the dashboard renders without particle clouds.
- `--port <int>` (optional, default 8911).
- `--no-open` (optional): suppress auto-opening the browser.

The CLI SHALL read the scenario via `ScenarioTruthReader` (imported
from `rtl.vectors.maritime.scenario_truth_schema` — the dedicated
truth-access module), PF estimates via `PFEstimateReader`, and (when
provided) particle records via `ParticleStreamReader` (all typed
readers from `rtl.vectors.maritime.scenario_truth_schema` and
`rtl.vectors.maritime.pf_estimates_schema`). The CLI SHALL start a
local HTTP server on `localhost:<port>`, print the full URL to
stdout, and block until interrupted.

#### Scenario: CLI serves the dashboard with main estimate stream only
- **WHEN** `12_maritime_dashboard.py --scenario /tmp/s.jsonl --estimates /tmp/e.jsonl --port 8911 --no-open` is invoked against valid files (no particle sidecar)
- **THEN** the server responds to `GET http://localhost:8911/` with HTTP 200 and a `text/html` content type
- **AND** the response body contains HTML markup
- **AND** the HTML does not include particle-cloud data (since no sidecar was loaded)

#### Scenario: CLI loads particle sidecar when provided
- **WHEN** the CLI is invoked with `--particles /tmp/p.jsonl` pointing to a valid sidecar
- **THEN** the inlined HTML contains particle records for every `node_id` that appears in the sidecar
- **AND** nodes with particle records are drill-down enabled in the UI (per the "Particle Drill-Down" requirement)

#### Scenario: CLI prints URL to stdout
- **WHEN** the CLI starts serving
- **THEN** a URL containing `localhost` and the chosen port is written to stdout before the server blocks

#### Scenario: CLI exits explicitly on missing scenario
- **WHEN** the CLI is invoked with a `--scenario` path that does not exist
- **THEN** the process exits non-zero
- **AND** stderr names the missing file (explicit error, not silent fallback)

#### Scenario: CLI exits explicitly on missing estimates
- **WHEN** the CLI is invoked with an `--estimates` path that does not exist
- **THEN** the process exits non-zero
- **AND** stderr names the missing file

#### Scenario: Missing optional particles file is not an error
- **WHEN** the CLI is invoked with `--particles /tmp/nonexistent.jsonl`
- **THEN** the CLI warns on stderr (explicit — "particles file not found, proceeding without drill-down") and still serves the dashboard
- **AND** the dashboard renders main trails and node icons normally

### Requirement: Single HTML Page with Inlined Data
The server SHALL respond to `GET /` with a single self-contained HTML page whose body includes all scenario truth, PF estimates, and coastline data inlined as JSON inside a `<script type="application/json">` tag. No runtime `fetch` / `XMLHttpRequest` to the server SHALL be required to render the dashboard. The server MAY respond with HTTP 404 for any path other than `/` (or static helpers like `/favicon.ico`). The inlined JSON blob SHALL be valid JSON (parseable by `json.loads` / `JSON.parse` without error) — any serialization pathology (NaN, Infinity, unescaped `</script>` in data) is a substance bug in the Python serializer, not an allowed variation.

#### Scenario: HTML contains schema version
- **WHEN** `GET /` is fetched
- **THEN** the response body contains the string `"schema_version": "1.0"` (from the scenario header)

#### Scenario: HTML contains all node IDs
- **WHEN** the scenario header lists 10 node IDs
- **THEN** the response body contains each of the 10 node IDs as a substring

#### Scenario: Inlined JSON blob parses and has expected top-level structure
- **WHEN** `GET /` is fetched and the response body is searched for a single `<script type="application/json" id="scenarioData">…</script>` tag
- **THEN** the tag's text content is valid JSON (parses via `json.loads` without raising)
- **AND** the parsed object is a mapping whose top-level keys include at least `header`, `truth_ticks`, `pf_estimates`, and `coastline` (exact key names chosen and documented by `build_dashboard_html`)
- **AND** the `header` sub-object's `schema_version` equals `"1.0"` and its `node_ids` has the scenario's fleet count

#### Scenario: Inlined JSON contains one tick entry per scenario tick
- **WHEN** the dashboard is served against a scenario with `N` tick records (e.g., `N=60`)
- **AND** the inlined JSON blob is parsed
- **THEN** the `truth_ticks` array has length `N`
- **AND** for every tick index `t` in `0..N-1`, the parsed entry at position `t` has `t_sec == t * header.dt_sec` (matching the scenario header's tick spacing)

### Requirement: Dashboard Is an Allowed Truth Consumer
The dashboard CLI SHALL import `ScenarioTruthReader` from
`rtl.vectors.maritime.scenario_truth_schema` because it visualizes
truth-vs-estimate comparison. This is an allowed charter use
(dashboard is validation tooling). The dashboard module SHALL NOT
appear in the `source_modules` of the PF-truth-separation
import-linter contract — the contract forbids PF code from importing
truth, not all code.

#### Scenario: Dashboard imports ScenarioTruthReader from the truth module
- **WHEN** the CLI module source is inspected
- **THEN** it contains an import of `ScenarioTruthReader` from `rtl.vectors.maritime.scenario_truth_schema` (the dedicated truth module, not `scenario_schema`)
- **AND** the module docstring documents the import as an explicit charter allowance

#### Scenario: Dashboard passes lint-imports
- **WHEN** `uv run lint-imports` is executed after the dashboard is implemented
- **THEN** it exits zero
- **AND** no contract names the dashboard as a `source_module`

### Requirement: No External JS Dependencies
The HTML page SHALL NOT include any `<script src="https://...">` or `<link href="https://...">` tags referencing external URLs. All CSS and JS SHALL be inlined in the HTML. Canvas + vanilla JS only. (Standard HTML data URIs for small static assets are permitted.)

#### Scenario: No external script tags
- **WHEN** the HTML response is parsed
- **THEN** no `<script>` element has a `src` attribute starting with `http://` or `https://`
- **AND** no `<link>` element has an `href` attribute starting with `http://` or `https://`

### Requirement: Coastline Rendered as Canvas Polygons
The dashboard SHALL render the bundled coastline (loaded via `maritime-geo.load_coastline_geojson`) as filled polygons on a Canvas element. The rendering SHALL respect the user's pan and zoom state.

#### Scenario: Coastline is rendered
- **WHEN** the dashboard loads a scenario whose bbox intersects the bundled test coastline (BC / Strait of Georgia in M1 per `maritime-geo`)
- **THEN** the HTML page includes coastline polygon data
- **AND** the JS draws each polygon as a filled Canvas path (verified by searching JS for `fill` / `beginPath` / `closePath` in coastline-draw function)

### Requirement: Per-Class Node Icons
Each node SHALL be rendered with a class-specific icon at its current-tick position:
- Anchor: filled triangle with a vertical mast line above
- Ballast drifter: filled circle
- Pure drifter: hollow circle with a 1-px border

All three shapes SHALL use distinguishable colors (anchor: red, ballast drifter: blue, pure drifter: green, or any three distinct colors consistent across trails + icons).

Verification note: the automated scenario below checks the JS structure (that class-specific branches exist); visual fidelity of the rendered icons themselves (triangle shape, mast line, filled vs hollow, distinct colors) is enforced by the manual verification items in `tasks.md` section 10 (Manual Verification). This deferral is deliberate — browser-automation tests are out of scope for M1.

#### Scenario: Icon code exists for each class
- **WHEN** the dashboard JS is inspected
- **THEN** it contains branches for the three class names (anchor, ballast_drifter, pure_drifter) in the icon-rendering function

### Requirement: Truth and Estimate Trails
For each node, the dashboard SHALL render a truth trail (solid line) and a PF estimate trail (distinct styling — dashed or lighter color) from tick 0 up to the current slider position. The trails SHALL use the node's class color. Toggling a per-layer visibility checkbox SHALL show/hide the trail layers. The trail *data* inlined into the HTML SHALL reflect the real scenario truth positions and the real PF estimate mean positions — an implementation that stubs either trail with zeros or a constant fails the content scenarios below.

#### Scenario: Both trails exist per node
- **WHEN** the dashboard renders a scenario tick
- **THEN** for each node, both truth-trail and estimate-trail data are present in the inlined JSON

#### Scenario: Inlined truth trail reflects scenario truth positions
- **WHEN** the dashboard is served against a scenario whose `ScenarioTruthReader` yields per-tick truth state with known anchor positions and time-varying drifter positions
- **AND** `GET /` is fetched and the inlined JSON blob is parsed (per "Single HTML Page with Inlined Data")
- **THEN** for every `node_id` and every tick `t` in the scenario, the parsed truth-trail entry for that `(node_id, t)` equals the scenario truth state's position slice at the same `(node_id, t)` within float tolerance
- **AND** two nodes at demonstrably different truth positions do NOT share the same trail point at the same tick (rules out a trivial zeroed-trail implementation)

#### Scenario: Inlined estimate trail reflects PF mean positions
- **WHEN** the dashboard is served against a PF estimate stream whose `PFEstimateReader` yields per-tick `PFEstimateRecord`s with finite, time-varying `mean` position slices
- **AND** `GET /` is fetched and the inlined JSON blob is parsed
- **THEN** for every `node_id` and every tick `t` covered by the estimate stream, the parsed estimate-trail entry for that `(node_id, t)` equals the `PFEstimateRecord.mean`'s position slice at the same `(node_id, t)` within float tolerance

### Requirement: LoRa Link Rendering
For the current tick, the dashboard SHALL draw one line per entry in the tick's `lora_links` array, connecting the two partner nodes' truth positions. The line color SHALL distinguish status: one color for `"success"`, one for `"dropped"`, one for `"out_of_range"`. Out-of-range lines MAY be rendered with reduced opacity.

Verification note: the automated scenario below checks the JS structure (that status-specific branches exist); the actual distinctness of rendered line colors and the presence/absence of opacity adjustments are enforced by the manual verification items in `tasks.md` section 10.

#### Scenario: Link status styling
- **WHEN** the dashboard JS is inspected
- **THEN** it contains branches for the three `status` values in the link-rendering function

### Requirement: Particle Drill-Down from Sidecar
The dashboard SHALL offer per-node particle-cloud drill-down for
every `node_id` that appears in the particle sidecar stream
(discovered via `ParticleStreamReader.node_ids_present()`). There
SHALL be no privileged "focus node" concept in the dashboard — any
node that has particle records becomes drill-downable; any node that
does not is rendered without particle data. The dashboard SHALL NOT
assume a fixed subset of nodes has particles.

When the user enables drill-down for a particle-present node, the
dashboard SHALL render the node's particle cloud at the current
slider tick as low-alpha dots at each particle's position. Dot alpha
MAY be scaled by the particle's weight. Drill-down state (which
nodes are currently rendering particles) SHALL be a client-side UI
toggle — the data for all particle-present nodes is already inlined.

If no particle sidecar was loaded, the drill-down UI SHALL be
hidden / disabled gracefully; the main map rendering SHALL proceed
without it.

#### Scenario: Drill-down is offered for every node in the sidecar
- **WHEN** a sidecar is loaded containing particle records for 4 nodes out of a 10-node fleet
- **THEN** the UI offers drill-down toggles for those 4 nodes
- **AND** the remaining 6 nodes have no drill-down toggle (no particle data to render)

#### Scenario: Dashboard renders without sidecar
- **WHEN** the dashboard is started without `--particles` (or with a missing file)
- **THEN** no drill-down UI element is visible
- **AND** the main map (trails, icons, LoRa links) renders normally

#### Scenario: Particle sampling respects sidecar thinning
- **WHEN** the sidecar was written with `--thin-particles 50`
- **THEN** the drill-down renders 50 dots per tick per drill-down-enabled node (matches `thin_particles` from the sidecar header)

### Requirement: Time Slider Scrubs All Layers Together
The page SHALL include a time slider (HTML `<input type="range">` or equivalent) spanning the scenario's tick range. Moving the slider SHALL update all layers (trails up to the new tick, node icons at the new tick, particle clouds at the new tick, LoRa links for the new tick) in a single redraw.

Verification note: the automated scenario below checks the JS event-handler shape (a single rendering function is invoked); whether each layer actually re-draws in response is enforced by the manual verification items in `tasks.md` section 10.

#### Scenario: Single redraw on slider change
- **WHEN** the slider's `input` event fires
- **THEN** the JS event handler calls a single rendering function that redraws all layers

### Requirement: Pan and Zoom
The map view SHALL support mouse-drag pan and mouse-wheel zoom. Zoom SHALL be centered on the cursor position. Pan and zoom SHALL persist across time-slider scrubbing (view state does not reset).

Verification note: the automated scenario below is a manual-verification placeholder — pan/zoom correctness under user interaction is enforced by the manual verification items in `tasks.md` section 10. The spec does not prescribe how the scenario is exercised by the automated test suite (a future headless-browser gate could implement it; M1 defers to manual review).

#### Scenario: Pan + zoom state survives scrubbing
- **WHEN** the user pans the view and then scrubs the time slider
- **THEN** the rendered map retains the user's pan offset and zoom level

### Requirement: Dashboard Smoke Test
The repository SHALL include a `tests/maritime/test_dashboard.py` that:
1. Generates (or uses a committed) minimal scenario + PF estimate file
2. Launches the CLI in a subprocess on a port chosen via port 0
3. Fetches `GET /` via `urllib`
4. Asserts the response is HTTP 200 with content-type `text/html`
5. Asserts the body contains the scenario's schema_version, every node_id, and the tick count
6. Terminates the subprocess cleanly

#### Scenario: Test detects server boot failure
- **WHEN** the subprocess fails to start (e.g., import error)
- **THEN** the test fails with a message naming the subprocess exit code or captured stderr

#### Scenario: Test detects missing data injection
- **WHEN** the server serves a page missing any node_id from the scenario
- **THEN** the test fails citing the missing identifier

### Requirement: Multi-Run Selector Mode
The CLI SHALL additionally accept `--runs-dir <path>` as a mutually-
exclusive alternative to `--scenario` + `--estimates`. When
`--runs-dir` is provided:

- The server SHALL discover one run per direct subdir of `<path>` that
  contains both `scenario.jsonl` and `estimates.jsonl`. Subdirs missing
  either are silently skipped (they are not runs). `particles.jsonl`
  and `manifest.json` are optional per-run; missing `particles.jsonl`
  SHALL behave exactly as the single-run "no sidecar" path.
- Each discovered run SHALL have its inlined-HTML blob eagerly built
  at server startup (one HTML string per run held in memory) so that
  switching runs does not require reloading scenario data from disk.
- The served HTML SHALL include a `<select id="runSelector">` element
  carrying one `<option>` per discovered run (ordered newest-first by
  the run's `scenario.jsonl` mtime). The currently-displayed run's
  `<option>` carries the `selected` attribute.
- `GET /?run=<name>` SHALL serve the HTML blob for the named run. An
  unknown `<name>` SHALL fall back to the default (newest) run.
- `GET /` with no query string SHALL serve the default (newest) run.
- The single-run contract (`--scenario` + `--estimates`) is unchanged;
  implementations MAY internally promote single-run mode to a degenerate
  one-element multi-run set, but the HTTP surface behavior for single-
  run callers is identical to the prior spec.
- Exactly one of `(--scenario AND --estimates)` OR `--runs-dir` SHALL
  be provided. If neither or both are provided, the CLI SHALL exit
  non-zero with an explanatory message on stderr.

An optional `manifest.json` in a run subdir MAY carry a `description`
string; when present, it SHALL be rendered in the dashboard's top
bar next to the selector. Additional manifest fields are implementer-
reserved and not part of this contract.

#### Scenario: Runs-dir discovers valid subdirs
- **WHEN** the CLI is invoked with `--runs-dir <path>` where `<path>`
  contains subdirs `a/` (holds `scenario.jsonl` + `estimates.jsonl`)
  and `b/` (holds only `scenario.jsonl`)
- **THEN** the server serves runs `a` (default) in the dropdown
- **AND** `b` is silently skipped (not a valid run)

#### Scenario: Query-param selects named run
- **WHEN** the CLI is invoked with `--runs-dir <path>` that has multiple
  valid runs including one named `my_run`
- **AND** `GET http://localhost:<port>/?run=my_run` is fetched
- **THEN** the response body's inlined `#scenarioData` blob carries the
  `my_run` run's header / truth_ticks / pf_estimates / coastline
- **AND** the `<select id="runSelector">` has `<option value="my_run"
  selected>` marking the current selection

#### Scenario: Unknown run name falls back to default
- **WHEN** `GET /?run=does_not_exist` is requested against a runs-dir
  server with at least one valid run
- **THEN** the server responds with HTTP 200 serving the default run's
  HTML blob (does not 404 on unknown run names)

#### Scenario: Single-run and runs-dir flags are mutually exclusive
- **WHEN** the CLI is invoked with BOTH `--scenario` and `--runs-dir`
- **THEN** the process exits non-zero
- **AND** stderr names the conflict (e.g. "--runs-dir is mutually
  exclusive with --scenario / --estimates")

#### Scenario: Manifest description surfaces in the page
- **WHEN** a run subdir carries `manifest.json` with a `description`
  field
- **THEN** the response body contains that description text
  (implementations typically render it in a top bar; the test asserts
  the substring is present in the HTML source)
