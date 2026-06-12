"""Cached, chunked SalishSeaCast ERDDAP fetcher.

Goals:
- Fetch a bbox-shaped spatial region in one ERDDAP request per time chunk
  (not one cell at a time — that's days of wall-clock for any real bbox).
- Don't re-download identical data: deterministic cache keys on disk.
- Resumable: interrupted fetches resume from the last cached chunk.
- Polite throttle between chunks.
- Support both bbox fetches (the primary case) AND single-cell fetches
  (useful for diagnostic work).

Chunking strategy: monthly along time, full spatial bbox in one request.
At 500 m native resolution, a 40 km × 40 km bbox is ~80×80 = 6400 cells;
one month × 40 depths × 6400 cells × 4 bytes = ~75 MB raw per variable.
ERDDAP griddap handles that in a single request; cached as one NetCDF
per variable per (bbox, month).

Cache layout:
    cache/
        <hash>.nc     — one NetCDF per (dataset_id, bbox_grid, year_month)

Cache key includes the bbox as integer grid-index bounds (not float
lat/lon) so the hash is deterministic across float-precision variants.

Concurrency note (confirmed empirically 2026-04): UBC SalishSeaCast
ERDDAP is strictly 1-concurrent-request-per-client. Parallel fetches
fail with HTTP 408 ("Timeout waiting for your other requests to
process. Please make just one request at a time."). Any multi-bbox /
multi-year fill-in must be serial or throttled to one-at-a-time. The
monthly cache is idempotent so resumability handles this — but do not
attempt to speed up fetches via multi-process / asyncio parallelism
against this endpoint.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np  # type: ignore[import-not-found]
import xarray as xr  # type: ignore[import-not-found]


ERDDAP_BASE = "https://salishsea.eos.ubc.ca/erddap/griddap"

U_DATASET = "ubcSSg3DuGridFields1hV21-11"
V_DATASET = "ubcSSg3DvGridFields1hV21-11"
# Tracers (temperature, salinity) live on the same (time, depth,
# gridY, gridX) structure as the velocity datasets. Verified against
# the live ERDDAP catalog 2026-04-24. Carries:
#   `salinity`    — reference salinity (g kg⁻¹)
#   `temperature` — conservative temperature (°C)
#   `sigma_theta` — potential density (kg m⁻³, not fetched here)
PHYSICS_DATASET = "ubcSSg3DPhysicsFields1hV21-11"
BATHY_DATASET = "ubcSSnBathymetryV21-08"

POLITE_DELAY_SEC = 1.0

_CACHE_DIR = Path(__file__).parent / "cache"


@dataclass(frozen=True)
class GridCell:
    gy: int
    gx: int
    lat_deg: float
    lon_deg: float
    bathymetry_m: float


@dataclass(frozen=True)
class GridBBox:
    """Integer grid-index rectangle on the SalishSeaCast grid."""
    gy_min: int
    gy_max: int  # inclusive
    gx_min: int
    gx_max: int  # inclusive

    @property
    def n_cells(self) -> int:
        return (self.gy_max - self.gy_min + 1) * (self.gx_max - self.gx_min + 1)

    def key(self) -> str:
        return f"gy={self.gy_min}-{self.gy_max}_gx={self.gx_min}-{self.gx_max}"


def _cache_hash(*parts: str) -> str:
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _cache_path(dataset_id: str, bbox_key: str, year_month: str) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    h = _cache_hash(dataset_id, bbox_key, year_month)
    return _CACHE_DIR / f"{h}.nc"


def _month_chunks(year_start: int, year_end: int) -> list[str]:
    out: list[str] = []
    for y in range(year_start, year_end + 1):
        for m in range(1, 13):
            out.append(f"{y:04d}-{m:02d}")
    return out


def _month_bounds_iso(year_month: str) -> tuple[str, str]:
    y, m = year_month.split("-")
    y_int, m_int = int(y), int(m)
    start = f"{y_int:04d}-{m_int:02d}-01T00:30"
    if m_int == 12:
        next_y, next_m = y_int + 1, 1
    else:
        next_y, next_m = y_int, m_int + 1
    end = f"{next_y:04d}-{next_m:02d}-01T00:30"
    return start, end


# ---------------------------------------------------------------------------
# Grid-location helpers
# ---------------------------------------------------------------------------

_bathy_cache: dict[str, xr.Dataset] = {}


def _load_bathy() -> xr.Dataset:
    """Cache the bathymetry dataset on disk + in-process (~2 MB).

    First caller fetches from ERDDAP and writes to a local NetCDF;
    every subsequent caller (including parallel processes) reads the
    local file. Prevents the 429-rate-limit storm when many processes
    start simultaneously.
    """
    if "bathy" in _bathy_cache:
        return _bathy_cache["bathy"]
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    local = _CACHE_DIR / f"bathy_{BATHY_DATASET}.nc"
    if not local.exists():
        url = f"{ERDDAP_BASE}/{BATHY_DATASET}"
        remote = xr.open_dataset(url)
        remote.load()
        # ERDDAP attrs have case-insensitive duplicates that trip NetCDF4
        # writes ("String match to name in use"). Strip everything
        # non-essential; we only need the data arrays themselves.
        remote.attrs.clear()
        for name in list(remote.variables):
            remote[name].attrs.clear()
        remote.to_netcdf(local)
        remote.close()
    _bathy_cache["bathy"] = xr.open_dataset(local)
    return _bathy_cache["bathy"]


def find_grid_cell(lat_deg: float, lon_deg: float) -> GridCell:
    """Nearest SalishSeaCast cell to (lat, lon), plus its bathymetry."""
    ds = _load_bathy()
    lats = ds["latitude"].values
    lons = ds["longitude"].values
    bathy = ds["bathymetry"].values

    cos_lat = np.cos(np.deg2rad(lat_deg))
    dlat_m = (lats - lat_deg) * 111_320.0
    dlon_m = (lons - lon_deg) * 111_320.0 * cos_lat
    dist_m = np.sqrt(dlat_m**2 + dlon_m**2)
    dist_m = np.where(np.isnan(dist_m), np.inf, dist_m)
    idx = int(np.argmin(dist_m))
    gy, gx = np.unravel_index(idx, dist_m.shape)
    gy, gx = int(gy), int(gx)

    return GridCell(
        gy=gy,
        gx=gx,
        lat_deg=float(lats[gy, gx]),
        lon_deg=float(lons[gy, gx]),
        bathymetry_m=float(bathy[gy, gx]),
    )


def bbox_from_latlon(
    lat_min: float, lat_max: float,
    lon_min: float, lon_max: float,
) -> GridBBox:
    """Convert a lat/lon bbox to an integer grid-index bbox on the SalishSeaCast grid."""
    ds = _load_bathy()
    lats = ds["latitude"].values  # (gridY, gridX)
    lons = ds["longitude"].values

    mask = (
        (lats >= lat_min) & (lats <= lat_max)
        & (lons >= lon_min) & (lons <= lon_max)
    )
    if not mask.any():
        raise ValueError(
            f"lat/lon bbox {lat_min}, {lat_max}, {lon_min}, {lon_max} "
            f"did not intersect the SalishSeaCast grid."
        )

    ys, xs = np.where(mask)
    return GridBBox(
        gy_min=int(ys.min()),
        gy_max=int(ys.max()),
        gx_min=int(xs.min()),
        gx_max=int(xs.max()),
    )


def bbox_latlon_arrays(bbox: GridBBox) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (lats, lons, bathymetry) as (gy_span, gx_span) arrays for this bbox."""
    ds = _load_bathy()
    sl_y = slice(bbox.gy_min, bbox.gy_max + 1)
    sl_x = slice(bbox.gx_min, bbox.gx_max + 1)
    return (
        ds["latitude"].values[sl_y, sl_x].copy(),
        ds["longitude"].values[sl_y, sl_x].copy(),
        ds["bathymetry"].values[sl_y, sl_x].copy(),
    )


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def _fetch_one_variable_bbox_month(
    dataset_id: str,
    var_name: str,
    bbox: GridBBox,
    year_month: str,
) -> xr.DataArray:
    """Fetch one month × one variable × full bbox in one ERDDAP request."""
    url = f"{ERDDAP_BASE}/{dataset_id}"
    ds = xr.open_dataset(url)
    start, end = _month_bounds_iso(year_month)
    sub = (
        ds[var_name]
        .sel(time=slice(start, end))
        .isel(
            gridY=slice(bbox.gy_min, bbox.gy_max + 1),
            gridX=slice(bbox.gx_min, bbox.gx_max + 1),
        )
    )
    sub.values  # force materialization
    return sub


