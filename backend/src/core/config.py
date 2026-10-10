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
        center=(88.183, 27.913),
        half_size_deg=0.025,
        lake_elevation_m=5200,
        alert_language="hi",
    ),
    "imja_tsho": Lake(
        id="imja_tsho",
        name="Imja Tsho",
        country="Nepal (Khumbu)",
        center=(86.9278, 27.8997),
        half_size_deg=0.025,
        lake_elevation_m=5010,
        alert_language="ne",
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
    alert_cooldown_seconds: int = 300

    # HTTP
    cors_origins: str = Field(default="http://localhost:5173")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

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
