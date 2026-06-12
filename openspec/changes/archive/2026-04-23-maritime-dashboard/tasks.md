## 1. CLI Contract — Tests

- [x] 1.1 CLI with valid `--scenario` + `--estimates` + `--port` + `--no-open` (no `--particles`) serves the dashboard on the specified port; `GET /` returns HTTP 200 with `text/html`
      (tests/maritime/test_dashboard.py)
- [x] 1.2 CLI prints a URL containing `localhost` and the chosen port to stdout
      (tests/maritime/test_dashboard.py)
- [x] 1.3 CLI exits non-zero on missing `--scenario` path; stderr names the missing file (explicit error)
      (tests/maritime/test_dashboard.py)
- [x] 1.4 CLI exits non-zero on missing `--estimates` path; stderr names the missing file
      (tests/maritime/test_dashboard.py)
- [x] 1.5 CLI with `--particles <existing-path>` loads the sidecar; response contains particle records for every node in the sidecar
      (tests/maritime/test_dashboard.py)
- [x] 1.6 CLI with `--particles <nonexistent-path>` warns on stderr (explicit — not silent) and still serves the dashboard without drill-down
      (tests/maritime/test_dashboard.py)

## 2. HTML Inlining — Tests

- [x] 2.1 Response body contains `"schema_version": "1.0"` from the scenario header
      (tests/maritime/test_dashboard.py)

- [x] 2.2 Response body contains every node_id from the scenario header (all 10 for standard fleet)
      (tests/maritime/test_dashboard.py)

- [x] 2.3 Inlined JSON has N tick entries with monotonically-spaced `t_sec` — for a scenario with `N` ticks, parse the `<script type="application/json" id="scenarioData">` blob and assert `len(parsed["truth_ticks"]) == N` and each entry `t` has `t_sec == t * header.dt_sec` exactly
      (tests/maritime/test_dashboard.py)

- [x] 2.4 Inlined truth trail matches scenario truth positions — for a scenario with distinct anchor positions and a time-varying drifter trajectory, parse the inlined JSON and assert each node's trail-at-tick-`t` equals the scenario truth state at `t` within float tolerance; assert two nodes at different truth positions produce different trail entries at the same tick (rules out zeroed-trail impl)
      (tests/maritime/test_dashboard.py)

- [x] 2.5 Inlined estimate trail matches PF estimate mean positions — for a PF estimate stream with finite, time-varying means, parse the inlined JSON and assert each node's estimate-trail-at-tick-`t` equals the `PFEstimateRecord.mean` position slice for the same `(node_id, t)` within float tolerance
      (tests/maritime/test_dashboard.py)

- [x] 2.6 Inlined JSON blob is valid and structurally complete — extract the `<script type="application/json" id="scenarioData">` tag, `json.loads` the content, assert top-level keys include `header`, `truth_ticks`, `pf_estimates`, and `coastline`, and `header.schema_version == "1.0"`
      (tests/maritime/test_dashboard.py)

## 3. Truth Reader Usage — Tests

- [x] 3.1 Dashboard module source contains `from rtl.vectors.maritime.scenario_truth_schema import ScenarioTruthReader` — importing from the dedicated truth module, not from `scenario_schema`
      (tests/maritime/test_dashboard.py)
- [x] 3.2 Dashboard module docstring documents the truth import as an explicit charter allowance
      (tests/maritime/test_dashboard.py)
- [x] 3.3 `uv run lint-imports` exits zero — dashboard is not in the PF-truth-separation contract's `source_modules` and is free to import truth
      (verification — manual or scripted)

## 4. No External Dependencies — Tests

- [x] 4.1 HTML response has no `<script>` with `src` starting with `http://` or `https://`
      (tests/maritime/test_dashboard.py)

- [x] 4.2 HTML response has no `<link>` with `href` starting with `http://` or `https://`
      (tests/maritime/test_dashboard.py)

## 5. Rendering Code Structure — Tests

- [x] 5.1 Dashboard JS includes coastline polygon rendering (search for canvas `fill` / `beginPath` / `closePath` in a coastline-specific function)
      (tests/maritime/test_dashboard.py)

- [x] 5.2 Dashboard JS branches on three node class names (anchor, ballast_drifter, pure_drifter) in the icon-rendering function
      (tests/maritime/test_dashboard.py)

- [x] 5.3 Dashboard JS branches on three LoRa status values (success, dropped, out_of_range) in the link-rendering function
      (tests/maritime/test_dashboard.py)

- [x] 5.4 Dashboard JS particle-rendering function is invoked for nodes present in the sidecar (discovered via `ParticleStreamReader.node_ids_present()`), not a hard-coded focus subset
      (tests/maritime/test_dashboard.py)
- [x] 5.5 Dashboard JS offers drill-down toggles only for nodes with particle data; nodes without particles have no drill-down UI
      (tests/maritime/test_dashboard.py)
- [x] 5.6 When sidecar is absent, drill-down UI is hidden/disabled; main rendering still works
      (tests/maritime/test_dashboard.py)

## 6. CLI and HTML Rendering — Implementation

- [x] 6.1 `experiments/12_maritime_dashboard.py` — argparse CLI with `--scenario`, `--estimates`, `--particles` (optional), `--port`, `--no-open`; `serve_dashboard()` reads scenario via `ScenarioTruthReader` (from `scenario_truth_schema`), PF main stream via `PFEstimateReader`, particle sidecar via `ParticleStreamReader` (when provided and file exists); loads bundled coastline; serves a single HTML page; prints URL to stdout
      (experiments/12_maritime_dashboard.py)
