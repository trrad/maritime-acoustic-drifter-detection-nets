# How it works: the ocean, the estimator, and the radio

*A plain-English walkthrough of the physics and inference under the fleet
results in the [README](../README.md). Code lives in
`experiments/harmonic_prototype/`; the running findings log is
[FINDINGS.md](../experiments/harmonic_prototype/FINDINGS.md).*

A drifter in this project is a small buoy with a hydrophone, a ballast pump,
a LoRa radio, and a microcontroller-class brain. It has three hard problems:
the ocean keeps moving it, it can't use GPS while submerged, and its radio
only works at the surface. Everything below is about turning those three
constraints into a working surveillance system.

## 1. The ocean we're riding

Current at any point in the sea is, usefully, two things added together: a
**predictable part** driven by the tides, and **everything else** — wind,
river outflow, eddies — which behaves like weather.

The predictable part is genuinely predictable. Tides are driven by
astronomy, so tidal currents are a sum of sinusoids at *exactly known
frequencies* (the largest, called M2, is the twice-daily lunar tide; S2, K1,
O1 are siblings). Fit the amplitude and phase of each sinusoid to a month of
data and you can predict that component of the current weeks ahead. This is
classical harmonic analysis, and it's the "harmonic" in
`harmonic_prototype`.

Two facts about the Strait of Georgia shape the whole design:

1. **The tide is a minority of the motion at the surface.** Harmonic
   analysis against the SalishSeaCast ocean model shows the major tidal
   constituents explain only ~9–16 % of surface current variance here. The
   rest is the "weather" part — Fraser River plume, wind events, eddies.
   So a drifter can't just consult a tide table; it has to *learn* the local
   weather as it goes (section 3).

2. **The current is different at different depths — in a structured way.**
   The M2 tide at 24 m depth lags the surface by about 33° of phase — about
   1.1 hours. The river plume only occupies the top few meters. Wind-driven
   flow decays over the top ~20 m. A drifter that can change its depth by
   pumping ballast is therefore picking *which* of several different
   conveyor belts to stand on. That's the entire steering mechanism: no
   propeller, just choosing your layer.

