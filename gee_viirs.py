"""
gee_viirs.py
------------
Queries Google Earth Engine for VIIRS active fire detections
over the Aberdare Forest AOI.
"""

import ee
import json
from datetime import datetime, timedelta, timezone
from typing import Optional


# ── Aberdare Forest bounding polygon ──────────────────────────────────────────
ABERDARE_COORDS = [
    [36.55, -0.70],
    [37.05, -0.70],
    [37.05, -0.10],
    [36.55, -0.10],
    [36.55, -0.70],
]

ABERDARE_AOI = ee.Geometry.Polygon([ABERDARE_COORDS])


def init_gee(service_account: str, key_file: str) -> None:
    """
    Authenticate with GEE using a service account (required for server-side / cloud deployment).
    
    Args:
        service_account: e.g. "viirs-fire@your-project.iam.gserviceaccount.com"
        key_file:        path to the downloaded JSON key file
    """
    credentials = ee.ServiceAccountCredentials(service_account, key_file)
    ee.Initialize(credentials)


def query_viirs(
    hours_back: int = 24,
    min_confidence: int = 50,
    aoi: Optional[ee.Geometry] = None,
) -> dict:
    """
    Query VIIRS SNPP Collection-2 active fire pixels.

    Args:
        hours_back:     how many hours back to look (default 24)
        min_confidence: minimum detection confidence 0-100 (default 50)
        aoi:            Earth Engine geometry; defaults to Aberdare polygon

    Returns:
        GeoJSON FeatureCollection with fire detections
    """
    if aoi is None:
        aoi = ABERDARE_AOI

    now_utc = datetime.now(timezone.utc)
    start   = (now_utc - timedelta(hours=hours_back)).strftime("%Y-%m-%dT%H:%M:%S")
    end     = now_utc.strftime("%Y-%m-%dT%H:%M:%S")

    collection = (
        ee.ImageCollection("FIRMS/VIIRS_SNPP_C2")
        .filterDate(start, end)
        .filterBounds(aoi)
    )

    # Convert image collection → vector points
    def image_to_points(image):
        # Mask low-confidence pixels
        confidence = image.select("confidence")
        mask = confidence.gte(min_confidence)
        masked = image.updateMask(mask)

        points = masked.sample(
            region=aoi,
            scale=375,          # VIIRS native resolution
            geometries=True,
        )

        # Tag each point with its acquisition timestamp
        acq_time = image.get("system:time_start")
        return points.map(lambda f: f.set("acq_millis", acq_time))

    fire_points = collection.map(image_to_points).flatten()

    # Select the bands we care about
    fire_points = fire_points.select(
        ["brightness", "frp", "confidence", "acq_millis"]
    )

    geojson = fire_points.getInfo()  # pulls data from GEE servers

    # Post-process: convert acq_millis → ISO string, round coordinates
    features = geojson.get("features", [])
    for feat in features:
        props = feat.get("properties", {})
        millis = props.get("acq_millis")
        if millis:
            props["acq_datetime"] = datetime.fromtimestamp(
                millis / 1000, tz=timezone.utc
            ).isoformat()
        # Round coords to 5 decimal places
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
    """Return the AOI polygon as GeoJSON (useful for map overlays)."""
    return {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [ABERDARE_COORDS],
        },
        "properties": {"name": "Aberdare Forest AOI"},
    }
