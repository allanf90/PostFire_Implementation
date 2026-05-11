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