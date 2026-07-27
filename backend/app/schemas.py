from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    username: str
    csrf_token: str


class SessionResponse(BaseModel):
    authenticated: bool
    role: Literal["admin", "guest", "none"]
    csrf_token: str | None = None
    guest_cache_access: bool
    guest_download_mode: Literal["disabled", "resample", "original"]


class TaskCreateRequest(BaseModel):
    gallery_urls: str = Field(min_length=1, max_length=50000)
    archive_type: Literal["original", "resample"] = "resample"


class TaskResponse(BaseModel):
    id: str
    gallery_url: str
    gid: int
    archive_type: str
    title: str | None
    status: str
    progress: int
    download_count: int
    size_bytes: int | None
    filename: str | None
    error: str | None
    wait_reason: str | None
    retry_count: int
    created_at: datetime
    updated_at: datetime
    expired_at: datetime
    completed_at: datetime | None
    download_url: str | None


class AccountCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    cookies_txt: str = Field(min_length=1, max_length=65536)
    priority: int = Field(default=100, ge=0, le=10000)


class AccountUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=10000)


class AccountResponse(BaseModel):
    id: str
    name: str
    cookie_fingerprint: str
    gp: int | None
    credits: int | None
    enabled: bool
    priority: int
    cooldown_reason: str | None
    cooldown_until: datetime | None
    last_used_at: datetime | None
    last_download_cost: int | None
    last_download_cost_type: str | None
    last_error: str | None
    created_at: datetime


class SettingsResponse(BaseModel):
    guest_cache_access: bool
    guest_download_mode: Literal["disabled", "resample", "original"]
    cache_enabled: bool
    retention_days: int
    cache_limit_bytes: int
    max_archive_size_mb: int
    worker_concurrency: int
    api_token_configured: bool


class SettingsUpdateRequest(BaseModel):
    guest_cache_access: bool
    guest_download_mode: Literal["disabled", "resample", "original"]
    cache_enabled: bool
    retention_days: int = Field(ge=0, le=3650)
    cache_limit_bytes: int = Field(ge=1024**3, le=10 * 1024**4)
    max_archive_size_mb: int = Field(ge=1, le=1024 * 1024)
    worker_concurrency: int = Field(ge=1, le=4)


class TokenRotateResponse(BaseModel):
    token: str
