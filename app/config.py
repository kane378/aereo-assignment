"""Application settings, loaded from environment variables or a local `.env` file.

Every limit that protects the service (upload size, zip limits, rate limits...)
lives here so it can be tuned per environment without touching code.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Geospatial File Measurement API"
    version: str = "1.0.0"
    database_url: str = "sqlite:///./data/geo_measure.db"

    # --- Upload / processing limits (defence against resource exhaustion) ---
    max_upload_mb: int = 25
    max_zip_entries: int = 200
    max_unzipped_mb: int = 200
    max_compression_ratio: int = 200
    max_features: int = 50_000
    max_vertices: int = 5_000_000

    # --- Access control & abuse protection ---
    # Comma-separated list of accepted API keys. Empty = authentication disabled.
    api_keys: str = ""
    # Requests per minute per client IP. 0 disables the limiter.
    rate_limit_per_minute: int = 120
    upload_rate_limit_per_minute: int = 10

    # --- Deployment hardening ---
    enable_docs: bool = True
    enable_ui: bool = True
    allowed_hosts: str = "*"  # comma-separated; "*" allows any
    cors_origins: str = ""  # comma-separated; empty = CORS disabled

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def max_unzipped_bytes(self) -> int:
        return self.max_unzipped_mb * 1024 * 1024

    @property
    def api_key_list(self) -> list[str]:
        return [k.strip() for k in self.api_keys.split(",") if k.strip()]

    @property
    def allowed_host_list(self) -> list[str]:
        return [h.strip() for h in self.allowed_hosts.split(",") if h.strip()] or ["*"]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