def _fetch_one_bbox_month(bbox: GridBBox, year_month: str) -> xr.Dataset:
    """Fetch velocities (u, v) for one month. Tracers (salinity,
    temperature) go through `_fetch_one_bbox_month_tracers` to a
    separate cache keyed off PHYSICS_DATASET so velocity caches are
    not invalidated when tracer fetching is added."""
    u = _fetch_one_variable_bbox_month(U_DATASET, "uVelocity", bbox, year_month)
    v = _fetch_one_variable_bbox_month(V_DATASET, "vVelocity", bbox, year_month)
    return xr.Dataset(
        {"u_ms": u, "v_ms": v},
        attrs={
            "gy_min": bbox.gy_min,
            "gy_max": bbox.gy_max,
            "gx_min": bbox.gx_min,
            "gx_max": bbox.gx_max,
            "year_month": year_month,
        },
    )


def _fetch_one_bbox_month_tracers(
    bbox: GridBBox, year_month: str,
) -> xr.Dataset:
    """Fetch tracers (salinity, temperature) for one month. Stored in
    a separate NetCDF cache keyed off `PHYSICS_DATASET`; velocity
    caches from `_fetch_one_bbox_month` are untouched."""
    s = _fetch_one_variable_bbox_month(
        PHYSICS_DATASET, "salinity", bbox, year_month,
    )
    t = _fetch_one_variable_bbox_month(
        PHYSICS_DATASET, "temperature", bbox, year_month,
    )
    return xr.Dataset(
        {"sal_psu": s, "temp_c": t},
        attrs={
            "gy_min": bbox.gy_min,
            "gy_max": bbox.gy_max,
            "gx_min": bbox.gx_min,
            "gx_max": bbox.gx_max,
            "year_month": year_month,
        },
    )


