import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def uuid4() -> str:
    return str(uuid.uuid4())


class TaskStatus(str, enum.Enum):
    QUEUED = "queued"
    CHECKING_CACHE = "checking_cache"
    REQUESTING_ARCHIVE = "requesting_archive"
    ARCHIVE_PENDING = "archive_pending"
    ARCHIVE_READY = "archive_ready"
    WAITING_DOWNLOAD = "waiting_download"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    CACHED = "cached"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


ACTIVE_TASK_STATUSES = {
    TaskStatus.QUEUED.value,
    TaskStatus.CHECKING_CACHE.value,
    TaskStatus.REQUESTING_ARCHIVE.value,
    TaskStatus.ARCHIVE_PENDING.value,
    TaskStatus.ARCHIVE_READY.value,
    TaskStatus.DOWNLOADING.value,
    TaskStatus.VERIFYING.value,
    TaskStatus.CACHED.value,
}


class ArchiveType(str, enum.Enum):
    ORIGINAL = "original"
    RESAMPLE = "resample"


class RequesterType(str, enum.Enum):
    ADMIN = "admin"
    USER = "user"
    GUEST = "guest"


# The public desk's own records: site users are attributed as `user`,
# anonymous visitors as `guest` — both visible to every non-admin, while
# admin-owned rows only surface once their archive is ready.
PUBLIC_REQUESTER_TYPES = (RequesterType.GUEST.value, RequesterType.USER.value)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    encrypted_cookie: Mapped[str] = mapped_column(Text)
    cookie_fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    gp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    credits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=100, index=True)
    cooldown_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_check: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_download_cost: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_download_cost_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    archives: Mapped[list["Archive"]] = relationship(back_populates="account")


class CacheEntry(Base):
    __tablename__ = "cache"

    sha1: Mapped[str] = mapped_column(String(40), primary_key=True)
    zip_path: Mapped[str] = mapped_column(Text, unique=True)
    filename: Mapped[str] = mapped_column(Text)
    filesize: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_accessed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expire_at: Mapped[datetime] = mapped_column(DateTime, index=True)

    archives: Mapped[list["Archive"]] = relationship(back_populates="cache_entry")


class Archive(Base):
    __tablename__ = "archives"
    __table_args__ = (Index("ix_archives_lookup", "gid", "token", "archive_type"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    gid: Mapped[int] = mapped_column(Integer, index=True)
    token: Mapped[str] = mapped_column(String(64))
    source_host: Mapped[str] = mapped_column(String(64))
    archive_type: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(32), default=TaskStatus.QUEUED.value, index=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    estimated_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost: Mapped[int] = mapped_column(Integer, default=0)
    cost_type: Mapped[str] = mapped_column(String(20), default="unknown")
    owner: Mapped[str | None] = mapped_column(String(36), ForeignKey("accounts.id"), nullable=True, index=True)
    archiver_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    remote_download_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    remote_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cache_sha1: Mapped[str | None] = mapped_column(String(40), ForeignKey("cache.sha1"), nullable=True)
    cache_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    account: Mapped[Account | None] = relationship(back_populates="archives")
    cache_entry: Mapped[CacheEntry | None] = relationship(back_populates="archives")
    tasks: Mapped[list["DownloadTask"]] = relationship(back_populates="archive")


class DownloadTask(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_owner", "requester_type", "requester_id"),
        UniqueConstraint("gid", "token", "archive_type", name="uq_tasks_gallery_archive"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    gallery_url: Mapped[str] = mapped_column(Text)
    gid: Mapped[int] = mapped_column(Integer, index=True)
    token: Mapped[str] = mapped_column(String(64))
    source_host: Mapped[str] = mapped_column(String(64))
    archive_type: Mapped[str] = mapped_column(String(20))
    archive_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("archives.id"), nullable=True)
    requester_type: Mapped[str] = mapped_column(String(20), index=True)
    requester_id: Mapped[str] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(32), default=TaskStatus.QUEUED.value, index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    downloaded_bytes: Mapped[int] = mapped_column(Integer, default=0)
    download_count: Mapped[int] = mapped_column(Integer, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    reconcile_count: Mapped[int] = mapped_column(Integer, default=0)
    reserved_bytes: Mapped[int] = mapped_column(Integer, default=0)
    wait_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    expired_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    archive: Mapped[Archive | None] = relationship(back_populates="tasks")
