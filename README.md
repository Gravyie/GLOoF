# Glacial Lake Monitor

A monitoring and early-warning prototype for glacial lake outburst floods (GLOFs). It derives the water and ice boundaries of
high-altitude lakes directly from Sentinel-2 satellite imagery, replays how those boundaries changed over the past nine years
on 3D terrain, evaluates outburst risk on each observation, and dispatches alerts through AWS SNS when a risk threshold is crossed.

The reference case is **South Lhonak Lake, Sikkim, India**, which burst on the night of 3-4 October 2023. The resulting flood
travelled down the Teesta valley and destroyed the Chungthang hydropower dam.

---

## Contents

1. [What it does](#what-it-does)
2. [Architecture](#architecture)
3. [Data and method](#data-and-method)
4. [Risk model](#risk-model)
5. [Notifications](#notifications)
6. [Getting started](#getting-started)
7. [AWS deployment](#aws-deployment)
8. [API reference](#api-reference)
9. [Project layout](#project-layout)
10. [Testing](#testing)
11. [Demo runbook](#demo-runbook)
12. [Limitations](#limitations)

---

## What it does

The application has three views.

| View | Purpose | Data |
|---|---|---|
| **Time machine** (default) | Replays every clear Sentinel-2 observation of the lake from December 2017 to today. The shoreline morphs between real outlines, each past season remains visible as a coloured outline, and the risk level is shown as it would have been on each date. Alerts are dispatched automatically as the replay passes a threshold crossing. | Real (Sentinel-2 L2A) |
| **Latest scene** | Water and ice outlines from the most recent cloud-free scene, with a same-season area trend. An operator can send a manual alert. | Real, computed on request |
| **Outburst night** | An hour-by-hour reconstruction of the night of the 2023 outburst, exercising the full escalation and operator-approval workflow. | Scripted illustration, labelled as such |

---

## Architecture

```
                   AWS Open Data
   Sentinel-2 L2A COGs (Earth Search STAC)      Terrarium elevation tiles
                 |                                        |
                 v                                        |
   +-----------------------------+                        |
   | Lambda: ingest (container)  | <- EventBridge, daily   |
   |  new scene -> NDWI / NDSI   |                        |
   |  -> outlines -> risk level  |                        |
   +-------------+---------------+                        |
                 |  archive JSON          level escalated  |
                 v                              |          |
          +-------------+               +-------v------+   |
          | S3 archive  |               |  SNS topic   |---+--> email / SMS subscribers
          +------+------+               +-------^------+        (filtered by audience)
                 |                              |
                 v                              |
   +-----------------------------+              |
   | FastAPI backend             |--------------+
   |  archive store (local / S3) |   replay alerts use the same dispatcher and topic
   |  risk timeline              |
   |  server-side replay clock   |
   +-------------+---------------+
                 |  REST (proxied by Vite in development)
                 v
   +-----------------------------+
   | React + MapLibre frontend   |
   |  3D terrain slab            |
   |  morphing shoreline         |
   |  risk panel, charts         |
   |  timeline, dispatch log     |
   +-----------------------------+
```

**Backend.** Python 3.12+, FastAPI, rasterio/GDAL, GeoPandas, Shapely, pystac-client, boto3.
**Frontend.** React 19, Vite, Tailwind CSS v4, MapLibre GL via react-map-gl.
**Infrastructure.** Terraform: S3, SNS, ECR, Lambda (container image), EventBridge, IAM.

Design decisions worth noting:

- **The server owns the replay clock.** A background thread advances it and dispatches alerts, so notifications fire on schedule
  even when no browser is connected. The frontend polls the clock and interpolates between polls for smooth animation.
- **One dispatcher for every source.** The archive replay, the scripted replay, the Lambda and manual alerts all go through
  `AlertDispatcher`, so a real deployment exercises exactly the code path that the demo shows.
- **Every integration reports its real mode.** `GET /api/system` states whether SNS delivery is live or simulated and where the
  archive is loaded from; the UI displays this in the header and the notifications panel.

---

## Data and method

### Imagery

Sentinel-2 Level-2A surface reflectance (ESA Copernicus), discovered through the Element 84 Earth Search STAC API and read
as Cloud-Optimised GeoTIFFs from the AWS Open Data registry. Only the window around the lake (about 5 x 5 km) is fetched,
using HTTP range requests; full tiles are never downloaded.

Earth Search provides no Level-2A coverage of this area before December 2017, so the archive covers the 2018 to 2026 melt seasons.

### Boundary extraction

For each scene:

1. Read the green (B03), near-infrared (B08), shortwave-infrared (B11) and scene-classification (SCL) bands.
2. Convert digital numbers to reflectance using the scale and offset published in the STAC `raster:bands` metadata
   (processing baseline 04.00 introduced a -1000 offset; ignoring it biases the index).
3. **Water:** NDWI = (Green - NIR) / (Green + NIR) > 0.1, with NIR reflectance <= 0.20 to reject fresh snow.
4. **Snow and ice:** NDSI = (Green - SWIR) / (Green + SWIR) > 0.4, with NIR >= 0.11.
5. Mask cloud, cloud shadow and cirrus using SCL.
6. Vectorise the masks, filter small polygons, simplify in metres (UTM) and reproject to WGS84.
7. Identify the lake as the water polygon at a configured seed point, rather than the largest polygon (a neighbouring lake can be larger).

### Archive construction

`scripts/build_archive.py` builds the multi-year archive:

- One STAC search per season, restricted to scenes whose footprint fully contains the analysis window.
  Overlapping tiles are de-duplicated by date, keeping the least cloudy.
- The season runs from 1 June to 5 November. Outside that window the lake is frozen, and late-season low sun casts long shadows
  that NDWI misreads as water.
- A cheap cloud screen on the SCL band runs before the 10 m bands are read.
- Scenes whose lake extent falls outside 60% to 160% of the archive median are discarded as artefacts.
- Frames closer together than 8 days are thinned, unless the area changed sharply.

The result is committed at `backend/archive/south_lhonak.json` so the application works without network access.

---

## Risk model

The risk timeline (`backend/src/services/history.py`) evaluates every archived frame using **only the frames up to and including
that date**, as a monitor running on that date would have. It never looks ahead.

| Indicator | Definition |
|---|---|
| Growth vs baseline | Three-scene median of lake extent, compared with the median extent of the first season(s) |
| Shoreline advance | Largest distance from any part of the current lake outside the baseline outline to that outline. Inflow streams are removed first by a morphological opening. |
| Two-year trend | Theil-Sen slope of lake extent over the trailing two years, as a percentage of baseline per year |
| Drainage | Two consecutive frames well below the median of the preceding frames, not explained by ice cover on the lake |

| Level | Rule (placeholder thresholds) |
|---|---|
| Watch | Growth >= 10%, or shoreline advance >= 150 m |
| Advisory | Growth >= 15% and trend >= 2% per year |
| Warning | Growth >= 25%, or growth >= 15% with shoreline advance >= 300 m |
| Evacuate | Sudden drainage detected |

Levels pass through a hysteresis tracker: Watch and Advisory require two consecutive evaluations, Warning and Evacuate escalate
immediately and are never cleared automatically.

**Result on the real South Lhonak archive.** Watch from October 2020 (lake about 12% above its 2018-19 extent), Advisory from
October 2021 (sustained growth of about 15%), and Warning on 24 October 2023, the first clear scene after the outburst, driven by
a shoreline shift of over 300 m. The satellite-only monitor was therefore at Advisory two years before the flood; the Warning
itself is post-event detection. The thresholds are round-number placeholders and have not been calibrated.

---

## Notifications

Alerts are mapped to audiences by level:

| Level | Audience | Approval |
|---|---|---|
| Watch | Control-room operators | None |
| Advisory | District officials | None |
| Warning, Evacuate | District officials and downstream residents | Public messages require operator approval unless auto-approve is enabled |

Public messages are bilingual (English plus Hindi or Nepali) and drawn only from vetted templates.

All audiences publish to a **single SNS topic**. Each message carries `audience`, `level` and `lake` attributes, and each
subscription uses an SNS filter policy to receive only its audiences. Email subscribers receive a subject line; SMS is sent as
transactional.

Delivery is **simulated unless both** `SIM_LIVE_SMS=true` and `ALERT_DRY_RUN=false` are set and a topic or recipient list is
configured. Simulated deliveries are labelled as such everywhere they appear.

---

## Getting started

### Prerequisites

- Python 3.12 or later
- Node.js 20 or later
- Internet access for terrain tiles and for rebuilding the archive (the committed archive works offline)

### Run

```bash
./run.sh
```

The script creates the Python virtual environment and installs frontend dependencies if needed, frees ports 8000 and 5173,
starts both servers and prints `READY` only after confirming that the API is serving the archive. Open
http://localhost:5173 and press **Play**.

Logs are written to `backend/server.log` and `frontend/vite.log`.

### Manual setup

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env
.venv/bin/uvicorn src.main:app --port 8000

cd frontend
npm install
npm run dev
```

### Rebuild the archive

```bash
cd backend
.venv/bin/python -m scripts.build_archive --lake south_lhonak               # full rebuild, about 5 minutes
.venv/bin/python -m scripts.build_archive --lake south_lhonak --reuse-raw   # re-run analysis on cached frames, no download
.venv/bin/python -m scripts.build_archive --lake south_lhonak --update      # append scenes newer than the archive
```

### Configuration

All settings are environment variables (see `backend/.env.example` and `backend/src/core/config.py`). The most relevant:

| Variable | Default | Meaning |
|---|---|---|
| `SNS_TOPIC_ARN` | unset | Topic used for every audience |
| `SIM_LIVE_SMS` | `false` | Allow replays to deliver for real |
| `ALERT_DRY_RUN` | `true` | Global safety switch; must be `false` for any real delivery |
| `ALERT_API_KEY` | unset | Operator key for approvals, manual alerts and test notifications |
| `ARCHIVE_BUCKET` | unset | Read the archive from S3 instead of `backend/archive/` |
| `REPLAY_DURATION_S` | `90` | Length of the full archive replay at 1x speed |

---

## AWS deployment

The `infra/` directory provisions the cloud side with Terraform.

| Resource | Purpose |
|---|---|
| S3 bucket (versioned, private) | Boundary archive and the last notified level per lake |
| SNS topic and subscriptions | Alert fan-out, filtered by audience |
| ECR repository | Lambda container image |
| Lambda `glof-ingest` | Daily: fetch new scenes, extend the archive, evaluate risk, notify on escalation |
| EventBridge rule | Daily schedule for the Lambda |
| IAM role and policy | Least-privilege access to the bucket and topic, for both the Lambda and the API |

```bash
aws sts get-caller-identity                     # credentials must be valid
cp infra/terraform/terraform.tfvars.example infra/terraform/terraform.tfvars
# edit subscribers: your email address, and optionally verified SMS numbers
cd infra && ./deploy.sh
```

`deploy.sh` is idempotent. It creates the registry, builds and pushes the Lambda image, applies the remaining resources, seeds
the archive into S3 and prints the lines to add to `backend/.env`. Confirm the subscription email before expecting delivery.

To verify the path after deployment:

```bash
# from the API (requires ALERT_API_KEY): verifies the topic and sends one test message
curl -X POST localhost:8000/api/notify/test -H "X-API-Key: $ALERT_API_KEY"

# from the Lambda: dry run, then a labelled drill that sends a real message
aws lambda invoke --function-name glof-ingest --payload '{"dry_run":true}' --cli-binary-format raw-in-base64-out /dev/stdout
aws lambda invoke --function-name glof-ingest --payload '{"force_level":"WARNING"}' --cli-binary-format raw-in-base64-out /dev/stdout
```

The SNS SMS sandbox only reaches verified numbers, and transactional SMS in India requires DLT registration. Email works immediately.

---

## API reference

| Method and path | Purpose |
|---|---|
| `GET /api/health` | Liveness |
| `GET /api/lakes` | Monitored lakes |
| `GET /api/system` | Real mode of each integration |
| `GET /api/archive?lake=` | All archived outlines and the per-frame risk timeline, in one response |
| `GET /api/archive/replay/state?lake=` | Replay clock, current level and dispatched alerts |
| `POST /api/archive/replay/{start,pause,speed,seek,reset}?lake=` | Control the replay; `seek` takes `progress` (0 to 1) or `date` |
| `POST /api/archive/reload` | Re-read archives from the store (operator key) |
| `POST /api/notify/test` | Verify the SNS topic and send one test message (operator key; refused in simulated mode) |
| `GET /api/telemetry?lake=` | Latest clear-scene outlines, computed live |
| `GET /api/timeseries?lake=` | Recent same-season area trend |
| `GET /api/alerts` | Alert audit log |
| `POST /api/alerts/{id}/{approve,reject}` | Operator decision on a pending public alert |
| `POST /api/alert` | Manual bilingual alert (operator key; dry-run by default) |
| `GET /api/sim/state`, `POST /api/sim/{start,pause,speed,reset}` | Scripted outburst-night replay |

---

## Project layout

```
backend/
  archive/                  committed boundary archive (JSON)
  scripts/build_archive.py  archive builder CLI
  src/
    api/                    routes and schemas
    core/config.py          settings and lake registry
    services/
      stac_client.py        scene discovery
      geospatial.py         raster to vector pipeline
      boundary.py           shoreline geometry (advance, retreat, freeze check)
      archive.py            archive builder and local/S3 store
      history.py            per-frame risk timeline
      history_replay.py     server-side replay clock and background ticker
      risk.py               rule engine and hysteresis tracker
      dispatch.py           audience policy and delivery
      sns_service.py        SNS publishing
    lambda_ingest.py        scheduled ingest handler
  Dockerfile.lambda
  tests/
frontend/
  src/
    components/ArchiveView.jsx   time machine view
    components/AreaChart.jsx     time-series chart
    components/MapViewer.jsx     3D terrain slab and map layers
    hooks/useReplay.js           replay polling and clock interpolation
    utils/morph.js               shoreline interpolation
    utils/slabDem.js             terrain clipping protocol
infra/
  terraform/                AWS resources
  deploy.sh                 build, push, apply, seed
run.sh                      one-command local start
TODO.md                     known limitations and next steps
```

---

## Testing

```bash
cd backend && .venv/bin/pytest
cd frontend && node --test test/slabDem.test.mjs && npm run build
cd infra/terraform && terraform init -backend=false && terraform validate
```

Backend tests cover the water and snow extraction, SNS safety rails, the risk engine and hysteresis, shoreline geometry,
the freeze guard, outlier filtering, the absence of look-ahead in the risk timeline, and the replay firing and re-arming alerts.

---

## Demo runbook

1. Run `./run.sh` and wait for `READY`. The header should read "SNS simulated" or "SNS live" as intended.
2. Open the Time machine view. Press **Play** (the full archive takes 90 seconds at 1x).
3. Point out the shoreline moving between real scenes, the coloured outlines of earlier seasons, and the dashed baseline.
4. As the replay passes October 2020, 2021 and 2023, a notice appears, the dispatch log fills and, when live, the email or SMS arrives.
5. Drag the timeline back to show that alerts after the playhead are re-armed and fire again.
6. State clearly that the outlines are real observations, that the shoreline between observations is interpolated for display,
   that the thresholds are uncalibrated placeholders, and that the Outburst night view is a scripted illustration.

---

## Limitations

- Imagery begins in December 2017; the archive covers nine seasons rather than ten.
- Scene-to-scene lake area varies by about 10% because of topographic shadow, icebergs and fresh snow.
- The 2023 outburst reduced the lake's surface area by only about 14%, within that noise, so drainage is not detected from area
  alone. The event is detected through the shoreline shift instead.
- No clear scene exists for September 2023, immediately before the outburst.
- The snow and ice layer includes seasonal snow and does not detect debris-covered ice.
- Risk thresholds are placeholders and must be calibrated with the relevant disaster-management authority.
- Bilingual messages must be reviewed by native speakers before operational use.
- The alert log is held in memory.

See `TODO.md` for the full list of planned work.