- [x] 6.2 `build_dashboard_html()` assembles the self-contained HTML from scenario header + truth ticks + estimate records + (optional) particle header + particle records + coastline GeoJSON; deterministic output for given inputs
      (experiments/12_maritime_dashboard.py)
- [x] 6.3 HTML skeleton with Canvas element, time slider `<input type="range">`, layer toggle checkboxes, per-node drill-down toggles (populated from sidecar's `node_ids_present()`), and inlined `<script type="application/json">` blob
      (experiments/12_maritime_dashboard.py — HTML template)

## 7. Canvas Rendering — Implementation

- [x] 7.1 Coastline rendering — polygon fill using the injected coastline GeoJSON, transformed by `latlonToCanvas(lat, lon, viewX, viewY, zoom)`
      (experiments/12_maritime_dashboard.py — JS section)

- [x] 7.2 Per-class node icon rendering — anchor (triangle + mast), ballast drifter (filled circle), pure drifter (hollow circle); distinct colors consistent with trails
      (experiments/12_maritime_dashboard.py — JS section)

- [x] 7.3 Truth and PF estimate trail rendering — solid line (truth) + distinct styling (estimate) from tick 0 to current; visibility toggles
      (experiments/12_maritime_dashboard.py — JS section)

- [x] 7.4 LoRa link line rendering — one line per tick `lora_links` entry with status-dependent color
      (experiments/12_maritime_dashboard.py — JS section)

- [x] 7.5 Per-node particle-cloud rendering — low-alpha dots scaled by weight; only invoked for nodes whose drill-down toggle is enabled AND whose node_id appears in the sidecar
      (experiments/12_maritime_dashboard.py — JS section)

## 8. UI Interaction — Implementation

- [x] 8.1 Time slider `input` event triggers a full scene redraw
      (experiments/12_maritime_dashboard.py — JS section)

- [x] 8.2 Mouse-drag pan updates `viewX` / `viewY`; mouse-wheel zoom updates `zoom` centered on the cursor
      (experiments/12_maritime_dashboard.py — JS section)

- [x] 8.3 Pan + zoom state persists across slider scrubbing (single shared state object; redraw applies view transform from that state)
      (experiments/12_maritime_dashboard.py — JS section)

- [x] 8.4 Keyboard shortcuts: space for play/pause, arrow keys for single-step (optional but recommended)
      (experiments/12_maritime_dashboard.py — JS section)

## 9. Smoke Test Harness — Implementation

- [x] 9.1 `test_dashboard.py` fixture — generates a tiny scenario + PF estimate file (reuses the golden trace or builds a fresh pair via CLIs), launches `12_maritime_dashboard.py` in a subprocess on a port-0-chosen port, fetches `GET /`, asserts tokens, terminates subprocess cleanly
      (tests/maritime/test_dashboard.py)

- [x] 9.2 Subprocess boot failure surfaces in test output — stderr captured and included in assertion message
      (tests/maritime/test_dashboard.py)

## 10. Manual Verification

- [x] 10.1 Run the dashboard against a freshly-generated 15-minute scenario + PF estimates + particle sidecar (default thinning). Open in browser.
- [x] 10.2 Confirm: coastline renders in correct position, 10 nodes visible with correct class icons, scrubbing time slider updates all layers together, pan/zoom works, LoRa link colors match status.
- [x] 10.3 Confirm drill-down: toggle drill-down for one pure drifter, one ballast drifter, one anchor; confirm particle clouds render for exactly those nodes.
- [x] 10.4 Confirm: the truth trails for pure drifters move with the current; anchors are stationary; ballast drifter trails are slower than pure drifter trails.
- [x] 10.5 Confirm: PF estimate trails track truth trails qualitatively (relationship, not a specific RMSE threshold).
- [x] 10.6 Confirm graceful handling: run the dashboard WITHOUT `--particles`; drill-down UI is hidden; main rendering is unaffected.

## 11. Verification

- [x] 11.1 `uv run pytest tests/maritime/test_dashboard.py` passes with zero failures
- [x] 11.2 Frozen baseline intact — `git diff` shows zero modifications to `experiments/01*.py` through `experiments/11*.py` and existing `rtl/vectors/*.py` files
- [x] 11.3 `openspec validate maritime-dashboard --strict` passes
- [x] 11.4 End-to-end smoke — the CLI launches, serves, and a `GET /` returns a parseable HTML page with expected data tokens

## 12. Multi-Run Selector Mode — Runs-Dir Extension

- [x] 12.1 CLI accepts --runs-dir <path> as a mutex alternative to --scenario/--estimates; exits non-zero when neither or both are provided
      (experiments/12_maritime_dashboard.py)
- [x] 12.2 discover_runs(runs_dir) enumerates subdirs containing both scenario.jsonl and estimates.jsonl; silently skips invalid subdirs; sorts newest-first by scenario.jsonl mtime
      (experiments/12_maritime_dashboard.py)
- [x] 12.3 Optional manifest.json per run (description field) is rendered in the page; missing manifest falls back to bare run name
      (experiments/12_maritime_dashboard.py)
- [x] 12.4 Eager per-run HTML build at server startup; GET /?run=<name> switches inlined data; unknown names fall back to default
      (experiments/12_maritime_dashboard.py)
- [x] 12.5 <select id="runSelector"> top-bar element carries one <option> per discovered run with correct selected attribute; JS onchange handler reloads with the selected ?run= query param
      (experiments/12_maritime_dashboard.py)
- [x] 12.6 Single-run mode (--scenario + --estimates) remains unchanged — all 24 original contract tests in tests/maritime/test_dashboard.py still pass
      (tests/maritime/test_dashboard.py)
