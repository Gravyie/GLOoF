"""Environment-driven settings and the registry of monitored lakes.

Everything tunable lives here so the services stay free of magic numbers.
Lake *locations* are static configuration (where to look); the water/ice
geometry is never stored anywhere - it is always derived from satellite pixels.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# --------------------------------------------------------------------------- #
# Lake registry
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Lake:
    id: str
    name: str
    country: str
    center: tuple[float, float]  # (lon, lat), WGS84
    half_size_deg: float  # half-width of the analysis window / 3D slab
    lake_elevation_m: float  # approximate surface elevation, used for the slab floor
    alert_language: str  # second language of the bilingual SMS ("hi" | "ne")
    # A point inside the lake. The water polygon containing (or nearest to) it is
    # "the lake"; "largest polygon" is fragile when a neighbouring lake is bigger.
    seed: tuple[float, float] | None = None
    utm_epsg: int = 32645  # metric CRS for boundary maths
    # Wider window for the glacier layer only (the lake window would clip the parent glacier).
    ice_bbox: tuple[float, float, float, float] | None = None
    # Documented real-world events shown on the archive timeline (facts, not model output).
    events: tuple[dict, ...] = ()

    @property
    def seed_point(self) -> tuple[float, float]:
        return self.seed or self.center

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """(west, south, east, north) in WGS84."""
        lon, lat = self.center
        h = self.half_size_deg
        return (round(lon - h, 6), round(lat - h, 6), round(lon + h, 6), round(lat + h, 6))


# NOTE: centres are approximate - verify them on the map and nudge if needed.
LAKES: dict[str, Lake] = {
    "south_lhonak": Lake(
        id="south_lhonak",
        name="South Lhonak Lake",
        country="India (Sikkim)",
        # Centre verified against Sentinel-2 imagery: the window covers the whole
        # lake including the eastern (outlet / moraine) end that failed in 2023.
        center=(88.196, 27.911),
        half_size_deg=0.025,
        lake_elevation_m=5200,
        alert_language="hi",
        seed=(88.196, 27.9115),
        utm_epsg=32645,
        # Covers RGI 7.0 G-15-07986 (parent glacier, terminus on the lake's west shore) and the
        # neighbouring G-15-07987, plus ~500 m. Tile 45RXL contains it fully.
        ice_bbox=(88.123, 27.850, 88.221, 27.936),
        events=(
            {"date": "2023-10-04", "label": "Outburst flood (GLOF)",
             "detail": "Moraine-dammed lake breached on the night of 3-4 Oct 2023; "
                       "the flood travelled down the Teesta valley and destroyed the Chungthang dam."},
        ),
    ),
    "imja_tsho": Lake(
        id="imja_tsho",
        name="Imja Tsho",
        country="Nepal (Khumbu)",
        center=(86.9278, 27.8997),
        half_size_deg=0.025,
        lake_elevation_m=5010,
        alert_language="ne",
        utm_epsg=32645,
    ),
}


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # STAC
    stac_url: str = "https://earth-search.aws.element84.com/v1"
    stac_collection: str = "sentinel-2-l2a"
    search_window_days: int = 90
    max_tile_cloud_cover: float = 60.0  # coarse server-side filter (whole tile)
    max_candidate_scenes: int = 12

    # Water extraction
    ndwi_threshold: float = 0.1
    # Snow has a high NIR reflectance, water does not. Without this guard,
    # NDWI > 0.1 also picks up fresh snow on glaciers.
    max_nir_reflectance: float = 0.20
    max_local_cloud_fraction: float = 0.15  # measured from SCL inside the window
    min_valid_fraction: float = 0.90
    min_polygon_area_m2: float = 2000.0  # drop speckle (< 20 px at 10 m)
    simplify_tolerance_m: float = 5.0
    exclude_snow_class: bool = False

    # Time series / threat heuristic
    timeseries_days: int = 730
    # Only compare scenes from the open-water season. High-altitude lakes are
    # frozen / snow-covered in winter-spring, which NDWI reads as "no water",
    # so mixing seasons fakes huge growth (or shrinkage).
    melt_season_months: str = "6,7,8,9,10"
    max_plausible_growth_pct: float = 100.0
    timeseries_max_scenes: int = 8
    timeseries_workers: int = 4
    growth_elevated_pct: float = 10.0
    growth_critical_pct: float = 25.0

    # Boundary archive (multi-year water / ice outlines)
    archive_dir: str = "archive"  # relative to backend/; committed so the demo works offline
    archive_bucket: str | None = None  # if set, the archive is read from / written to S3
    archive_prefix: str = "archive/"
    archive_start: str = "2017-12-01"  # first Sentinel-2 L2A scenes over the Himalaya on Earth Search
    # MM-DD window: open-water season. Ends early November: later, low sun casts long
    # shadows and the lake starts to freeze, both of which NDWI misreads.
    archive_season: str = "06-01,11-05"
    archive_min_gap_days: int = 8
    archive_max_cloud_fraction: float = 0.08
    replay_duration_s: float = 90.0

    # Perennial ice composites (one outline per year from end-of-ablation scenes)
    ice_season: str = "09-15,10-31"  # seasonal snow is at its minimum
    ice_persistence: float = 0.7  # ice in >= this fraction of a pixel's valid observations
    ice_min_scenes: int = 3  # fewer scenes -> low-confidence year
    ice_baseline_year: int = 2018
    ice_max_tile_cloud: float = 60.0

    # Cache
    cache_ttl_seconds: int = 6 * 3600  # Sentinel-2 revisit is ~5 days

    # SNS
    aws_region: str = "ap-south-1"
    sns_topic_arn: str | None = None
    alert_phone_numbers: str = ""
    sns_entity_id: str | None = None
    sns_template_id: str | None = None
    sns_sender_id: str | None = None
    operator_phone_numbers: str = ""
    official_phone_numbers: str = ""
    # Simulation never sends real SMS unless this is explicitly true AND alert_dry_run is false.
    sim_live_sms: bool = False
    alert_api_key: str | None = None
    alert_dry_run: bool = True
    # Third gate for the demo drill: a real SNS publish from the app needs
    # ALERT_DRY_RUN=false AND SIM_LIVE_SMS=true AND DEMO_SMS_LIVE=true.
    demo_sms_live: bool = False
    # The scheduled Lambda only notifies for real when this is true (off by default,
    # so a fresh deployment can never text anyone on its own).
    lambda_live_alerts: bool = False
    # DynamoDB table for the alert audit log + demo latch (AWS). Unset: local files under data_dir.
    alerts_table: str | None = None
    data_dir: str = "data"
    # Persist the alert log + demo latch to data_dir (./run.sh sets this). Off by
    # default so tests and throwaway processes never share state on disk.
    local_persistence: bool = False
    alert_cooldown_seconds: int = 300

    # HTTP
    cors_origins: str = Field(default="http://localhost:5173")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def season_window(self) -> tuple[str, str]:
        a, b = (p.strip() for p in self.archive_season.split(","))
        return a, b

    @property
    def sns_live(self) -> bool:
        """Simulations/replays deliver for real only if BOTH flags say so."""
        return self.sim_live_sms and not self.alert_dry_run

    @property
    def drill_live(self) -> bool:
        """All three gates set and a topic to publish to."""
        return self.sns_live and self.demo_sms_live and bool(self.sns_topic_arn)

    @property
    def melt_months(self) -> set[int]:
        return {int(m) for m in self.melt_season_months.split(",") if m.strip()}

    @staticmethod
    def _split(v: str) -> list[str]:
        return [p.strip() for p in v.split(",") if p.strip()]

    @property
    def operator_phones(self) -> list[str]:
        return self._split(self.operator_phone_numbers)

    @property
    def official_phones(self) -> list[str]:
        return self._split(self.official_phone_numbers)

    @property
    def phone_list(self) -> list[str]:
        return [p.strip() for p in self.alert_phone_numbers.split(",") if p.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


class UnknownLakeError(KeyError):
    pass


def get_lake(lake_id: str) -> Lake:
    try:
        return LAKES[lake_id]
    except KeyError as exc:
        raise UnknownLakeError(lake_id) from exc
