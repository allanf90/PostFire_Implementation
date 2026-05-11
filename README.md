# Aberdare Forest — VIIRS Fire Detection API

Real-time active fire detections for Aberdare Forest, Kenya, powered by
NASA VIIRS SNPP Collection-2 via Google Earth Engine, served as a REST API
on Google Cloud Run.

---

## Project structure

```
viirs-fire-service/
├── main.py           # FastAPI application
├── gee_viirs.py      # Google Earth Engine query logic
├── requirements.txt
├── Dockerfile
└── .github/
    └── workflows/
        └── deploy.yml   # CI/CD → Cloud Run
```

---

## One-time setup

### 1. Google Earth Engine — service account

GEE requires a service account for server-side (non-interactive) use.

```bash
# Create a service account in your GCP project
gcloud iam service-accounts create viirs-fire \
  --display-name "VIIRS Fire Detection"

# Grant Earth Engine access
# Go to https://signup.earthengine.google.com/#!/service_accounts
# and register: viirs-fire@YOUR_PROJECT.iam.gserviceaccount.com

# Download a JSON key
gcloud iam service-accounts keys create gee_key.json \
  --iam-account viirs-fire@YOUR_PROJECT.iam.gserviceaccount.com
```

> **Important**: register the service account at
> https://signup.earthengine.google.com/#!/service_accounts
> before it can query GEE. This takes a few minutes.

---

### 2. GitHub Secrets (for CI/CD)

In your repo → Settings → Secrets → Actions, add:

| Secret name           | Value                                                          |
|-----------------------|----------------------------------------------------------------|
| `GCP_PROJECT_ID`      | your GCP project ID (e.g. `my-fire-project-123`)              |
| `GCP_SA_KEY`          | full JSON of a GCP service account key with Cloud Run + GCR roles |
| `GEE_SERVICE_ACCOUNT` | `viirs-fire@YOUR_PROJECT.iam.gserviceaccount.com`              |
| `GEE_KEY_JSON`        | contents of `gee_key.json` (the full JSON string)              |

For `GEE_KEY_JSON`, paste the entire contents of the key file — it is stored
as a GitHub secret and injected as an environment variable at runtime.

---

### 3. Enable required GCP APIs

```bash
gcloud services enable \
  run.googleapis.com \
  containerregistry.googleapis.com \
  earthengine.googleapis.com
```

---

## Local development

```bash
# Install dependencies
pip install -r requirements.txt

# Authenticate GEE locally (one-time)
earthengine authenticate

# Set env vars
export GEE_SERVICE_ACCOUNT="viirs-fire@YOUR_PROJECT.iam.gserviceaccount.com"
export GEE_KEY_JSON="$(cat gee_key.json)"

# Run
uvicorn main:app --reload
```

Then open http://localhost:8000/docs for the interactive API explorer.

---

## Deployment

Push to `main` — the GitHub Actions workflow builds the Docker image,
pushes to Google Container Registry, and deploys to Cloud Run automatically.

```bash
git add .
git commit -m "initial deploy"
git push origin main
```

The workflow prints the live URL at the end:
```
https://aberdare-fire-api-xxxxxxxxxx-ew.a.run.app
```

---

## API reference

### `GET /fire/latest`

Returns VIIRS fire detections for the last 24 hours (default).

**Query params:**
- `hours` — look-back window, 1–72 (default `24`)
- `min_confidence` — confidence threshold 0–100 (default `50`)

**Example:**
```
GET /fire/latest?hours=24&min_confidence=70
```

**Response:**
```json
{
  "type": "FeatureCollection",
  "query_window": { "start": "2024-01-15T06:00:00", "end": "2024-01-16T06:00:00", "hours_back": 24 },
  "count": 3,
  "features": [
    {
      "type": "Feature",
      "geometry": { "type": "Point", "coordinates": [36.721, -0.412] },
      "properties": {
        "brightness": 342.1,
        "frp": 12.4,
        "confidence": 82,
        "acq_datetime": "2024-01-15T09:32:00+00:00"
      }
    }
  ]
}
```

### `GET /fire/history`

Detections from the last N days (1–7).

```
GET /fire/history?days=3
```

### `GET /fire/boundary`

Returns the Aberdare Forest AOI as a GeoJSON polygon.

### `GET /health`

Liveness probe. Returns `{ "status": "ok" }`.

---

## Architecture notes

### Why Cloud Run?
- **Scales to zero** — you pay nothing when there are no requests
- **No server management** — just push a Docker image
- **Handles HTTPS** automatically
- **Region**: `africa-south1` (Johannesburg) keeps latency low for Kenya-based clients

### Caching
VIIRS overpasses Aberdare ~twice per day, so querying GEE more frequently
than that wastes quota. The API caches results for **30 minutes** in memory.
For a production system, swap the in-memory cache with Redis (Cloud Memorystore).

### Cost estimate
At ~50 API calls/day on Cloud Run free tier (2M requests/month free):
- **Cloud Run**: free
- **GEE**: free for non-commercial research use
- **Container Registry**: ~$0.02/GB/month for the image

---

## Extending this service

1. **Simulation engine** — add a `POST /fire/simulate` endpoint that accepts
   a fire point and returns a spread ellipse given wind + terrain data.
2. **Alerts** — add a background task (APScheduler) that polls `/fire/latest`
   every 30 min and sends an SMS via Africa's Talking if new points appear.
3. **Persistent history** — write detections to Firestore or BigQuery for
   long-term trend analysis.
