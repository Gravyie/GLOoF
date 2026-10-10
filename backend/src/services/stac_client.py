"""AWS Earth Search (Element 84) STAC queries for Sentinel-2 L2A.

This module only *finds* scenes and resolves the three assets we need.
It never touches pixels - that is geospatial.py's job.

Earth Search v1 asset keys: ``green`` (B03), ``nir`` (B08), ``scl`` (scene
classification). Older/other catalogs use band IDs, so aliases are supported.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from pystac import Asset, Item
from pystac_client import Client

from src.core.config import Settings

log = logging.getLogger(__name__)

_ALIASES = {
    "green": ("green", "B03"),
    "nir": ("nir", "B08"),
    "scl": ("scl", "SCL"),
    "swir": ("swir16", "B11"),  # shortwave infrared, 20 m
}


class StacError(RuntimeError):
    """The catalog could not be queried or returned an unusable item."""


@dataclass(frozen=True)
class BandAsset:
    href: str
    scale: float  # reflectance = DN * scale + offset
    offset: float


@dataclass(frozen=True)
class SceneRef:
    item_id: str
    acquired: datetime
    tile_cloud_cover: float | None
    green: BandAsset
    nir: BandAsset
    scl_href: str | None
    swir: BandAsset | None = None


def _find_asset(item: Item, key: str) -> Asset | None:
    for name in _ALIASES[key]:
        if name in item.assets:
            return item.assets[name]
    return None


def _band_asset(item: Item, asset: Asset) -> BandAsset:
    """Read scale/offset from the STAC ``raster:bands`` metadata.

    This matters: since processing baseline 04.00 (Jan 2022) L2A DNs carry a
    -1000 offset. NDWI is a ratio, so ignoring the offset biases it.
    """
    bands = asset.extra_fields.get("raster:bands") or [{}]
    meta = bands[0]
    scale = meta.get("scale")
    offset = meta.get("offset")
    if scale is None or offset is None:
        baseline = str(item.properties.get("s2:processing_baseline", "0"))
        try:
            new_baseline = float(baseline) >= 4.0
        except ValueError:
            new_baseline = False
        scale = 1e-4 if scale is None else scale
        offset = (-0.1 if new_baseline else 0.0) if offset is None else offset
    return BandAsset(href=asset.href, scale=float(scale), offset=float(offset))


def _to_scene(item: Item) -> SceneRef:
    green = _find_asset(item, "green")
    nir = _find_asset(item, "nir")
    if green is None or nir is None:
        raise StacError(f"{item.id}: missing green/nir assets")
    scl = _find_asset(item, "scl")
    swir = _find_asset(item, "swir")
    acquired = item.datetime
    if acquired is None:
        raise StacError(f"{item.id}: item has no datetime")
    return SceneRef(
        item_id=item.id,
        acquired=acquired,
        tile_cloud_cover=item.properties.get("eo:cloud_cover"),
        green=_band_asset(item, green),
        nir=_band_asset(item, nir),
        scl_href=scl.href if scl else None,
        swir=_band_asset(item, swir) if swir else None,
    )


class StacSceneFinder:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Client | None = None

    def _get_client(self) -> Client:
        if self._client is None:
            try:
                self._client = Client.open(self._settings.stac_url)
            except Exception as exc:  # network, DNS, bad JSON...
                raise StacError(f"cannot open STAC catalog: {exc}") from exc
        return self._client

    def find_scenes(
        self,
        bbox: tuple[float, float, float, float],
        *,
        limit: int,
        days: int | None = None,
    ) -> list[SceneRef]:
        """Newest-first Sentinel-2 L2A scenes intersecting ``bbox``."""
        s = self._settings
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days or s.search_window_days)
        try:
            search = self._get_client().search(
                collections=[s.stac_collection],
                bbox=list(bbox),
                datetime=f"{start.isoformat()}/{end.isoformat()}",
                query={"eo:cloud_cover": {"lt": s.max_tile_cloud_cover}},
                sortby="-properties.datetime",
                max_items=limit,
            )
            items = list(search.items())
        except StacError:
            raise
        except Exception as exc:
            raise StacError(f"STAC search failed: {exc}") from exc

        scenes: list[SceneRef] = []
        for item in items:
            try:
                scenes.append(_to_scene(item))
            except StacError as exc:
                log.warning("skipping item: %s", exc)
        scenes.sort(key=lambda sc: sc.acquired, reverse=True)
        return scenes
