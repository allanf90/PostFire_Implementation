"""
main.py
-------
FastAPI service exposing VIIRS fire detection data for Aberdare Forest.

Endpoints:
  GET /fire/latest          – detections from the last 24 h (cached 30 min)
  GET /fire/history?days=N  – up to 7 days of detections
  GET /fire/boundary        – AOI GeoJSON polygon
  GET /health               – liveness probe for Cloud Run / Railway
"""

import os
import json
import time
import logging
from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional

import ee
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from gee_viirs import init_gee, query_viirs, get_aberdare_boundary

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("fire-api")

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Aberdare Fire Detection API",
    description="Real-time VIIRS active fire detections for Aberdare Forest, Kenya",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten in production
    allow_methods=["GET"],
    allow_headers=["*"],
)

# ── Simple in-memory cache ────────────────────────────────────────────────────
_cache: dict = {}
CACHE_TTL_SECONDS = 30 * 60   # 30 minutes (VIIRS updates ~twice/day)


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


# ── Startup: initialise GEE ───────────────────────────────────────────────────
@app.on_event("startup")
def startup():
    service_account = os.environ["GEE_SERVICE_ACCOUNT"]   # set in deployment env
    key_json        = os.environ["GEE_KEY_JSON"]           # full JSON key as string

    # Write key to a temp file (GEE SDK needs a file path)
    key_path = "/tmp/gee_key.json"
    with open(key_path, "w") as f:
        f.write(key_json)

    init_gee(service_account, key_path)
    log.info("GEE initialised with service account: %s", service_account)


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "utc": datetime.now(timezone.utc).isoformat()}


@app.get("/fire/latest")
def fire_latest(
    hours: int = Query(default=24, ge=1, le=72, description="Look-back window in hours"),
    min_confidence: int = Query(default=50, ge=0, le=100, description="Minimum confidence %"),
):
    """
    Returns VIIRS fire detections for the Aberdare AOI.
    Results are cached for 30 minutes to avoid hitting GEE rate limits.
    """
    try:
        data = cached_query(hours_back=hours, min_confidence=min_confidence)
        return JSONResponse(content=data)
    except Exception as exc:
        log.exception("GEE query failed")
        raise HTTPException(status_code=502, detail=f"GEE query failed: {str(exc)}")


@app.get("/fire/history")
def fire_history(
    days: int = Query(default=3, ge=1, le=7, description="Days of history (max 7)"),
    min_confidence: int = Query(default=50, ge=0, le=100),
):
    """
    Returns fire detections from the last N days (heavier query, longer cache).
    """
    hours = days * 24
    try:
        data = cached_query(hours_back=hours, min_confidence=min_confidence)
        return JSONResponse(content=data)
    except Exception as exc:
        log.exception("GEE history query failed")
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/fire/boundary")
def fire_boundary():
    """Returns the Aberdare Forest AOI as a GeoJSON Feature."""
    return JSONResponse(content=get_aberdare_boundary())


@app.get("/")
def root():
    return {
        "service": "Aberdare Fire Detection API",
        "docs": "/docs",
        "endpoints": ["/fire/latest", "/fire/history", "/fire/boundary", "/health"],
    }
