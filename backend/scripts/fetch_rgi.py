"""Fetch Randolph Glacier Inventory 7.0 outlines around a lake and commit them as GeoJSON.

    cd backend && .venv/bin/python -m scripts.fetch_rgi --lake south_lhonak

Source: the official GLIMS GeoServer WFS, which serves the RGI 7.0 regional files
(the NSIDC zip download needs an Earthdata login). Outlines are clipped to the
lake's ice window. If the service cannot be reached this exits non-zero; nothing
is approximated.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import shapely
from shapely.geometry import box, mapping, shape

from src.core.config import LAKES

WFS = "https://www.glims.org/geoserver/ows"
LAYER = "GLIMS:RGI2000-v7.0-G-15_south_asia_east_epsg3857"
KEEP = ("rgi_id", "glims_id", "glac_name", "src_date", "area_km2", "termlon", "termlat",
        "zmin_m", "zmax_m", "lmax_m", "term_type", "dem_source")
OUT_DIR = Path(__file__).resolve().parents[1] / "reference"

CITATION = ("RGI 7.0 Consortium, 2023. Randolph Glacier Inventory - A Dataset of Global Glacier Outlines, "
            "Version 7.0. Boulder, Colorado USA. NSIDC: National Snow and Ice Data Center. "
            "doi:10.5067/f6jmovy5navz")


def fetch(bbox: tuple[float, float, float, float]) -> dict:
    w, s, e, n = bbox
    q = urlencode({
        "service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": LAYER,
        "outputFormat": "application/json", "srsName": "EPSG:4326",
        "bbox": f"{s},{w},{n},{e},urn:ogc:def:crs:EPSG::4326",  # WFS 2.0 + EPSG:4326 = lat,lon order
    })
    with urlopen(f"{WFS}?{q}", timeout=120) as r:
        return json.loads(r.read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lake", default="south_lhonak", choices=sorted(LAKES))
    a = ap.parse_args()
    lake = LAKES[a.lake]
    bbox = lake.ice_bbox or lake.bbox
    try:
        raw = fetch(bbox)
    except Exception as exc:
        print(f"RGI fetch failed ({exc}). Not writing anything.", file=sys.stderr)
        return 1
    clip = box(*bbox)
    feats = []
    for f in raw.get("features", []):
        g = shapely.make_valid(shape(f["geometry"])).intersection(clip)
        if g.is_empty:
            continue
        props = {k: f["properties"].get(k) for k in KEEP}
        props["src_date"] = (props["src_date"] or "")[:10]
        props["clipped"] = not shape(f["geometry"]).within(clip)
        feats.append({"type": "Feature", "properties": props,
                      "geometry": mapping(shapely.set_precision(g, 1e-6))})
    if not feats:
        print("RGI query returned no outlines in the window. Not writing anything.", file=sys.stderr)
        return 1
    doc = {
        "type": "FeatureCollection",
        "metadata": {
            "dataset": "Randolph Glacier Inventory 7.0 (RGI2000-v7.0-G), region 15 South Asia East",
            "version": "7.0",
            "citation": CITATION,
            "doi": "10.5067/F6JMOVY5NAVZ",
            "distribution": f"GLIMS GeoServer WFS, layer {LAYER}",
            "accessed": datetime.now(timezone.utc).date().isoformat(),
            "clip_bbox": list(bbox),
            "note": "Each outline's source image date is in properties.src_date; RGI targets the year 2000 but "
                    "individual outlines come from different years. RGI includes debris-covered ice; the "
                    "Sentinel-2 NDSI composites in this project do not, so the two are not directly subtractable.",
        },
        "features": feats,
    }
    OUT_DIR.mkdir(exist_ok=True)
    out = OUT_DIR / f"rgi7_{lake.id}.geojson"
    out.write_text(json.dumps(doc, indent=1))
    dates = sorted({f["properties"]["src_date"] for f in feats})
    print(f"{len(feats)} outlines -> {out.relative_to(OUT_DIR.parent)} (source dates: {', '.join(dates)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
