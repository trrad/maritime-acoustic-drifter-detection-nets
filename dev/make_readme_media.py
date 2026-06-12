"""Assemble README media (animated GIFs) from footprint frame sequences.

Builds docs/media/ assets from the per-config footprint frame PNGs written by
the fleet sweep. Re-run after regenerating sweep figures to refresh the GIFs.

Usage:
    uv run python dev/make_readme_media.py
"""

from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parent.parent
FRAMES_ROOT = (
    REPO
    / "experiments/harmonic_prototype/figures/sweep_runs"
    / "20260430_campaign_combined/per_config"
)
MEDIA = REPO / "docs/media"

HERO_CONFIG = "D6_redep72h__post_event_30m_12h__footprint_frames"
BASELINE_CONFIG = "D6_norep__fixed_6h__footprint_frames"

FRAME_MS = 350
LAST_FRAME_MS = 1500
SCALE = 0.8


def load_frames(config: str) -> list[Image.Image]:
    frame_dir = FRAMES_ROOT / config
    paths = sorted(frame_dir.glob("footprint_t*.png"))
    if not paths:
        raise FileNotFoundError(f"no footprint frames in {frame_dir}")
    frames = []
    for p in paths:
        im = Image.open(p).convert("RGB")
        if SCALE != 1.0:
            im = im.resize(
                (round(im.width * SCALE), round(im.height * SCALE)),
                Image.Resampling.LANCZOS,
            )
        frames.append(im)
    return frames


def save_gif(frames: list[Image.Image], out: Path) -> None:
    durations = [FRAME_MS] * (len(frames) - 1) + [LAST_FRAME_MS]
    quantized = [f.quantize(colors=256, method=Image.Quantize.MEDIANCUT) for f in frames]
    quantized[0].save(
        out,
        save_all=True,
        append_images=quantized[1:],
        duration=durations,
        loop=0,
        optimize=True,
    )
    print(f"wrote {out.relative_to(REPO)} ({len(frames)} frames, {out.stat().st_size / 1e6:.1f} MB)")


def hstack(a: Image.Image, b: Image.Image) -> Image.Image:
    h = max(a.height, b.height)
    canvas = Image.new("RGB", (a.width + b.width, h), "white")
    canvas.paste(a, (0, (h - a.height) // 2))
    canvas.paste(b, (a.width, (h - b.height) // 2))
    return canvas


def main() -> None:
    MEDIA.mkdir(parents=True, exist_ok=True)

    hero = load_frames(HERO_CONFIG)
    baseline = load_frames(BASELINE_CONFIG)
    if len(hero) != len(baseline):
        raise ValueError(
            f"frame count mismatch: {HERO_CONFIG}={len(hero)} "
            f"{BASELINE_CONFIG}={len(baseline)}"
        )

    save_gif(hero, MEDIA / "coverage_smart_redeploy.gif")
    save_gif(
        [hstack(b, h) for b, h in zip(baseline, hero)],
        MEDIA / "coverage_static_vs_smart_redeploy.gif",
    )


if __name__ == "__main__":
    main()
