"""
gee_viirs.py
------------
Queries Google Earth Engine for VIIRS active fire detections
over the Aberdare Forest AOI.
"""

import ee
from datetime import datetime, timedelta, timezone
from typing import Optional


# ── Aberdare Forest bounding polygon (plain Python list — no ee calls here) ──
ABERDARE_COORDS = [
    [36.55, -0.70],
    [37.05, -0.70],
    [37.05, -0.10],
    [36.55, -0.10],
    [36.55, -0.70],
]


def init_gee(service_account: str, key_file: str) -> None:
    """Authenticate with GEE using a service account."""
    credentials = ee.ServiceAccountCredentials(service_account, key_file)
    ee.Initialize(credentials)


def get_aoi() -> ee.Geometry:
    """Build the AOI geometry — only called after ee.Initialize()."""
    return ee.Geometry.Polygon([ABERDARE_COORDS])


def query_viirs(
    hours_back: int = 24,
    min_confidence: int = 50,
    aoi: Optional[ee.Geometry] = None,
) -> dict:
    """Query VIIRS SNPP Collection-2 active fire pixels."""
    if aoi is None:
        aoi = get_aoi()   # ← created here, after GEE is initialized

    now_utc = datetime.now(timezone.utc)
    start   = (now_utc - timedelta(hours=hours_back)).strftime("%Y-%m-%dT%H:%M:%S")
    end     = now_utc.strftime("%Y-%m-%dT%H:%M:%S")

    collection = (
        ee.ImageCollection("FIRMS/VIIRS_SNPP_C2")
        .filterDate(start, end)
        .filterBounds(aoi)
    )

    def image_to_points(image):
        confidence = image.select("confidence")
        mask = confidence.gte(min_confidence)
        masked = image.updateMask(mask)
        points = masked.sample(
            region=aoi,
            scale=375,
            geometries=True,
        )
        acq_time = image.get("system:time_start")
        return points.map(lambda f: f.set("acq_millis", acq_time))

    fire_points = collection.map(image_to_points).flatten()
    fire_points = fire_points.select(
        ["brightness", "frp", "confidence", "acq_millis"]
    )

    geojson = fire_points.getInfo()

    features = geojson.get("features", [])
    for feat in features:
        props = feat.get("properties", {})
        millis = props.get("acq_millis")
        if millis:
            props["acq_datetime"] = datetime.fromtimestamp(
                millis / 1000, tz=timezone.utc
            ).isoformat()
        coords = feat.get("geometry", {}).get("coordinates", [])
        if coords:
            feat["geometry"]["coordinates"] = [round(c, 5) for c in coords]

    return {
        "type": "FeatureCollection",
        "query_window": {"start": start, "end": end, "hours_back": hours_back},
        "count": len(features),
        "features": features,
    }


def get_aberdare_boundary() -> dict:
    """Return the AOI polygon as GeoJSON."""
    return {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [ABERDARE_COORDS],
        },
        "properties": {"name": "Aberdare Forest AOI"},
    }