The "weather" part isn't formless either. The simulation's error model (what
the real ocean does that the model ocean doesn't) is built from five layers,
each with its own horizontal size and depth reach, calibrated against
published current-meter and radar studies:

| Layer | What it is | Size | How deep it reaches |
|---|---|---|---|
| Basin-coherent | tide & exchange-flow mismatch | ~5 km | all depths |
| Plume | Fraser River freshwater slab | ~2 km | top ~5 m, sharp cutoff |
| Submesoscale + wind | small eddies, fronts, wind-driven layer | ~5 km | fades over ~20 m |
| Near-inertial | post-windstorm rotation (16.5 h period at this latitude) | ~20 km | fades over ~20 m |
| White | unresolvable small-scale shear | ~1 km | all depths |

Total: about 8 cm/s of unpredicted current at the surface — which over a
6-hour dive is ~1.7 km of position error if you do nothing about it. That
number is why the estimator exists.

## 2. What a drifter can sense

Submerged, a drifter in the current model has exactly three senses:
pressure (so it knows its depth well), a CTD (conductivity–temperature–
depth — in practice, the temperature and salinity of the water around it),
and the hydrophone. There's no speedometer and no inertial dead-reckoning
in the loop: underwater position information comes from physics, not
motion sensing.

Salinity earns its place. The Fraser River plume is a slab of fresher
water riding on top of the salty strait, with sharp edges in both depth
and map position. That structure makes a salinity reading a *position*
observation: a position hypothesis that puts the drifter inside the plume
while the water tastes salty is in the wrong place, and loses weight
accordingly. The drifter literally tastes the water to work out which
water mass — and therefore roughly where — it is.

GPS doesn't penetrate seawater, and neither does any useful radio signal;
absolute fixes happen only at the surface.

At the surface, it gets two things: a GPS fix, and LoRa radio contact with
the rest of the fleet — including a few **anchor buoys**, fixed nodes with
GPS and satellite uplink that act as the fleet's georeferenced backbone.
Ranging to those anchors (by radio time-of-flight, good to roughly 20–100 m
over the sea) gives a position even when a drifter's own GPS fix is
degraded.

So a drifter's life is a sawtooth: surface, learn where you are to within
tens of meters; dive, and watch your position uncertainty grow as unknown
currents carry you; surface again, snap the uncertainty back down.

## 3. The estimator: a cloud of guesses plus a learned correction map

Between fixes, the drifter tracks its position with a **particle filter**:
it maintains a few hundred candidate positions ("particles"). Every time
step, each particle is moved by the predicted current (tidal prediction
plus the learned correction described next) plus random noise representing
what we don't know. When a measurement arrives — a salinity reading, a LoRa
range, a GPS fix — particles that agree with it get up-weighted, particles
that don't get down-weighted, and the cloud is periodically resampled so it
concentrates where the evidence points. The cloud's spread *is* the
drifter's honest uncertainty.

The clever part is what rides along with each particle. The "weather" —
the few-cm/s discrepancy between the predicted and the actual current —
is worth learning, because it persists for hours to days and has
kilometer-scale structure (section 1's table). The estimator represents it
as a **bias field**: a coarse grid of correction vectors over the local
patch of ocean, with built-in smoothness (nearby cells are statistically
tied to each other over ~5 km, so a measurement in one place also informs
its neighborhood).

Crucially, this is not a free-form fudge layer. The correction is
**structured by the same physics as section 1's table**. The field is
resolved per depth slab, because a correction learned at 2 m (plume
territory) says nothing about the current at 24 m. Its smoothness scale is
matched to the layers that are actually learnable. And the layers that
*aren't* learnable — the rotating inertial signal, the small-scale white
noise — are deliberately excluded from the field and budgeted as
measurement noise instead. The structural knowledge runs both directions:
knowing the plume is a shallow, sharp-edged, fresh feature is exactly what
makes salinity a useful observation of it. Learning "the bias" as one
unstructured blur would smear plume-edge errors across the whole patch and
every depth. (The current implementation resolves depth slabs
independently; folding each physical layer's known depth profile into
shared cross-depth components is the queued next step.)

Estimating a smooth field from drift measurements is a linear-Gaussian
problem — exactly what a **Kalman filter** solves optimally. So the design
is a *Rao-Blackwellized* particle filter: particles carry only the hard
nonlinear unknown (position), and each particle carries its own small
Kalman filter for the bias field, conditioned on that particle's position
history. In effect each candidate trajectory maintains its own running map
of the local current weather, and trajectories whose maps keep predicting
the drift correctly win the resampling.

One discipline keeps this honest: the noise budget is **split, not
duplicated**. Ocean-error layers the grid can actually represent (the
slow, ≥5 km ones) are assigned to the bias field's prior; layers it cannot
represent (the rotating inertial signal, small-scale white noise) are
assigned to measurement noise. Counting a layer in both places makes the
filter falsely confident it has already explained the discrepancy, and it
stops learning.

Finally, time runs in two directions. The forward filter is what the
drifter knows *live* — that's what the depth controller acts on, and the
only thing coverage claims are allowed to use. But for the actual mission —
"where exactly was I when I heard that boat?" — the drifter can wait until
its *next* surfacing fix and run a smoother **backward** from it (an RTS
smoother), pinning down the past trajectory far more tightly than it was
known at the time. Position accuracy is needed retroactively, not live.
That one asymmetry is what lets a node spend most of its life submerged
and silent and still contribute ~100 m-grade positions to triangulation.

## 4. The radio is the bottleneck

Seawater blocks radio. That single fact shapes the fleet design more than
any other:

- **Communication and position fixes only happen at the surface**, so
  surfacing cadence — not the quality of any sensor — sets how fast
  position uncertainty is reset. The fleet's coverage "half-life" is about
  2.5 hours: that's how long after a fix the typical triangulation error
  stays under 500 m. This is why surfacing *policy* is a first-class
  design axis in the sweeps.
- **LoRa is the workhorse** because it's free to operate, peer-to-peer,
  and low-power (~220 µW average duty cycle). The price is physics: with
  antennas centimeters above the waterline, 5–10 km of range is realistic.
  The fleet shares the channel on a one-hour TDMA frame (each node gets
  assigned slots), which also provides the time-of-flight ranging.
- **Satellite (Iridium) lives only on the anchor buoys.** Drifters report
  through the mesh to an anchor; the anchor uplinks. This keeps the
  per-drifter cost and power down and concentrates the expensive hardware
  in a handful of nodes.
- **Listening is nearly free; everything else isn't.** The hydrophone's
  always-on envelope detector runs at ~100 µW; the wake-up classifier
  costs a few mW in bursts; a surfacing event costs ballast pumping plus
  time not spent listening at depth.

The surfacing policy ties sections 3 and 4 together. The naive schedule —
surface every 6 hours no matter what — works but spends ~450
surfacings/week across a 16-node fleet. The event-driven policy instead
surfaces a drifter ~30 minutes *after it hears something worth reporting*,
with a 12-hour safety cap. "Worth reporting" uses a track-divergence test:
if a contact keeps pinging along the track already reported, stay down; if
it diverges more than ~500 m from what was last exfiltrated, surface. That
gets a tight smoothing window around exactly the moments that matter
(events), which is why its triangulation error (~80 m median) beats the
fixed schedule's (~200 m) while surfacing half as often. Its weakness —
a drifter that drifts out of earshot never surfaces and gets lost — is
exactly what the periodic-redeployment layer repairs (+124 % coverage from
redeploy under this policy, vs. +19 % under the fixed schedule).

## 5. The loop, end to end

1. **Plan drops:** place N drifters using per-site mobility statistics
   measured from closed-loop simulation, maximizing expected triangulation
   coverage over 72 h.
2. **Drift and steer:** each drifter rides depth-dependent currents, its
   MPC controller re-choosing a ballast depth every 30 minutes.
3. **Listen:** envelope detector always on; classifier wakes on candidate
   contacts.
4. **Surface on events**, fix position, exfiltrate via the mesh.
5. **Smooth backward** from each fix; when ≥3 drifters heard the same
   event, triangulate it by time-difference-of-arrival.
6. **Redeploy** on a 72 h cycle, replacing nodes that wandered or lost
   confidence, re-optimized for the fleet that remains.

The README's [headline table](../README.md#headline-result) is this loop
measured over week-long simulated missions.
