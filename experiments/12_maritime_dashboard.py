"""M1 maritime dashboard — Canvas + vanilla JS visual-validation tool.

Imports ``ScenarioTruthReader`` from
``rtl.vectors.maritime.scenario_truth_schema`` by explicit charter
allowance: the dashboard is validation tooling, and the simulation
integrity charter allows such tooling to access truth for visualizing
truth-vs-estimate comparison. The PF-truth-separation import-linter
contract forbids PF modules from importing truth; the dashboard module
is NOT in that contract's ``source_modules`` list, so the allowance is
declarative (enforced by the contract's scope), not by convention.

Modes:

- **Single run** (``--scenario`` + ``--estimates`` + optional
  ``--particles``): inlines one dataset, serves it on ``/``. This is
  the original M1 contract that the test suite locks in.
- **Multi run** (``--runs-dir <path>``): scans the directory for
  subdirs containing ``scenario.jsonl`` + ``estimates.jsonl`` (with
  optional ``particles.jsonl`` and ``manifest.json`` metadata), inlines
  each one's HTML at startup, serves all of them via a ``<select>``
  dropdown — the page reloads with ``?run=<name>`` when the user
  switches. Eager-builds all HTML at startup for fast switching at the
  cost of memory (one HTML blob per run held in RAM).
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

# Ensure the repo root (parent of experiments/) is on sys.path so the
# `rtl.vectors.maritime` namespace package resolves when this script is
# invoked directly as `python experiments/12_maritime_dashboard.py ...`
# (in that mode sys.path[0] is the experiments/ directory, not the repo
# root).
REPO_ROOT: Path = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402

from rtl.vectors.maritime.coastline import (  # noqa: E402
    clip_coastline_bbox,
    load_coastline_geojson,
)
from rtl.vectors.maritime.pf_estimates_schema import (  # noqa: E402
    ParticleStreamReader,
    PFEstimateReader,
)
from rtl.vectors.maritime.scenario_truth_schema import ScenarioTruthReader  # noqa: E402


COASTLINE_PATH: Path = (
    REPO_ROOT / "rtl" / "vectors" / "maritime" / "data" / "bc_coast_sample.geojson"
)


# ---------------------------------------------------------------------------
# Run discovery
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Run:
    """One scenario+PF run available to the dashboard.

    ``particles_path`` may be ``None`` (no sidecar configured) or a path
    that does not exist (will warn at load time and proceed without
    drill-down).
    """

    name: str
    description: str
    scenario_path: Path
    estimates_path: Path
    particles_path: Path | None


def discover_runs(runs_dir: Path) -> list[Run]:
    """Scan ``runs_dir`` for valid run subdirs and return them newest-first.

    A subdir is a valid run if it contains ``scenario.jsonl`` AND
    ``estimates.jsonl``. Optional files: ``particles.jsonl`` (sidecar)
    and ``manifest.json`` (metadata: ``description`` field used for the
    dropdown label). Sorted by ``scenario.jsonl`` mtime, newest first,
    so the most-recent run is the default.
    """
    if not runs_dir.is_dir():
        raise FileNotFoundError(f"runs dir not found: {runs_dir}")

    found: list[tuple[float, Run]] = []
    for sub in sorted(runs_dir.iterdir()):
        if not sub.is_dir():
            continue
        scn = sub / "scenario.jsonl"
        est = sub / "estimates.jsonl"
        if not (scn.exists() and est.exists()):
            continue
        part = sub / "particles.jsonl"
        manifest_path = sub / "manifest.json"
        description = ""
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text())
                description = str(manifest.get("description", ""))
            except (json.JSONDecodeError, OSError) as exc:
                print(
                    f"Warning: malformed manifest.json in {sub}: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
        run = Run(
            name=sub.name,
            description=description,
            scenario_path=scn,
            estimates_path=est,
            particles_path=part if part.exists() else None,
        )
        found.append((scn.stat().st_mtime, run))

    if not found:
        raise ValueError(
            f"runs dir {runs_dir} contains no valid runs "
            "(need a subdir with scenario.jsonl + estimates.jsonl)"
        )
    found.sort(key=lambda x: -x[0])
    return [r for _, r in found]


# ---------------------------------------------------------------------------
# JSON assembly
# ---------------------------------------------------------------------------


def _header_to_json(header: Any) -> dict[str, Any]:
    return {
        "schema_version": header.schema_version,
        "bbox": list(header.bbox),
        "fleet_composition": dict(header.fleet_composition),
        "node_ids": list(header.node_ids),
        "node_classes": dict(header.node_classes),
        "seed": header.seed,
        "duration_sec": header.duration_sec,
        "dt_sec": header.dt_sec,
        "created_at_utc": header.created_at_utc,
        "onboard_map_path": header.onboard_map_path,
        "anchor_positions": {
            node_id: [float(lat), float(lon)]
            for node_id, (lat, lon) in header.anchor_positions.items()
        },
    }


def _truth_ticks_to_json(truth_ticks: Sequence[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for view in truth_ticks:
        nodes_obj: dict[str, dict[str, float]] = {}
        for node_id, state in view.node_truth.items():
            nodes_obj[node_id] = {
                "east_m": float(state[0]),
                "north_m": float(state[1]),
                "depth_m": float(state[2]),
            }
        links_obj = [
            {
                "node_a": link.node_a,
                "node_b": link.node_b,
                "status": link.status,
                "range_m": (None if link.range_m is None else float(link.range_m)),
            }
            for link in view.lora_links
        ]
        out.append(
            {
                "t": int(view.t),
                "t_sec": float(view.t_sec),
                "nodes": nodes_obj,
                "lora_links": links_obj,
            }
        )
    return out


def _pf_estimates_to_json(records: Sequence[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rec in records:
        if len(rec.mean) < 3:
            raise ValueError(
                f"PFEstimateRecord mean has {len(rec.mean)} dims; expected at least 3 "
                f"for (east_m, north_m, depth_m)"
            )
        out.append(
            {
                "node_id": rec.node_id,
                "t": int(rec.t),
                "t_sec": float(rec.t_sec),
                "mean": [float(rec.mean[0]), float(rec.mean[1]), float(rec.mean[2])],
            }
        )
    return out


def _particles_to_json(
    records: Sequence[Any],
) -> dict[str, list[dict[str, Any]]]:
    by_node: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        particles_pos = [
            [float(p[0]), float(p[1]), float(p[2])] if len(p) >= 3 else list(map(float, p))
            for p in rec.particles
        ]
        by_node.setdefault(rec.node_id, []).append(
            {
                "t": int(rec.t),
                "t_sec": float(rec.t_sec),
                "particles": particles_pos,
                "weights": [float(w) for w in rec.weights],
            }
        )
    return by_node


def _coastline_to_json(
    polygons: Iterable[np.ndarray],
    bbox: tuple[float, float, float, float],
) -> list[list[list[float]]]:
    lat_s, lon_w, lat_n, lon_e = bbox
    clipped = clip_coastline_bbox(list(polygons), lat_s, lon_w, lat_n, lon_e)
    out: list[list[list[float]]] = []
    for poly in clipped:
        out.append([[float(pt[0]), float(pt[1])] for pt in poly])
    return out


# ---------------------------------------------------------------------------
# HTML assembly
# ---------------------------------------------------------------------------


_HTML_SHELL = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Maritime Dashboard</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  background: #1e1e2e;
  color: #cdd6f4;
  font-family: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace;
  overflow: hidden;
  height: 100vh;
  display: flex;
  flex-direction: column;
}
#top-bar {
  background: #181825;
  border-bottom: 1px solid #313244;
  padding: 4px 12px;
  display: flex;
  gap: 12px;
  align-items: center;
  font-size: 12px;
  color: #cdd6f4;
  flex: 0 0 auto;
  height: 28px;
  white-space: nowrap;
  overflow: hidden;
}
#top-bar select {
  background: #313244;
  color: #cdd6f4;
  border: 1px solid #45475a;
  padding: 1px 6px;
  font-family: inherit;
  font-size: 12px;
  max-width: 280px;
}
#run-desc {
  color: #a6adc8;
  font-size: 11px;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}
#main-row {
  flex: 1;
  display: flex;
  flex-direction: row;
  min-height: 0;
}
#map-wrap {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}
#canvas-wrap {
  flex: 1;
  position: relative;
  background: #11111b;
  overflow: hidden;
}
#mapCanvas {
  display: block;
  width: 100%;
  height: 100%;
  cursor: grab;
}
#mapCanvas.dragging { cursor: grabbing; }
#bottom-bar {
  padding: 8px 14px;
  background: #181825;
  border-top: 1px solid #313244;
  display: flex;
  gap: 12px;
  align-items: center;
  font-size: 12px;
}
#bottom-bar input[type=range] {
  flex: 1;
  accent-color: #cba6f7;
  height: 6px;
}
#bottom-bar button {
  background: #313244;
  color: #cdd6f4;
  border: 1px solid #45475a;
  border-radius: 4px;
  padding: 4px 10px;
  cursor: pointer;
  font-family: inherit;
  font-size: 12px;
}
#bottom-bar button:hover { background: #45475a; }
#bottom-bar button.active { background: #cba6f7; color: #1e1e2e; }
#tick-label { min-width: 90px; text-align: right; color: #a6adc8; }
#sidebar {
  width: 260px;
  background: #181825;
  border-left: 1px solid #313244;
  padding: 12px;
  overflow-y: auto;
  font-size: 12px;
}
#sidebar h2 {
  font-size: 12px;
  color: #cba6f7;
  margin-top: 10px;
  margin-bottom: 4px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}
#sidebar h2:first-child { margin-top: 0; }
#sidebar label {
  display: block;
  padding: 2px 0;
  color: #cdd6f4;
  cursor: pointer;
}
#sidebar label input[type=checkbox] { margin-right: 6px; accent-color: #cba6f7; }
.legend-row { display: flex; align-items: center; gap: 6px; padding: 2px 0; color: #a6adc8; }
.legend-swatch { width: 10px; height: 10px; border-radius: 2px; }
</style>
</head>
<body>
<div id="top-bar">
  <label>Run:
    <select id="runSelector">__RUN_OPTIONS__</select>
  </label>
  <span id="run-desc">__RUN_DESC__</span>
</div>
<div id="main-row">
<div id="map-wrap">
  <div id="canvas-wrap">
    <canvas id="mapCanvas"></canvas>
  </div>
  <div id="bottom-bar">
    <button id="btnPlay" title="Play/Pause (space)">&#9654;</button>
    <button id="btnStep" title="Step back (left)">&#9664;</button>
    <button id="btnStepFwd" title="Step forward (right)">&#9654;&#9654;</button>
    <input type="range" id="timeSlider" min="0" max="0" value="0">
    <span id="tick-label">0/0</span>
    <button id="btnReset" title="Reset view">reset view</button>
  </div>
</div>
<div id="sidebar">
  <h2>Layers</h2>
  <label><input type="checkbox" id="togTruth" checked> Truth trails</label>
  <label><input type="checkbox" id="togEstimate" checked> Estimate trails</label>
  <label><input type="checkbox" id="togLora" checked> LoRa links</label>
  <label><input type="checkbox" id="togCoast" checked> Coastline</label>
  <label><input type="checkbox" id="togIcons" checked> Node icons</label>

  <h2>Legend</h2>
  <div class="legend-row"><div class="legend-swatch" style="background:#f38ba8"></div>anchor</div>
  <div class="legend-row"><div class="legend-swatch" style="background:#89b4fa"></div>ballast_drifter</div>
  <div class="legend-row"><div class="legend-swatch" style="background:#a6e3a1;border:1px solid #a6e3a1"></div>pure_drifter</div>
  <div class="legend-row" style="margin-top:6px"><div class="legend-swatch" style="background:#cdd6f4"></div>solid line = truth</div>
  <div class="legend-row"><div class="legend-swatch" style="background:repeating-linear-gradient(90deg,#cdd6f4 0 4px,transparent 4px 7px)"></div>dashed line = PF estimate</div>
  <div class="legend-row" style="margin-top:6px"><div class="legend-swatch" style="background:#a6e3a1"></div>success</div>
  <div class="legend-row"><div class="legend-swatch" style="background:#f9e2af"></div>dropped</div>
  <div class="legend-row"><div class="legend-swatch" style="background:#f38ba8;opacity:0.4"></div>out_of_range</div>

  __DRILL_DOWN_SECTION__
</div>
</div>

<script type="application/json" id="scenarioData">__SCENARIO_DATA__</script>

<script>
"use strict";

const DATA = JSON.parse(document.getElementById("scenarioData").textContent);
const header = DATA.header;
const truthTicks = DATA.truth_ticks;
const pfEstimates = DATA.pf_estimates || [];
const coastline = DATA.coastline || [];
const particles = DATA.particles || null;

const NODE_IDS = header.node_ids;
const NODE_CLASSES = header.node_classes;
const BBOX = header.bbox;
const DT_SEC = header.dt_sec;
const N_TICKS = truthTicks.length;

const CLASS_COLORS = {
  anchor: "#f38ba8",
  ballast_drifter: "#89b4fa",
  pure_drifter: "#a6e3a1",
};
const LORA_COLORS = {
  success: "#a6e3a1",
  dropped: "#f9e2af",
  out_of_range: "#f38ba8",
};
const LAND_COLOR = "#3b3b52";

// Index PF estimates by [node_id][t].
const pfIndex = {};
for (const rec of pfEstimates) {
  if (!pfIndex[rec.node_id]) pfIndex[rec.node_id] = {};
  pfIndex[rec.node_id][rec.t] = rec.mean;
}

// Index particles by [node_id][t].
const particleIndex = {};
if (particles) {
  for (const nodeId of Object.keys(particles)) {
    particleIndex[nodeId] = {};
    for (const entry of particles[nodeId]) {
      particleIndex[nodeId][entry.t] = entry;
    }
  }
}

// bbox center (lat, lon). Used as local-tangent-plane origin for east/north.
const BBOX_LAT_S = BBOX[0];
const BBOX_LON_W = BBOX[1];
const BBOX_LAT_N = BBOX[2];
const BBOX_LON_E = BBOX[3];
const CENTER_LAT = 0.5 * (BBOX_LAT_S + BBOX_LAT_N);
const CENTER_LON = 0.5 * (BBOX_LON_W + BBOX_LON_E);
const DEG2M_LAT = 111320.0;
function deg2mLon(lat) { return 111320.0 * Math.cos(lat * Math.PI / 180.0); }

// Scale chosen so the bbox spans ~90% of the viewport at zoom=1.
function bboxSpanMeters() {
  const dNorth = (BBOX_LAT_N - BBOX_LAT_S) * DEG2M_LAT;
  const dEast = (BBOX_LON_E - BBOX_LON_W) * deg2mLon(CENTER_LAT);
  return { dEast: Math.max(dEast, 1.0), dNorth: Math.max(dNorth, 1.0) };
}

const canvas = document.getElementById("mapCanvas");
const ctx = canvas.getContext("2d");
const slider = document.getElementById("timeSlider");
const tickLabel = document.getElementById("tick-label");
const btnPlay = document.getElementById("btnPlay");
const btnStep = document.getElementById("btnStep");
const btnStepFwd = document.getElementById("btnStepFwd");
const btnReset = document.getElementById("btnReset");

const togTruth = document.getElementById("togTruth");
const togEstimate = document.getElementById("togEstimate");
const togLora = document.getElementById("togLora");
const togCoast = document.getElementById("togCoast");
const togIcons = document.getElementById("togIcons");

const viewState = { viewX: 0, viewY: 0, zoom: 1, curTick: 0 };
let playing = false;
let playTimer = null;
const drillDownEnabled = {};

slider.max = Math.max(0, N_TICKS - 1);

function resizeCanvas() {
  const wrap = canvas.parentElement;
  const dpr = window.devicePixelRatio || 1;
  const r = wrap.getBoundingClientRect();
  canvas.width = Math.max(1, Math.round(r.width * dpr));
  canvas.height = Math.max(1, Math.round(r.height * dpr));
  canvas.style.width = r.width + "px";
  canvas.style.height = r.height + "px";
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.W = r.width;
  ctx.H = r.height;
}

// Convert meters-east/meters-north offsets (from bbox center) to canvas px.
function enToCanvas(east_m, north_m, viewX, viewY, zoom) {
  const span = bboxSpanMeters();
  const fit = 0.9 * Math.min(ctx.W / span.dEast, ctx.H / span.dNorth);
  const px = ctx.W / 2 + viewX + east_m * fit * zoom;
  const py = ctx.H / 2 + viewY - north_m * fit * zoom;
  return [px, py];
}

function latlonToCanvas(lat, lon, viewX, viewY, zoom) {
  const east_m = (lon - CENTER_LON) * deg2mLon(CENTER_LAT);
  const north_m = (lat - CENTER_LAT) * DEG2M_LAT;
  return enToCanvas(east_m, north_m, viewX, viewY, zoom);
}

function drawCoastline() {
  if (!togCoast.checked) return;
  ctx.fillStyle = LAND_COLOR;
  ctx.strokeStyle = "#45475a";
  ctx.lineWidth = 1;
  for (const poly of coastline) {
    if (poly.length < 3) continue;
    ctx.beginPath();
    for (let i = 0; i < poly.length; i++) {
      const [lon, lat] = poly[i];
      const [x, y] = latlonToCanvas(lat, lon, viewState.viewX, viewState.viewY, viewState.zoom);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
  }
}

function nodePosAtTick(nodeId, t) {
  const entry = truthTicks[t];
  if (!entry) return null;
  const n = entry.nodes[nodeId];
  if (!n) return null;
  return [n.east_m, n.north_m, n.depth_m];
}

function estimatePosAtTick(nodeId, t) {
  const m = pfIndex[nodeId] && pfIndex[nodeId][t];
  return m ? [m[0], m[1], m[2]] : null;
}

function drawTruthTrails() {
  if (!togTruth.checked) return;
  ctx.lineWidth = 1.6;
  for (const nodeId of NODE_IDS) {
    const cls = NODE_CLASSES[nodeId];
    ctx.strokeStyle = CLASS_COLORS[cls] || "#cdd6f4";
    ctx.beginPath();
    let started = false;
    for (let t = 0; t <= viewState.curTick; t++) {
      const p = nodePosAtTick(nodeId, t);
      if (!p) continue;
      const [x, y] = enToCanvas(p[0], p[1], viewState.viewX, viewState.viewY, viewState.zoom);
      if (!started) { ctx.moveTo(x, y); started = true; }
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
  }
}

function drawEstimateTrails() {
  if (!togEstimate.checked) return;
  ctx.lineWidth = 1.2;
  ctx.setLineDash([5, 3]);
  for (const nodeId of NODE_IDS) {
    const cls = NODE_CLASSES[nodeId];
    const baseColor = CLASS_COLORS[cls] || "#cdd6f4";
    ctx.strokeStyle = baseColor;
    ctx.globalAlpha = 0.75;
    ctx.beginPath();
    let started = false;
    for (let t = 0; t <= viewState.curTick; t++) {
      const p = estimatePosAtTick(nodeId, t);
      if (!p) continue;
      const [x, y] = enToCanvas(p[0], p[1], viewState.viewX, viewState.viewY, viewState.zoom);
      if (!started) { ctx.moveTo(x, y); started = true; }
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
    ctx.globalAlpha = 1.0;
  }
  ctx.setLineDash([]);
}

function drawLoraLinks() {
  if (!togLora.checked) return;
  const entry = truthTicks[viewState.curTick];
  if (!entry) return;
  const links = entry.lora_links || [];
  ctx.lineWidth = 1;
  for (const link of links) {
    const status = link.status;
    let color;
    let alpha = 0.85;
    if (status === "success") {
      color = LORA_COLORS.success;
    } else if (status === "dropped") {
      color = LORA_COLORS.dropped;
    } else if (status === "out_of_range") {
      color = LORA_COLORS.out_of_range;
      alpha = 0.3;
    } else {
      color = "#6c7086";
    }
    const a = nodePosAtTick(link.node_a, viewState.curTick);
    const b = nodePosAtTick(link.node_b, viewState.curTick);
    if (!a || !b) continue;
    const [ax, ay] = enToCanvas(a[0], a[1], viewState.viewX, viewState.viewY, viewState.zoom);
    const [bx, by] = enToCanvas(b[0], b[1], viewState.viewX, viewState.viewY, viewState.zoom);
    ctx.strokeStyle = color;
    ctx.globalAlpha = alpha;
    ctx.beginPath();
    ctx.moveTo(ax, ay);
    ctx.lineTo(bx, by);
    ctx.stroke();
    ctx.globalAlpha = 1.0;
  }
}

function drawParticleClouds() {
  if (!particles) return;
  for (const nodeId of Object.keys(particleIndex)) {
    if (!drillDownEnabled[nodeId]) continue;
    const entry = particleIndex[nodeId][viewState.curTick];
    if (!entry) continue;
    const cls = NODE_CLASSES[nodeId];
    const color = CLASS_COLORS[cls] || "#cdd6f4";
    ctx.fillStyle = color;
    const ps = entry.particles;
    const ws = entry.weights;
    let maxW = 0;
    for (const w of ws) if (w > maxW) maxW = w;
    if (maxW <= 0) maxW = 1;
    for (let i = 0; i < ps.length; i++) {
      const p = ps[i];
      const alpha = Math.min(0.9, 0.15 + 0.6 * (ws[i] / maxW));
      ctx.globalAlpha = alpha;
      const [x, y] = enToCanvas(p[0], p[1], viewState.viewX, viewState.viewY, viewState.zoom);
      ctx.beginPath();
      ctx.arc(x, y, 2, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1.0;
  }
}

function drawAnchorIcon(x, y, color) {
  ctx.fillStyle = color;
  ctx.strokeStyle = color;
  ctx.lineWidth = 1;
  // mast line
  ctx.beginPath();
  ctx.moveTo(x, y - 10);
  ctx.lineTo(x, y - 4);
  ctx.stroke();
  // filled triangle pointing up
  ctx.beginPath();
  ctx.moveTo(x, y - 4);
  ctx.lineTo(x - 5, y + 4);
  ctx.lineTo(x + 5, y + 4);
  ctx.closePath();
  ctx.fill();
}

function drawBallastDrifterIcon(x, y, color) {
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.arc(x, y, 4, 0, Math.PI * 2);
  ctx.fill();
}

function drawPureDrifterIcon(x, y, color) {
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.2;
  ctx.beginPath();
  ctx.arc(x, y, 4, 0, Math.PI * 2);
  ctx.stroke();
}

function drawNodeIcons() {
  if (!togIcons.checked) return;
  for (const nodeId of NODE_IDS) {
    const p = nodePosAtTick(nodeId, viewState.curTick);
    if (!p) continue;
    const cls = NODE_CLASSES[nodeId];
    const color = CLASS_COLORS[cls] || "#cdd6f4";
    const [x, y] = enToCanvas(p[0], p[1], viewState.viewX, viewState.viewY, viewState.zoom);
    if (cls === "anchor") {
      drawAnchorIcon(x, y, color);
    } else if (cls === "ballast_drifter") {
      drawBallastDrifterIcon(x, y, color);
    } else if (cls === "pure_drifter") {
      drawPureDrifterIcon(x, y, color);
    } else {
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(x, y, 3, 0, Math.PI * 2);
      ctx.fill();
    }
  }
}

function render() {
  ctx.clearRect(0, 0, ctx.W, ctx.H);
  drawCoastline();
  drawTruthTrails();
  drawEstimateTrails();
  drawLoraLinks();
  drawParticleClouds();
  drawNodeIcons();
  tickLabel.textContent = viewState.curTick + "/" + Math.max(0, N_TICKS - 1);
}

function setTick(t) {
  if (t < 0) t = 0;
  if (t > N_TICKS - 1) t = N_TICKS - 1;
  viewState.curTick = t;
  slider.value = String(t);
  render();
}

slider.addEventListener("input", () => { setTick(+slider.value); });
btnStep.addEventListener("click", () => setTick(viewState.curTick - 1));
btnStepFwd.addEventListener("click", () => setTick(viewState.curTick + 1));
btnReset.addEventListener("click", () => {
  viewState.viewX = 0;
  viewState.viewY = 0;
  viewState.zoom = 1;
  render();
});
btnPlay.addEventListener("click", togglePlay);

for (const tog of [togTruth, togEstimate, togLora, togCoast, togIcons]) {
  tog.addEventListener("change", render);
}

// Run selector — reload page with ?run=<name> on change.
const runSelector = document.getElementById("runSelector");
if (runSelector) {
  runSelector.addEventListener("change", () => {
    const u = new URL(window.location.href);
    u.searchParams.set("run", runSelector.value);
    window.location.href = u.toString();
  });
}

// Drill-down toggles.
document.querySelectorAll("input[data-particle-node]").forEach((el) => {
  const nodeId = el.getAttribute("data-particle-node");
  drillDownEnabled[nodeId] = el.checked;
  el.addEventListener("change", () => {
    drillDownEnabled[nodeId] = el.checked;
    render();
  });
});

function togglePlay() {
  playing = !playing;
  btnPlay.classList.toggle("active", playing);
  btnPlay.textContent = playing ? "❙❙" : "▶";
  if (playing) {
    playTimer = setInterval(() => {
      if (viewState.curTick < N_TICKS - 1) setTick(viewState.curTick + 1);
      else togglePlay();
    }, 200);
  } else if (playTimer) {
    clearInterval(playTimer);
    playTimer = null;
  }
}

// Pan
let dragging = false;
let dragStart = null;
canvas.addEventListener("mousedown", (e) => {
  dragging = true;
  canvas.classList.add("dragging");
  dragStart = { x: e.clientX, y: e.clientY, vx: viewState.viewX, vy: viewState.viewY };
});
window.addEventListener("mousemove", (e) => {
  if (!dragging || !dragStart) return;
  viewState.viewX = dragStart.vx + (e.clientX - dragStart.x);
  viewState.viewY = dragStart.vy + (e.clientY - dragStart.y);
  render();
});
window.addEventListener("mouseup", () => {
  dragging = false;
  canvas.classList.remove("dragging");
});

// Zoom
canvas.addEventListener("wheel", (e) => {
  e.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const cx = e.clientX - rect.left;
  const cy = e.clientY - rect.top;
  const factor = e.deltaY < 0 ? 1.1 : 1 / 1.1;
  // Center zoom on cursor: adjust viewX/viewY so the world point
  // under the cursor stays pinned.
  const worldX = (cx - ctx.W / 2 - viewState.viewX) / viewState.zoom;
  const worldY = (cy - ctx.H / 2 - viewState.viewY) / viewState.zoom;
  viewState.zoom *= factor;
  viewState.viewX = cx - ctx.W / 2 - worldX * viewState.zoom;
  viewState.viewY = cy - ctx.H / 2 - worldY * viewState.zoom;
  render();
}, { passive: false });

// Keyboard
window.addEventListener("keydown", (e) => {
  if (e.code === "Space") {
    e.preventDefault();
    togglePlay();
  } else if (e.code === "ArrowLeft") {
    setTick(viewState.curTick - 1);
  } else if (e.code === "ArrowRight") {
    setTick(viewState.curTick + 1);
  }
});

window.addEventListener("resize", () => { resizeCanvas(); render(); });

resizeCanvas();
render();
</script>
</body>
</html>
"""


