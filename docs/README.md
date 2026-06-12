# Docs index

The `docs/` tree mixes curated public-facing documents with raw working
notes. Reading order if you're new here:

## Start here

| Doc | What it is |
|---|---|
| [overview.md](overview.md) | Vision and system design — the problem, the bet, how the pieces fit |
| [status.md](status.md) | Honest current state: done / in flight / dormant / caveats |
| [how_it_works.md](how_it_works.md) | Plain-English walkthrough: the ocean structure, the estimator, the comms constraints |
| [../experiments/harmonic_prototype/FINDINGS.md](../experiments/harmonic_prototype/FINDINGS.md) | Running findings log for the maritime stack, step by step |

## Deep dives

| Doc | What it is |
|---|---|
| [findings_campaign_2026-04-30.md](findings_campaign_2026-04-30.md) | Continuous-coverage campaign: surfacing policy × redeployment matrix |
| [science_sweep_v1_morning_summary.md](science_sweep_v1_morning_summary.md) | 64-cell parameter sweep (density × policy × ranging σ × cadence) |
| [smart_redeploy_v1_status.md](smart_redeploy_v1_status.md) | Smart-redeploy implementation status |
| [maritime_buoy_design.md](maritime_buoy_design.md) | Hardware vision for the drifter node |
| [simulation_integrity.md](simulation_integrity.md) | Simulation honesty charter: truth/belief separation and its enforcement |
| [testing-philosophy.md](testing-philosophy.md) | Shape vs. substance testing; pipeline tests |
| [archive/](archive/) | The EML/LNS8 FPGA workstream's historical record (direction, status, research notes) |

## Working notes

Everything else (dated diagnostics summaries, platform surveys, design
sketches, `reference/`) is raw research material — kept in-tree for
provenance, not edited for an external reader.

`media/` holds the figures and GIFs embedded in the top-level README,
rebuilt via `uv run python dev/make_readme_media.py`.
