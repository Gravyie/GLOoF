# Glacial Threat Monitor

3D "mission control" for glacial lakes (South Lhonak, Imja Tsho). Water polygons are **derived live from Sentinel-2 pixels** - nothing is hand-drawn or stored.

```
AWS Earth Search STAC -> windowed COG reads (B03, B08, SCL) -> reflectance -> NDWI
  -> mask -> rasterio.features.shapes -> area filter/simplify (UTM) -> EPSG:4326 GeoJSON
  -> GET /api/telemetry -> MapLibre fill + line on 3D terrain
```

## Setup from scratch

### 1. Frontend (React + Vite + Tailwind + MapLibre)

```bash
npm create vite@latest frontend -- --template react
cd frontend
npm install
npm install maplibre-gl react-map-gl          # map stack
npm install tailwindcss @tailwindcss/vite     # Tailwind v4 (Vite plugin, no config file)
```

Then add `tailwindcss()` to the plugins in `vite.config.js` and put `@import "tailwindcss";` at the top of `src/index.css` (both already done in this repo).

### 2. Backend (FastAPI + geospatial stack)

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install fastapi "uvicorn[standard]" boto3 pystac-client rasterio geopandas shapely pydantic-settings cachetools
# or, pinned minimums:  pip install -r requirements.txt
cp .env.example .env
```

### 3. Run

```bash
# terminal 1
cd backend && source .venv/bin/activate && uvicorn src.main:app --reload --port 8000

# terminal 2
cd frontend && npm run dev            # http://localhost:5173  (proxies /api -> :8000)
```

Optional: `frontend/.env` with `VITE_MAPTILER_KEY=...` enables the contour grid lines.

### 4. Tests

```bash
cd backend && pip install -r requirements-dev.txt && pytest
cd frontend && node --test test/slabDem.test.mjs && npm run build
```

## Demo runbook (replay of the Oct 2023 South Lhonak outburst)

1. **With internet, once, before the demo:** start backend + frontend, open the Live view and wait for the South Lhonak
   outline to appear. The backend saves it to `backend/data/` so the map still works if the venue Wi-Fi fails
   (the top bar then says "Showing saved satellite data").
2. Set `ALERT_API_KEY=demo123` in `backend/.env` (needed only to approve alerts manually) and restart the backend.
3. Click **Replay: 2023 outburst** -> **Start replay**. The server runs the clock; the risk engine evaluates every
   simulated 15 minutes and fires alerts automatically on each level change (Watch -> Advisory -> Warning -> Evacuate).
   Delivery is *simulated* (no SMS) unless `SIM_LIVE_SMS=true` and `ALERT_DRY_RUN=false`.
4. To show the human-in-the-loop step, untick **Auto-approve public alerts** before starting: Warning/Evacuate public
   alerts then wait in the log until someone enters the operator key and clicks **Approve**.
5. Be upfront that the replay timeline (rain, sensor readings, field report) is a scripted illustration; only the lake
   outline and terrain are real data.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/lakes` | Lake registry (id, bbox, centre) |
| `GET /api/telemetry?lake=&refresh=` | Latest clear-sky water GeoJSON + scene/stat metadata |
| `GET /api/timeseries?lake=&n=` | Water area over recent clear scenes + growth screen |
| `GET /api/sim/state`, `POST /api/sim/{start,pause,speed,reset}` | Replay simulation (server-side clock) |
| `GET /api/alerts`, `POST /api/alerts/{id}/{approve,reject}` | Alert audit log + operator approval |
| `POST /api/alert` | Bilingual SMS via SNS. Needs `X-API-Key`; **dry-run by default** |

## What was changed from the brief, and why

- **Windowed COG reads, not whole-tile downloads.** A Sentinel-2 tile band is ~100 MB; only the ~5 km window around the lake is fetched (HTTP range requests).
- **Reflectance scale/offset applied** from STAC `raster:bands`. Since baseline 04.00 DNs carry a -1000 offset; NDWI is a ratio so skipping it biases the result.
- **NDWI > 0.1 kept, plus a NIR <= 0.20 guard and SCL cloud/shadow mask.** Fresh snow can exceed NDWI 0.1; its NIR reflectance is high, water's is not. Test `test_snow_guard_matters` demonstrates it.
- **Slab DEM (`slabDem.js`) in addition to the inverted mask.** The mask only paints outside black; the terrain mesh remains and tall neighbours occlude the slab at low pitch. A `slab://` tile protocol flattens elevation outside the box, giving real cliff walls.
- **`services/telemetry.py` + `core/cache.py`** (orchestration + TTL cache) keep routes thin; Sentinel-2 revisits every ~5 days so results are cached 6 h.
- **Tailwind v4:** `@import "tailwindcss"` replaces the old `@tailwind` directives.

## Limits to know about

- Lake centres in `core/config.py` are approximate - check them on the map.
- SMS translations (Hindi, Nepali) must be reviewed by native speakers / the disaster authority. India needs DLT registration for transactional SMS; SNS sandbox only reaches verified numbers.
- The growth flag compares first vs latest clear scene; it is a screen, not a GLOF forecast. Steep-valley topographic shadow can still produce false water.