def fetch_bbox(
    bbox: GridBBox,
    year_start: int,
    year_end: int,
    *,
    verbose: bool = True,
) -> xr.Dataset:
    """Fetch hourly u, v over a spatial bbox for [year_start, year_end].

    Uses the monthly cache per bbox. Concatenates along time. Large bboxes
    (thousands of cells) stay manageable because the per-request size is
    one month × bbox area × 40 depths.

    Returns a Dataset with dims (time, depth, gridY, gridX) where gridY /
    gridX are local-to-bbox indices; `gridY_global` and `gridX_global`
    coords record the SalishSeaCast grid indices.
    """
    return fetch_bbox_months(bbox, _month_chunks(year_start, year_end), verbose=verbose)


def fetch_bbox_months(
    bbox: GridBBox,
    year_months: Sequence[str],
    *,
    verbose: bool = True,
    include_tracers: bool = False,
) -> xr.Dataset:
    """Fetch hourly u, v over a spatial bbox for an explicit list of
    months. When `include_tracers=True`, also fetches salinity and
    temperature from `PHYSICS_DATASET` and merges into the returned
    dataset (`sal_psu`, `temp_c`). Tracer caches are keyed off
    `PHYSICS_DATASET` so velocity-only caches are NOT invalidated when
    adding tracer fetching.
    """
    cached_paths: list[Path] = []
    tracer_paths: list[Path] = []
    n_hits, n_fetches, dl_bytes = 0, 0, 0

    for ym in year_months:
        # Velocity cache (u, v).
        path = _cache_path(U_DATASET, bbox.key(), ym)
        if path.exists():
            cached_paths.append(path)
            n_hits += 1
        else:
            if verbose:
                print(f"[fetch] uv bbox={bbox.key()} month={ym} → {path.name}  "
                      f"({bbox.n_cells} cells)")
            t0 = time.time()
            ds_month = _fetch_one_bbox_month(bbox, ym)
            ds_month.to_netcdf(path)
            dt = time.time() - t0
            dl_bytes += path.stat().st_size
            if verbose:
                print(f"[fetch]   done in {dt:.1f}s "
                      f"({path.stat().st_size / 1024 / 1024:.1f} MiB on disk)")
            cached_paths.append(path)
            n_fetches += 1
            if ym != year_months[-1]:
                time.sleep(POLITE_DELAY_SEC)

        # Tracer cache (salinity, temperature) — separate hash.
        if include_tracers:
            tpath = _cache_path(PHYSICS_DATASET, bbox.key(), ym)
            if tpath.exists():
                tracer_paths.append(tpath)
                n_hits += 1
            else:
                if verbose:
                    print(f"[fetch] T/S bbox={bbox.key()} month={ym} → "
                          f"{tpath.name}")
                t0 = time.time()
                ds_tracer = _fetch_one_bbox_month_tracers(bbox, ym)
                ds_tracer.to_netcdf(tpath)
                dt = time.time() - t0
                dl_bytes += tpath.stat().st_size
                if verbose:
                    print(f"[fetch]   done in {dt:.1f}s "
                          f"({tpath.stat().st_size / 1024 / 1024:.1f} MiB on disk)")
                tracer_paths.append(tpath)
                n_fetches += 1
                if ym != year_months[-1]:
                    time.sleep(POLITE_DELAY_SEC)

    if verbose:
        print(f"[fetch] total: {n_hits} cache hits, {n_fetches} network fetches, "
              f"{dl_bytes / 1024 / 1024:.1f} MiB downloaded this run")

    parts = [xr.open_dataset(p) for p in cached_paths]
    combined = xr.concat(parts, dim="time")
    combined = combined.drop_duplicates(dim="time", keep="first").sortby("time")

    if include_tracers:
        tracer_parts = [xr.open_dataset(p) for p in tracer_paths]
        tracer_combined = xr.concat(tracer_parts, dim="time")
        tracer_combined = (tracer_combined
                            .drop_duplicates(dim="time", keep="first")
                            .sortby("time"))
        # Merge tracers into the velocity dataset on matching coords.
        combined = xr.merge([combined, tracer_combined], compat="override")

    return combined


