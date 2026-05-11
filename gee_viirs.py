"""
gee_viirs.py
------------
Fire detection for Aberdare Forest using:
  1. NASA FIRMS API — VIIRS current data (primary, always fresh)
  2. Google Earth Engine FIRMS/MODIS — fallback for historical
"""

import ee
import httpx
import os
from datetime import datetime, timedelta, timezone
from typing import Optional


ABERDARE_COORDS = [
    [36.55, -0.70],
    [37.05, -0.70],
    [37.05, -0.10],
    [36.55, -0.10],
    [36.55, -0.70],
]

# Aberdare bounding box for FIRMS API
AOI_BBOX = "36.55,-0.70,37.05,-0.10"   # west,south,east,north


def init_gee(service_account: str, key_file: str) -> None:
    credentials = ee.ServiceAccountCredentials(service_account, key_file)
    ee.Initialize(credentials)


def get_aoi() -> ee.Geometry:
    return ee.Geometry.Polygon([ABERDARE_COORDS])


def query_firms_api(days_back: int = 1) -> dict:
    """
    Query NASA FIRMS API directly for VIIRS SNPP data — always current.
    Requires FIRMS_API_KEY environment variable.
    Get a free key at: https://firms.modaps.eosdis.nasa.gov/api/area/
    """
    api_key = os.environ.get("FIRMS_API_KEY", "")
    if not api_key:
        return {"features": [], "source": "firms_api", "error": "No FIRMS_API_KEY set"}

    # FIRMS area API: returns CSV of fire detections
    url = (
        f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/"
        f"{api_key}/VIIRS_SNPP_NRT/{AOI_BBOX}/{days_back}"
    )

    try:
        resp = httpx.get(url, timeout=30)
        resp.raise_for_status()
        lines = resp.text.strip().split("\n")
        if len(lines) < 2:
            return {"features": [], "source": "firms_api", "count": 0}

        headers = lines[0].split(",")
        features = []
        for line in lines[1:]:
            values = line.split(",")
            if len(values) != len(headers):
                continue
            row = dict(zip(headers, values))
            try:
                lat = float(row.get("latitude", 0))
                lon = float(row.get("longitude", 0))
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]},
                    "properties": {
                        "brightness_ti4": float(row.get("bright_ti4", 0)),
                        "brightness_ti5": float(row.get("bright_ti5", 0)),
                        "frp": float(row.get("frp", 0)) if row.get("frp") else None,
                        "confidence": row.get("confidence", "unknown"),
                        "day_night": "day" if row.get("daynight") == "D" else "night",
                        "acq_datetime": f"{row.get('acq_date','unknown')}T{row.get('acq_time','0000')}+00:00",
                        "satellite": "VIIRS_SNPP",
                        "source": "NASA_FIRMS_API",
                    }
                })
            except (ValueError, KeyError):
                continue

        return {
            "type": "FeatureCollection",
            "count": len(features),
            "features": features,
            "source": "NASA_FIRMS_API_VIIRS",
        }
    except Exception as e:
        return {"features": [], "source": "firms_api", "error": str(e)}


def query_viirs(
    hours_back: int = 24,
    min_confidence: int = 50,
    aoi: Optional[ee.Geometry] = None,
) -> dict:
    """
    Primary: NASA FIRMS API (always current VIIRS data)
    Fallback: GEE FIRMS MODIS (live but 1km resolution)
    """
    days_back = max(1, hours_back // 24)
    now_utc = datetime.now(timezone.utc)
    start = (now_utc - timedelta(hours=hours_back)).strftime("%Y-%m-%dT%H:%M:%S")
    end = now_utc.strftime("%Y-%m-%dT%H:%M:%S")

    # ── Try FIRMS API first ───────────────────────────────────────────────────
    firms_result = query_firms_api(days_back=days_back)
    features = firms_result.get("features", [])

    # ── Also query GEE MODIS FIRMS (still live, 1km) ─────────────────────────
    if aoi is None:
        aoi = get_aoi()

    try:
        modis = (
            ee.ImageCollection("FIRMS")
            .filterDate(start, end)
            .filterBounds(aoi)
        )

        def image_to_points(image):
            points = image.select("T21").sample(
                region=aoi, scale=1000, geometries=True
            )
            acq_time = image.get("system:time_start")
            return points.map(lambda f: f.set("acq_millis", acq_time))

        modis_points = modis.map(image_to_points).flatten()
        geojson = modis_points.getInfo()

        for feat in geojson.get("features", []):
            props = feat.get("properties", {})
            millis = props.get("acq_millis")
            if millis:
                props["acq_datetime"] = datetime.fromtimestamp(
                    millis / 1000, tz=timezone.utc
                ).isoformat()
            props["satellite"] = "MODIS"
            props["source"] = "GEE_FIRMS_MODIS"
            props["brightness_ti4"] = props.pop("T21", None)
            coords = feat.get("geometry", {}).get("coordinates", [])
            if coords:
                feat["geometry"]["coordinates"] = [round(c, 5) for c in coords]
            features.append(feat)

    except Exception as e:
        pass  # GEE failure is non-fatal if FIRMS API returned data

    return {
        "type": "FeatureCollection",
        "query_window": {"start": start, "end": end, "hours_back": hours_back},
        "count": len(features),
        "features": features,
    }


def get_aberdare_boundary() -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [ABERDARE_COORDS]},
        "properties": {"name": "Aberdare Forest AOI"},
    }