def _build_drill_down_section(sidecar_node_ids: frozenset[str]) -> str:
    if not sidecar_node_ids:
        return ""
    # Sort for determinism.
    lines = ["  <h2>Particle drill-down</h2>"]
    for node_id in sorted(sidecar_node_ids):
        # Escape the node_id for HTML attribute safety. Node IDs are
        # generator-produced ASCII identifiers, but we quote defensively.
        safe = (
            node_id.replace("&", "&amp;")
            .replace("\"", "&quot;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        lines.append(
            f'  <label><input type="checkbox" data-particle-node="{safe}"> {safe}</label>'
        )
    return "\n".join(lines)


def _build_run_options_html(
    runs_for_selector: Sequence[Run], current_run_name: str
) -> tuple[str, str]:
    """Return ``(options_html, current_description)``.

    ``options_html`` is the inner ``<select>`` markup; ``current_description``
    is the dropdown's description-line text for the currently-displayed run.
    """
    options: list[str] = []
    current_desc = ""
    for run in runs_for_selector:
        safe_name = (
            run.name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;")
        )
        # Option label is the name only — description shown in #run-desc.
        selected = " selected" if run.name == current_run_name else ""
        options.append(
            f'<option value="{safe_name}"{selected}>{safe_name}</option>'
        )
        if run.name == current_run_name:
            current_desc = run.description
    safe_desc = (
        current_desc.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    return "\n".join(options), safe_desc


def build_dashboard_html(
    scenario_header: Any,
    scenario_ticks: Sequence[Any],
    pf_estimates: Sequence[Any],
    particle_records: Sequence[Any],
    coastline_geojson: Iterable[np.ndarray],
    sidecar_node_ids: frozenset[str],
    runs_for_selector: Sequence[Run] = (),
    current_run_name: str = "",
) -> str:
    """Assemble the complete self-contained HTML page.

    Deterministic: same inputs always produce the same byte string.
    """
    blob: dict[str, Any] = {
        "header": _header_to_json(scenario_header),
        "truth_ticks": _truth_ticks_to_json(scenario_ticks),
        "pf_estimates": _pf_estimates_to_json(pf_estimates),
        "coastline": _coastline_to_json(coastline_geojson, scenario_header.bbox),
    }
    if sidecar_node_ids:
        blob["particles"] = _particles_to_json(particle_records)
    else:
        blob["particles"] = None

    # Escape any literal </script> sequences in the JSON blob (JSON
    # encoding won't produce them naturally, but defense-in-depth).
    json_text = json.dumps(blob, indent=2, allow_nan=False)
    json_text = json_text.replace("</script>", "<\\/script>")

    drill_html = _build_drill_down_section(sidecar_node_ids)
    options_html, current_desc = _build_run_options_html(
        runs_for_selector, current_run_name
    )
    html = _HTML_SHELL.replace("__SCENARIO_DATA__", json_text)
    html = html.replace("__DRILL_DOWN_SECTION__", drill_html)
    html = html.replace("__RUN_OPTIONS__", options_html)
    html = html.replace("__RUN_DESC__", current_desc)
    return html


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------


def _make_handler(
    html_by_run: dict[str, bytes], default_run: str
) -> type[BaseHTTPRequestHandler]:
    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            if path not in ("/", "/index.html"):
                self.send_response(404)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"not found\n")
                return
            qs = parse_qs(parsed.query)
            run_name = qs.get("run", [default_run])[0]
            html = html_by_run.get(run_name, html_by_run[default_run])
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, format: str, *args: Any) -> None:
            del format, args  # suppress request logging

    return DashboardHandler


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _build_html_for_run(
    run: Run, runs_for_selector: Sequence[Run], coastline_polys: list[np.ndarray]
) -> bytes:
    """Load one run's data and produce the inlined-HTML byte string.

    A missing optional particles file warns to stderr and proceeds
    without drill-down — same explicit-error policy as single-run mode.
    """
    truth_reader = ScenarioTruthReader(run.scenario_path)
    scenario_header = truth_reader.header()
    scenario_ticks = list(truth_reader)

    pf_estimates = list(PFEstimateReader(run.estimates_path))

    particle_records: Sequence[Any] = ()
    sidecar_node_ids: frozenset[str] = frozenset()
    eff_part = run.particles_path
    if eff_part is not None and not eff_part.exists():
        print(
            f"Warning: particles file not found for run {run.name!r}: "
            f"{eff_part}; proceeding without drill-down",
            file=sys.stderr,
            flush=True,
        )
        eff_part = None
    if eff_part is not None:
        particle_records = list(ParticleStreamReader(eff_part))
        sidecar_node_ids = ParticleStreamReader(eff_part).node_ids_present()

    html = build_dashboard_html(
        scenario_header=scenario_header,
        scenario_ticks=scenario_ticks,
        pf_estimates=pf_estimates,
        particle_records=particle_records,
        coastline_geojson=coastline_polys,
        sidecar_node_ids=sidecar_node_ids,
        runs_for_selector=runs_for_selector,
        current_run_name=run.name,
    )
    return html.encode("utf-8")


