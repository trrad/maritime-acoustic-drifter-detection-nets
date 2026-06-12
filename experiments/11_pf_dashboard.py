"""
Particle filter dashboard — interactive HTML/JS single-page app.

Reads JSON-lines from stdin into memory, serves an interactive dashboard
with time slider and autoplay on localhost:8911.

Usage:
    cat build/rtl_trace.jsonl | uv run python experiments/11_pf_dashboard.py

    uv run python experiments/10_drone_pf_lns.py --stream --method delta | \
        uv run python experiments/11_pf_dashboard.py

    # Custom port:
    uv run python experiments/11_pf_dashboard.py --port 9000
"""

import argparse
import json
import sys
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# Shared state
state_lock = threading.Lock()
all_records = []
is_done = False


# ---------------------------------------------------------------------------
# HTML page with inline JS/CSS
# ---------------------------------------------------------------------------

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>LNS8 Particle Filter</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  background: #1a0f1f; color: #cdd6f4;
  font-family: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace;
  overflow: hidden; height: 100vh; display: flex; flex-direction: column;
}
.header {
  display: flex; align-items: center; gap: 16px; padding: 8px 16px;
  background: #1e1529; border-bottom: 1px solid #313244;
}
.header h1 { font-size: 14px; color: #cba6f7; white-space: nowrap; }
.transport { display: flex; align-items: center; gap: 8px; flex: 1; }
.transport button {
  background: #313244; color: #cdd6f4; border: 1px solid #45475a;
  border-radius: 4px; padding: 4px 10px; cursor: pointer; font-size: 13px;
  font-family: inherit;
}
.transport button:hover { background: #45475a; }
.transport button.active { background: #cba6f7; color: #1e1529; }
.slider-wrap { flex: 1; display: flex; align-items: center; gap: 8px; }
.slider-wrap input[type=range] {
  flex: 1; accent-color: #cba6f7; height: 6px;
}
.slider-wrap .step-label {
  font-size: 12px; color: #a6adc8; min-width: 60px; text-align: right;
}
.panels {
  flex: 1; display: grid; grid-template-columns: 1fr 1fr; grid-template-rows: 1fr 1fr;
  gap: 4px; padding: 4px;
}
.panel { position: relative; background: #181225; border: 1px solid #313244; border-radius: 4px; }
.panel canvas { width: 100%; height: 100%; display: block; }
.panel .label {
  position: absolute; top: 4px; left: 8px; font-size: 11px; color: #a6adc8;
}
.footer {
  display: flex; gap: 24px; padding: 6px 16px;
  background: #1e1529; border-top: 1px solid #313244;
  font-size: 11px; color: #a6adc8; flex-wrap: wrap;
}
.footer .section { display: flex; gap: 6px; align-items: center; }
.footer .val { color: #cdd6f4; }
.footer .dim-rmse { color: #89b4fa; }
</style>
</head>
<body>

<div class="header">
  <h1>LNS8 Particle Filter</h1>
  <div class="transport">
    <button id="btn-prev" title="Previous step">&#9664;</button>
    <div class="slider-wrap">
      <input type="range" id="slider" min="0" max="0" value="0">
      <span class="step-label" id="step-label">0/0</span>
    </div>
    <button id="btn-next" title="Next step">&#9654;</button>
    <button id="btn-play" title="Play/Pause">&#9654;&#9654;</button>
  </div>
</div>

<div class="panels">
  <div class="panel"><div class="label">X position / VX velocity</div><canvas id="c-x"></canvas></div>
  <div class="panel"><div class="label">Y position / VY velocity</div><canvas id="c-y"></canvas></div>
  <div class="panel"><div class="label">Z position / VZ velocity</div><canvas id="c-z"></canvas></div>
  <div class="panel"><div class="label">Estimation Error</div><canvas id="c-err"></canvas></div>
</div>

<div class="footer" id="footer"></div>

<script>
"use strict";

let DATA = [];
let curStep = 0;
let playing = false;
let playTimer = null;
let streaming = true;

const DIM_POS = ['x', 'y', 'z'];
const DIM_VEL = ['vx', 'vy', 'vz'];
const DIM_ALL = ['x', 'y', 'z', 'vx', 'vy', 'vz'];

// Colors (Catppuccin Mocha)
const COL = {
  truth: '#f38ba8',     // red
  estimate: '#89b4fa',  // blue
  particle: '#a6e3a1',  // green
  spread: 'rgba(166,227,161,0.15)',
  velTruth: '#f5c2e7',  // pink
  velEst: '#74c7ec',    // sapphire
  grid: '#313244',
  text: '#a6adc8',
  bg: '#181225',
  barColors: ['#f38ba8','#89b4fa','#a6e3a1','#f5c2e7','#74c7ec','#fab387'],
};

const slider = document.getElementById('slider');
const stepLabel = document.getElementById('step-label');
const btnPrev = document.getElementById('btn-prev');
const btnNext = document.getElementById('btn-next');
const btnPlay = document.getElementById('btn-play');

slider.addEventListener('input', () => { curStep = +slider.value; render(); });
btnPrev.addEventListener('click', () => { if (curStep > 0) { curStep--; slider.value = curStep; render(); } });
btnNext.addEventListener('click', () => { if (curStep < DATA.length - 1) { curStep++; slider.value = curStep; render(); } });
btnPlay.addEventListener('click', togglePlay);

function togglePlay() {
  playing = !playing;
  btnPlay.classList.toggle('active', playing);
  if (playing) {
    playTimer = setInterval(() => {
      if (curStep < DATA.length - 1) { curStep++; slider.value = curStep; render(); }
      else { togglePlay(); }
    }, 200);
  } else {
    clearInterval(playTimer); playTimer = null;
  }
}

// Fetch data
function fetchData() {
  fetch('/data.json').then(r => r.json()).then(d => {
    DATA = d;
    slider.max = Math.max(0, DATA.length - 1);
    if (curStep >= DATA.length) curStep = DATA.length - 1;
    if (curStep < 0) curStep = 0;
    slider.value = curStep;
    render();
  }).catch(() => {});
}

function pollStatus() {
  fetch('/status').then(r => r.text()).then(t => {
    if (t.includes('done')) {
      streaming = false;
      fetchData(); // final fetch
    } else {
      fetchData();
      setTimeout(pollStatus, 800);
    }
  }).catch(() => { setTimeout(pollStatus, 2000); });
}

// Canvas helpers
function getCtx(id) {
  const c = document.getElementById(id);
  const r = c.parentElement.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  c.width = r.width * dpr;
  c.height = r.height * dpr;
  const ctx = c.getContext('2d');
  ctx.scale(dpr, dpr);
  ctx.W = r.width; ctx.H = r.height;
  return ctx;
}

function range(arr) {
  if (!arr.length) return [0, 1];
  let mn = Infinity, mx = -Infinity;
  for (const v of arr) { if (isFinite(v)) { if (v < mn) mn = v; if (v > mx) mx = v; } }
  if (mn === mx) { mn -= 0.5; mx += 0.5; }
  const pad = (mx - mn) * 0.08;
  return [mn - pad, mx + pad];
}

function mapX(t, tMin, tMax, W, margin) {
  return margin.left + (t - tMin) / Math.max(tMax - tMin, 1) * (W - margin.left - margin.right);
}
function mapY(v, vMin, vMax, H, margin) {
  return margin.top + (1 - (v - vMin) / Math.max(vMax - vMin, 1e-9)) * (H - margin.top - margin.bottom);
}

// Draw position panel with dual y-axes
function drawPosPanel(canvasId, posDim, velDim, dimIdx) {
  const ctx = getCtx(canvasId);
  const W = ctx.W, H = ctx.H;
  const M = {top: 16, bottom: 20, left: 44, right: 44};

  ctx.clearRect(0, 0, W, H);

  const end = curStep + 1;
  const steps = []; const truthP = []; const estP = [];
  const truthV = []; const estV = [];
  const pLo = []; const pHi = [];

  for (let i = 0; i < end && i < DATA.length; i++) {
    const r = DATA[i];
    steps.push(r.t);
    truthP.push(r.truth[posDim] || 0);
    estP.push(r.estimate[posDim] || 0);
    truthV.push(r.truth[velDim] || 0);
    estV.push(r.estimate[velDim] || 0);

    const pp = r.particles && r.particles[posDim];
    if (pp && pp.length) {
      const sorted = [...pp].sort((a, b) => a - b);
      pLo.push(sorted[Math.floor(sorted.length * 0.02)]);
      pHi.push(sorted[Math.floor(sorted.length * 0.98)]);
    } else {
      pLo.push(estP[i]); pHi.push(estP[i]);
    }
  }

  if (!steps.length) return;

  const tR = [steps[0], Math.max(steps[steps.length-1], steps[0]+1)];
  const allPos = truthP.concat(estP, pLo, pHi);
  const posR = range(allPos);
  const allVel = truthV.concat(estV);
  const velR = range(allVel);

  // Grid
  ctx.strokeStyle = COL.grid; ctx.lineWidth = 0.5;
  for (let i = 0; i < 5; i++) {
    const y = M.top + i * (H - M.top - M.bottom) / 4;
    ctx.beginPath(); ctx.moveTo(M.left, y); ctx.lineTo(W - M.right, y); ctx.stroke();
  }

  // Spread band
  ctx.fillStyle = COL.spread;
  ctx.beginPath();
  for (let i = 0; i < steps.length; i++) {
    const x = mapX(steps[i], tR[0], tR[1], W, M);
    const y = mapY(pHi[i], posR[0], posR[1], H, M);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  }
  for (let i = steps.length - 1; i >= 0; i--) {
    const x = mapX(steps[i], tR[0], tR[1], W, M);
    const y = mapY(pLo[i], posR[0], posR[1], H, M);
    ctx.lineTo(x, y);
  }
  ctx.closePath(); ctx.fill();

  // Particle dots at current step
  const curRec = DATA[curStep];
  if (curRec && curRec.particles && curRec.particles[posDim]) {
    ctx.fillStyle = COL.particle;
    ctx.globalAlpha = 0.35;
    const pp = curRec.particles[posDim];
    for (const v of pp) {
      const x = mapX(curRec.t, tR[0], tR[1], W, M);
      const y = mapY(v, posR[0], posR[1], H, M);
      ctx.beginPath(); ctx.arc(x, y, 2, 0, Math.PI * 2); ctx.fill();
    }
    ctx.globalAlpha = 1;
  }

  // Truth position line
  ctx.strokeStyle = COL.truth; ctx.lineWidth = 1.5;
  ctx.beginPath();
  for (let i = 0; i < steps.length; i++) {
    const x = mapX(steps[i], tR[0], tR[1], W, M);
    const y = mapY(truthP[i], posR[0], posR[1], H, M);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  }
  ctx.stroke();

  // Estimate position line (dashed)
  ctx.strokeStyle = COL.estimate; ctx.lineWidth = 1.5; ctx.setLineDash([4,3]);
  ctx.beginPath();
  for (let i = 0; i < steps.length; i++) {
    const x = mapX(steps[i], tR[0], tR[1], W, M);
    const y = mapY(estP[i], posR[0], posR[1], H, M);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  }
  ctx.stroke();
  ctx.setLineDash([]);

  // Velocity truth (right axis, thin)
  ctx.strokeStyle = COL.velTruth; ctx.lineWidth = 0.8;
  ctx.beginPath();
  for (let i = 0; i < steps.length; i++) {
    const x = mapX(steps[i], tR[0], tR[1], W, M);
    const y = mapY(truthV[i], velR[0], velR[1], H, M);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  }
  ctx.stroke();

  // Velocity estimate (right axis, thin dashed)
  ctx.strokeStyle = COL.velEst; ctx.lineWidth = 0.8; ctx.setLineDash([3,2]);
  ctx.beginPath();
  for (let i = 0; i < steps.length; i++) {
    const x = mapX(steps[i], tR[0], tR[1], W, M);
    const y = mapY(estV[i], velR[0], velR[1], H, M);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  }
  ctx.stroke();
  ctx.setLineDash([]);

  // Y-axis labels (left = position)
  ctx.fillStyle = COL.text; ctx.font = '9px monospace'; ctx.textAlign = 'right';
  for (let i = 0; i <= 4; i++) {
    const v = posR[0] + i * (posR[1] - posR[0]) / 4;
    const y = mapY(v, posR[0], posR[1], H, M);
    ctx.fillText(v.toFixed(1), M.left - 3, y + 3);
  }

  // Y-axis labels (right = velocity)
  ctx.textAlign = 'left'; ctx.fillStyle = COL.velEst;
  for (let i = 0; i <= 4; i++) {
    const v = velR[0] + i * (velR[1] - velR[0]) / 4;
    const y = mapY(v, velR[0], velR[1], H, M);
    ctx.fillText(v.toFixed(2), W - M.right + 3, y + 3);
  }
}

// Draw error panel
function drawErrorPanel() {
  const ctx = getCtx('c-err');
  const W = ctx.W, H = ctx.H;
  const M = {top: 16, bottom: 20, left: 44, right: 12};

  ctx.clearRect(0, 0, W, H);

  if (!DATA.length || curStep >= DATA.length) return;
  const rec = DATA[curStep];

  // Current absolute errors
  const errors = DIM_ALL.map(d => Math.abs((rec.truth[d] || 0) - (rec.estimate[d] || 0)));
  const maxErr = Math.max(...errors, 0.01);

  const barH = (H - M.top - M.bottom - 10) / 6;
  const barArea = W - M.left - M.right;

  for (let i = 0; i < 6; i++) {
    const y = M.top + i * (barH + 1) + 1;
    const w = (errors[i] / maxErr) * barArea * 0.7;

    ctx.fillStyle = COL.barColors[i];
    ctx.globalAlpha = 0.7;
    ctx.fillRect(M.left, y, w, barH - 2);
    ctx.globalAlpha = 1;

    // Dim label
    ctx.fillStyle = COL.text; ctx.font = '9px monospace'; ctx.textAlign = 'right';
    ctx.fillText(DIM_ALL[i], M.left - 4, y + barH / 2 + 3);

    // Error value
    ctx.fillStyle = COL.barColors[i]; ctx.textAlign = 'left';
    ctx.fillText(errors[i].toFixed(3), M.left + w + 4, y + barH / 2 + 3);

    // RMSE
    if (rec.rmse && rec.rmse[DIM_ALL[i]] !== undefined) {
      ctx.fillStyle = '#585b70';
      ctx.fillText('rmse=' + rec.rmse[DIM_ALL[i]].toFixed(3),
        M.left + barArea * 0.75, y + barH / 2 + 3);
    }
  }
}

// Update footer stats
function updateFooter() {
  const el = document.getElementById('footer');
  if (!DATA.length || curStep >= DATA.length) { el.innerHTML = ''; return; }
  const rec = DATA[curStep];
  const cyc = rec.cycles || {};
  const total = cyc.total || 0;
  const hz30 = total > 0 ? Math.round(30e6 / total) : 0;
  const nP = rec.particles && rec.particles.x ? rec.particles.x.length : '?';

  let parts = [];
  parts.push(`<span class="section">Step <span class="val">${rec.t}/${DATA.length-1}</span></span>`);
  parts.push(`<span class="section">Method: <span class="val">${rec.method || '?'}</span></span>`);
  parts.push(`<span class="section">Particles: <span class="val">${nP}</span></span>`);

  // Cycle breakdown
  let cycParts = [];
  for (const p of ['predict','weight','resample','recenter']) {
    const v = cyc[p] || 0;
    if (v > 0) {
      const pct = (100 * v / Math.max(total, 1)).toFixed(1);
      cycParts.push(`${p} ${v.toLocaleString()} (${pct}%)`);
    }
  }
  if (cycParts.length) {
    parts.push(`<span class="section">Cycles: <span class="val">${cycParts.join(' | ')}</span></span>`);
  }

  parts.push(`<span class="section">Throughput: <span class="val">${hz30.toLocaleString()} Hz</span> @ 30 MHz</span>`);

  // Per-dim RMSE
  if (rec.rmse) {
    let rmseStr = DIM_ALL.map(d => `${d}=${(rec.rmse[d]||0).toFixed(3)}`).join('  ');
    parts.push(`<span class="section">RMSE: <span class="dim-rmse">${rmseStr}</span></span>`);
  }

  el.innerHTML = parts.join('');
}

function render() {
  stepLabel.textContent = DATA.length ? `${curStep}/${DATA.length-1}` : '0/0';
  drawPosPanel('c-x', 'x', 'vx', 0);
  drawPosPanel('c-y', 'y', 'vy', 1);
  drawPosPanel('c-z', 'z', 'vz', 2);
  drawErrorPanel();
  updateFooter();
}

// Handle resize
window.addEventListener('resize', render);

// Initial load
fetchData();
pollStatus();
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split('?')[0]  # strip query params

        if path == '/' or path.startswith('/index'):
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(DASHBOARD_HTML.encode())

        elif path == '/data.json':
            with state_lock:
                data = list(all_records)
            payload = json.dumps(data).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        elif path == '/status':
            with state_lock:
                done = is_done
            msg = 'done' if done else 'streaming'
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(msg.encode())

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # suppress request logs


# ---------------------------------------------------------------------------
# Stdin reader
# ---------------------------------------------------------------------------

def read_stdin():
    global is_done

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        with state_lock:
            all_records.append(rec)

    with state_lock:
        is_done = True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='PF dashboard web server')
    parser.add_argument('--port', type=int, default=8911,
                        help='HTTP port (default: 8911)')
    args = parser.parse_args()

    # Start stdin reader in background thread
    reader = threading.Thread(target=read_stdin, daemon=True)
    reader.start()

    server = HTTPServer(('0.0.0.0', args.port), DashboardHandler)
    print(f"Dashboard: http://localhost:{args.port}", file=sys.stderr)
    print("Server keeps running after stream ends. Ctrl-C to stop.",
          file=sys.stderr)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()


if __name__ == '__main__':
    main()
