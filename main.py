import os
import base64
import json
import time
import logging
from datetime import datetime, timezone

import ee
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from gee_viirs import init_gee, query_viirs, get_aberdare_boundary

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("fire-api")

app = FastAPI(
    title="Aberdare Fire Detection API",
    description="Real-time VIIRS active fire detections for Aberdare Forest, Kenya",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

_cache: dict = {}
CACHE_TTL_SECONDS = 30 * 60


def cached_query(hours_back: int, min_confidence: int) -> dict:
    key = f"{hours_back}:{min_confidence}"
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < CACHE_TTL_SECONDS:
        log.info("Cache hit for key=%s", key)
        return entry["data"]
    log.info("Cache miss – querying GEE (hours_back=%d)", hours_back)
    data = query_viirs(hours_back=hours_back, min_confidence=min_confidence)
    _cache[key] = {"ts": time.time(), "data": data}
    return data


@app.on_event("startup")
def startup():
    service_account = os.environ["GEE_SERVICE_ACCOUNT"]
    key_b64 = os.environ["GEE_KEY_JSON"]

    # ── Decode base64 → JSON string → write to temp file ──────────────────
    try:
        key_json = base64.b64decode(key_b64).decode("utf-8")
        # Validate it's real JSON before writing
        json.loads(key_json)
    except Exception as e:
        log.error("Failed to decode GEE_KEY_JSON: %s", e)
        raise RuntimeError("GEE_KEY_JSON is not valid base64-encoded JSON") from e

    key_path = "/tmp/gee_key.json"
    with open(key_path, "w") as f:
        f.write(key_json)

    init_gee(service_account, key_path)
    log.info("GEE initialised with service account: %s", service_account)


@app.get("/health")
def health():
    return {"status": "ok", "utc": datetime.now(timezone.utc).isoformat()}


@app.get("/fire/latest")
def fire_latest(
    hours: int = Query(default=24, ge=1, le=72),
    min_confidence: int = Query(default=50, ge=0, le=100),
):
    try:
        data = cached_query(hours_back=hours, min_confidence=min_confidence)
        return JSONResponse(content=data)
    except Exception as exc:
        log.exception("GEE query failed")
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/fire/history")
def fire_history(
    days: int = Query(default=3, ge=1, le=7),
    min_confidence: int = Query(default=50, ge=0, le=100),
):
    try:
        data = cached_query(hours_back=days * 24, min_confidence=min_confidence)
        return JSONResponse(content=data)
    except Exception as exc:
        log.exception("GEE history query failed")
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/fire/boundary")
def fire_boundary():
    return JSONResponse(content=get_aberdare_boundary())


@app.get("/")
def root():
    return {
        "service": "Aberdare Fire Detection API",
        "docs": "/docs",
        "endpoints": ["/fire/latest", "/fire/history", "/fire/boundary", "/health"],
    }

@app.get("/fire/debug")
def fire_debug():
    """
    Diagnostic endpoint — shows env var status, raw FIRMS API response,
    and GEE collection size. Never expose in production.
    """
    import os, httpx

    firms_key = os.environ.get("FIRMS_API_KEY", "")
    gee_account = os.environ.get("GEE_SERVICE_ACCOUNT", "")

    result = {
        "env": {
            "FIRMS_API_KEY_set": bool(firms_key),
            "FIRMS_API_KEY_length": len(firms_key),
            "GEE_SERVICE_ACCOUNT": gee_account,
        },
        "firms_api": None,
        "gee_modis": None,
    }

    # ── Test FIRMS API raw ────────────────────────────────────────────────────
    if firms_key:
        try:
            url = (
                f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/"
                f"{firms_key}/VIIRS_SNPP_NRT/36.55,-0.70,37.05,-0.10/5"
            )
            resp = httpx.get(url, timeout=30)
            result["firms_api"] = {
                "status_code": resp.status_code,
                "raw_first_500_chars": resp.text[:500],
                "line_count": len(resp.text.strip().split("\n")),
            }
        except Exception as e:
            result["firms_api"] = {"error": str(e)}
    else:
        result["firms_api"] = {"error": "FIRMS_API_KEY not set in environment"}

    # ── Test GEE MODIS collection size ────────────────────────────────────────
    try:
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        start = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
        end = now.strftime("%Y-%m-%dT%H:%M:%S")
        aoi = ee.Geometry.Polygon([[
            [36.55, -0.70], [37.05, -0.70],
            [37.05, -0.10], [36.55, -0.10], [36.55, -0.70]
        ]])
        count = (
            ee.ImageCollection("FIRMS")
            .filterDate(start, end)
            .filterBounds(aoi)
            .size()
            .getInfo()
        )
        result["gee_modis"] = {"image_count_last_7_days": count}
    except Exception as e:
        result["gee_modis"] = {"error": str(e)}

    return JSONResponse(content=result)