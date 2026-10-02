from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_secret: str = Field(min_length=32)
    admin_username: str = "admin"
    admin_password: str = Field(min_length=10)
    session_ttl_seconds: int = 86400
    database_url: str = "sqlite:////app/data/eh-downloader.db"
    cache_dir: Path = Path("/app/cache")
    frontend_dir: Path = Path("/app/frontend")
    default_retention_days: int = Field(default=0, ge=0, le=3650)
    default_cache_limit_bytes: int = Field(default=0, ge=0)
    default_max_archive_size_mb: int = Field(default=0, ge=0)
    default_worker_concurrency: int = Field(default=2, ge=1, le=4)
    account_cooldown_seconds: int = Field(default=3600, ge=60)
    capacity_wait_seconds: int = Field(default=86400, ge=300)
    worker_poll_seconds: float = Field(default=3.0, ge=0.25)
    worker_stale_seconds: int = Field(default=900, ge=60)
    eh_request_timeout_seconds: float = Field(default=30.0, ge=5.0)
    secure_cookies: bool = False

    # Site integration (`aiya/integrations/v1`): the master switch plus the
    # base URL (the site origin, no /wp-json suffix) and the service key
    # must ALL be set for the integration to come alive; with the switch
    # off the core access mode hides entirely and only guest/admin remain.
    aiya_core_enabled: bool = False
    aiya_core_base_url: str = ""
    aiya_core_service_key: str = ""
    aiya_core_session_cookie: str = "aiya_session"
    aiya_core_timeout_seconds: float = Field(default=5.0, ge=1.0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