def serve_runs(runs: list[Run], port: int = 8911, open_browser: bool = True) -> None:
    """Eager-build HTML for each run, serve via local HTTP. Blocks until Ctrl-C.

    The first run in the list is the default; ``?run=<name>`` selects.
    """
    coastline_polys = load_coastline_geojson(str(COASTLINE_PATH))
    html_by_run: dict[str, bytes] = {
        r.name: _build_html_for_run(r, runs, coastline_polys) for r in runs
    }
    default_run = runs[0].name

    handler = _make_handler(html_by_run, default_run)
    server = HTTPServer(("0.0.0.0", port), handler)
    actual_port = server.server_address[1]
    url = f"http://localhost:{actual_port}/"
    print(url, flush=True)
    print(
        f"Serving {len(runs)} run(s); default = {default_run}; Ctrl-C to stop",
        file=sys.stderr,
        flush=True,
    )

    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            # Auto-open is a convenience, not a correctness requirement.
            pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def serve_dashboard(
    scenario_path: Path,
    estimates_path: Path,
    particles_path: Path | None = None,
    port: int = 8911,
    open_browser: bool = True,
) -> None:
    """Single-run wrapper around ``serve_runs`` (back-compat for tests)."""
    run = Run(
        name=scenario_path.stem or "run",
        description="",
        scenario_path=scenario_path,
        estimates_path=estimates_path,
        particles_path=particles_path,
    )
    serve_runs([run], port=port, open_browser=open_browser)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="M1 maritime visual-validation dashboard."
    )
    parser.add_argument("--scenario", type=Path, default=None, help="Scenario JSONL path (single-run mode).")
    parser.add_argument("--estimates", type=Path, default=None, help="PF estimates JSONL path (single-run mode).")
    parser.add_argument("--particles", type=Path, default=None, help="Optional particle sidecar JSONL path (single-run mode).")
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=None,
        help=(
            "Directory containing run subdirs (each with scenario.jsonl + "
            "estimates.jsonl, optional particles.jsonl + manifest.json). "
            "Mutually exclusive with --scenario/--estimates."
        ),
    )
    parser.add_argument("--port", type=int, default=8911, help="HTTP port (0 = ephemeral).")
    parser.add_argument("--no-open", action="store_true", help="Do not auto-open a browser.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)

    single_run = args.scenario is not None or args.estimates is not None
    multi_run = args.runs_dir is not None

    if single_run and multi_run:
        print(
            "Error: --runs-dir is mutually exclusive with --scenario / --estimates",
            file=sys.stderr,
            flush=True,
        )
        return 2
    if not single_run and not multi_run:
        print(
            "Error: must specify either --runs-dir OR (--scenario AND --estimates)",
            file=sys.stderr,
            flush=True,
        )
        return 2

    if multi_run:
        runs_dir: Path = args.runs_dir
        if not runs_dir.is_dir():
            print(
                f"Error: runs dir not found: {runs_dir}",
                file=sys.stderr,
                flush=True,
            )
            return 2
        try:
            runs = discover_runs(runs_dir)
        except (FileNotFoundError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr, flush=True)
            return 2
        try:
            serve_runs(runs, port=args.port, open_browser=not args.no_open)
        except (ValueError, FileNotFoundError) as exc:
            print(
                f"Error: failed to load run data from {runs_dir}: {exc}",
                file=sys.stderr,
                flush=True,
            )
            return 1
        except OSError as exc:
            print(f"Error: failed to bind HTTP server: {exc}", file=sys.stderr, flush=True)
            return 1
        return 0

    # Single-run path (back-compat with M1 contract tests).
    if args.scenario is None or args.estimates is None:
        print(
            "Error: --scenario AND --estimates are both required in single-run mode",
            file=sys.stderr,
            flush=True,
        )
        return 2
    scenario_path: Path = args.scenario
    estimates_path: Path = args.estimates
    particles_path: Path | None = args.particles

    if not scenario_path.exists():
        print(
            f"Error: scenario file not found: {scenario_path}",
            file=sys.stderr,
            flush=True,
        )
        return 2
    if not estimates_path.exists():
        print(
            f"Error: estimates file not found: {estimates_path}",
            file=sys.stderr,
            flush=True,
        )
        return 2

    effective_particles: Path | None
    if particles_path is None:
        effective_particles = None
    elif particles_path.exists():
        effective_particles = particles_path
    else:
        print(
            f"Warning: particles file not found: {particles_path}; "
            "proceeding without drill-down",
            file=sys.stderr,
            flush=True,
        )
        effective_particles = None

    try:
        serve_dashboard(
            scenario_path=scenario_path,
            estimates_path=estimates_path,
            particles_path=effective_particles,
            port=args.port,
            open_browser=not args.no_open,
        )
    except (ValueError, FileNotFoundError) as exc:
        print(
            f"Error: failed to load scenario {scenario_path} / estimates "
            f"{estimates_path}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return 1
    except OSError as exc:
        print(f"Error: failed to bind HTTP server: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