# ---------------------------------------------------------------------------
# Single-cell convenience (diagnostic use)
# ---------------------------------------------------------------------------

def fetch_cell_column(
    gy: int,
    gx: int,
    year_start: int,
    year_end: int,
    *,
    verbose: bool = True,
) -> xr.Dataset:
    """Fetch at one cell (gy, gx). Thin wrapper over `fetch_bbox` with a 1×1 bbox."""
    bbox = GridBBox(gy_min=gy, gy_max=gy, gx_min=gx, gx_max=gx)
    ds = fetch_bbox(bbox, year_start, year_end, verbose=verbose)
    # Squeeze singleton spatial dims for convenience.
    return ds.isel(gridY=0, gridX=0)


def cache_size_bytes() -> int:
    if not _CACHE_DIR.exists():
        return 0
    return sum(f.stat().st_size for f in _CACHE_DIR.glob("*.nc"))


if __name__ == "__main__":
    # Smoke test: small bbox, one month, print summary.
    print("=== salishseacast_cache smoke test ===")
    test_bbox = bbox_from_latlon(49.20, 49.25, -123.60, -123.55)
    print(f"bbox: {test_bbox}  (cells={test_bbox.n_cells})")

    t0 = time.time()
    ds = fetch_bbox_months(test_bbox, ["2023-06"])
    print(f"fetched in {time.time()-t0:.1f}s")
    print(f"dims: {dict(ds.sizes)}")
    print(f"cache size: {cache_size_bytes() / 1024 / 1024:.2f} MiB")
