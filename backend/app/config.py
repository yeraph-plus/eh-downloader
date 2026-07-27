from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
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
    default_retention_days: int = Field(default=7, ge=0, le=3650)
    default_cache_limit_bytes: int = Field(default=20 * 1024**3, ge=1024**3)
    default_max_archive_size_mb: int = Field(default=4096, ge=1)
    default_worker_concurrency: int = Field(default=2, ge=1, le=4)
    account_cooldown_seconds: int = Field(default=3600, ge=60)
    capacity_wait_seconds: int = Field(default=86400, ge=300)
    worker_poll_seconds: float = Field(default=3.0, ge=0.25)
    worker_stale_seconds: int = Field(default=900, ge=60)
    eh_proxy_url: str | None = None
    eh_request_timeout_seconds: float = Field(default=30.0, ge=5.0)
    secure_cookies: bool = False

    @field_validator("eh_proxy_url", mode="before")
    @classmethod
    def empty_proxy_is_none(cls, value: object) -> object:
        return None if value == "" else value


@lru_cache
def get_settings() -> Settings:
    return Settings()
