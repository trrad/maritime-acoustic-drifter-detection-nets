#!/usr/bin/env python
"""Screenshot helper for the maritime dashboard — for Claude's own use.

Captures full-window PNGs of the dashboard at specific URLs / scrub
positions. Used to pixel-inspect rendering during exploration; not
part of any production path.

Usage:
  uv run python dev/dash_screenshot.py <url> <out.png> [tick=N]

Examples:
  uv run python dev/dash_screenshot.py http://localhost:8912/ /tmp/d0.png
  uv run python dev/dash_screenshot.py 'http://localhost:8912/?run=12h_75km_anchors_OOR' /tmp/d1.png tick=900
"""

from __future__ import annotations

import asyncio
import sys

from playwright.async_api import async_playwright  # type: ignore[import-not-found]


async def shoot(url: str, out_path: str, tick: int | None) -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            page = await browser.new_page(viewport={"width": 1600, "height": 900})
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await page.wait_for_function(
                'typeof DATA !== "undefined" && DATA.truth_ticks.length > 0',
                timeout=15000,
            )
            if tick is not None:
                await page.evaluate(
                    "(t) => { window.setTick(t); }"
                    if False
                    else (
                        "(t) => { "
                        " const s = document.getElementById('timeSlider'); "
                        " s.value = String(t); "
                        " s.dispatchEvent(new Event('input', {bubbles:true})); "
                        "}"
                    ),
                    tick,
                )
                # Allow render() to finish.
                await page.wait_for_timeout(150)
            top_h = await page.evaluate(
                "document.getElementById('top-bar').offsetHeight"
            )
            n_ticks = await page.evaluate("DATA.truth_ticks.length")
            cur = await page.evaluate(
                "document.getElementById('runSelector').value"
            )
            print(
                f"top-bar={top_h}px run={cur} ticks={n_ticks} -> {out_path}",
                file=sys.stderr,
            )
            await page.screenshot(path=out_path, full_page=False)
        finally:
            await browser.close()


def _parse_args(argv: list[str]) -> tuple[str, str, int | None]:
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    url, out = argv[1], argv[2]
    tick: int | None = None
    for arg in argv[3:]:
        if arg.startswith("tick="):
            tick = int(arg.split("=", 1)[1])
    return url, out, tick


def main() -> int:
    url, out, tick = _parse_args(sys.argv)
    asyncio.run(shoot(url, out, tick))
    return 0


if __name__ == "__main__":
    sys.exit(main())
