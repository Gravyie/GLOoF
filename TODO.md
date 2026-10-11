# Later (deferred during the demo speedrun)

## Data quality
- [ ] Imagery starts Dec 2017 (no anonymous Sentinel-2 L2A over this tile earlier on Earth Search). For a true 10-year
      view, add Landsat 8 (2016-2017) via `landsat-c2-l2` (requester-pays bucket, needs AWS creds).
- [ ] Frame-to-frame lake-area noise is about ±10% (topographic shadow on the south shore, icebergs, fresh snow).
      Try: NDWI on a shadow-masked DEM (Copernicus GLO-30), MNDWI with SWIR, or a per-scene Otsu threshold.
- [ ] The 2023 outburst shows only a ~14% area drop (the lake level fell; the footprint changed less). Add a
      lake-level/volume proxy (DEM hypsometry) so drainage detection does not rely on area alone.
- [ ] Single big STAC search (2017 -> now) was observed to drop later years; investigate pagination
      (we now search per season).
- [ ] Build an archive for Imja Tsho as well (`python -m scripts.build_archive --lake imja_tsho`).

## Perennial ice layer (Phase 1 findings)
- [ ] Late-season snowfall survives the 70% persistence vote at this altitude: 2025 (+32 km2 "gained") and 2026
      (+41 km2, season still in progress) are snow, not glacier growth. Options to decide on: restrict the comparison to
      the RGI glacier footprint plus a buffer; use only the least-snowy scene(s) per year; raise persistence to 0.9-1.0;
      or flag years whose composite exceeds the baseline by a set margin as snow-affected.
- [ ] Late-season scenes from Oct 2017 observe only 6-22% of the ice window (cloud), so 2017 has no composite.
- [ ] RGI 7.0 outlines for this area come from a 1994-07-11 source image, not about 2000; labels say so.

## Risk engine
- [ ] Thresholds in `history.py` are round-number placeholders. Calibrate with the disaster-management authority.
- [ ] Add operator "clear" for WARNING/EVACUATE in the archive replay (the tracker never steps down from those).

## AWS (Phase 3)
- [ ] Not deployed yet. Before `infra/deploy.sh`: create the `main` and `sms` profiles (SNS-only IAM user in the SMS
      account), run `infra/bootstrap-state.sh`, fill `infra/terraform/terraform.tfvars`, start Docker.
- [ ] After deploy: confirm each email subscription; Indian numbers may need DLT registration for carriers to deliver.
- [ ] Cross-region CloudWatch alarm actions were not confirmed either way in AWS docs; alarms use an EventBridge relay
      Lambda. If direct actions turn out to work, the relay could be dropped.
- [ ] Lambda real alerts are off (`lambda_live_alerts = false`); decide when to turn them on.
- [ ] HTTPS between CloudFront and the ALB (needs a domain + ACM certificate); today the origin hop is HTTP inside AWS.
- [ ] Build and test both images locally against the Lambda Runtime Interface Emulator / docker run.

## Frontend
- [ ] Mobile layout (panels currently hidden below 1100px).
- [ ] Table view of the archive (accessibility), CSV export.
- [ ] Code-split maplibre (bundle is ~1.3 MB).

## Tests
- [ ] Unit tests for `boundary.py`, `history.py`, `history_replay.py`, `lambda_ingest.py` (fake store + fake SNS).
- [ ] Experimental hydrology model (`backend/experimental`) still has a failing test.
