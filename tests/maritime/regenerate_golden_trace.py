#!/usr/bin/env python
"""Regenerate the golden trace fixture for maritime scenario tests.

Pins --created-at to a fixed UTC string so the fixture is byte-identical
across runs. Bbox is a ~330 m square so all 10 nodes stay inside the 10 km
LoRa range; duration/dt match design D6 (15 minutes at 1 Hz = 900 ticks).
"""

import subprocess
import sys
from pathlib import Path

FIXTURE_DIR = Path(__file__).parent / "golden_trace"
FIXTURE_PATH = FIXTURE_DIR / "m1_tiny.jsonl"
FIXED_CREATED_AT = "2026-04-22T00:00:00+00:00"


def main():
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "rtl.vectors.maritime.gen_maritime_scenario",
        "--seed", "42",
        "--bbox", "48.6,-123.5,48.603,-123.497",
        "--duration-hours", "0.25",
        "--dt-sec", "1.0",
        "--nodes", "10",
        "--out", str(FIXTURE_PATH),
        "--created-at", FIXED_CREATED_AT,
    ]
    subprocess.run(cmd, check=True)
    size_mb = FIXTURE_PATH.stat().st_size / (1024 * 1024)
    print(f"Golden trace regenerated: {FIXTURE_PATH} ({size_mb:.2f} MB)")


if __name__ == "__main__":
    main()
