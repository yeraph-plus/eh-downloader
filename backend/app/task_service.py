from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import Settings
from .eh_client import GalleryRef
from .models import Archive, CacheEntry, DownloadTask, RequesterType, TaskStatus, utcnow


class TaskService:
    def __init__(self, settings: Settings):
        self.settings = settings

    @staticmethod
    def find_existing(session: Session, gallery: GalleryRef, archive_type: str) -> DownloadTask | None:
        return session.scalar(
            select(DownloadTask).where(
                DownloadTask.gid == gallery.gid,
                DownloadTask.token == gallery.token,
                DownloadTask.archive_type == archive_type,
            ).limit(1)
        )

    def create_or_existing(
        self, session: Session, gallery: GalleryRef, archive_type: str,
        requester_type: str, requester_id: str,
    ) -> tuple[DownloadTask, bool]:
        existing = self.find_existing(session, gallery, archive_type)
        if existing:
            if existing.status not in {TaskStatus.FAILED.value, TaskStatus.EXPIRED.value}:
                return existing, False
            existing.gallery_url = gallery.gallery_url
            existing.source_host = gallery.host
            existing.archive_id = None
            existing.requester_type = requester_type
            existing.requester_id = requester_id
            existing.status = TaskStatus.QUEUED.value
            existing.progress = 0
            existing.downloaded_bytes = 0
            existing.download_count = 0
            existing.retry_count = 0
            existing.reconcile_count = 0
            existing.reserved_bytes = 0
            existing.wait_reason = None
            existing.error = None
            existing.next_attempt_at = None
            existing.claimed_at = None
            existing.expired_at = utcnow() + timedelta(seconds=self.settings.capacity_wait_seconds)
            existing.completed_at = None
            session.flush()
            return existing, True
        task = DownloadTask(
            gallery_url=gallery.gallery_url,
            gid=gallery.gid,
            token=gallery.token,
            source_host=gallery.host,
            archive_type=archive_type,
            requester_type=requester_type,
            requester_id=requester_id,
            status=TaskStatus.QUEUED.value,
            expired_at=utcnow() + timedelta(seconds=self.settings.capacity_wait_seconds),
        )
        try:
            with session.begin_nested():
                session.add(task)
                session.flush()
        except IntegrityError:
            existing = self.find_existing(session, gallery, archive_type)
            if existing:
                return existing, False
            raise
        return task, True

    @staticmethod
    def visible_query(identity_kind: str, *, guest_access: bool):
        query = select(DownloadTask).order_by(DownloadTask.created_at.desc())
        if identity_kind == RequesterType.GUEST.value:
            query = query.where(
                or_(
                    DownloadTask.requester_type == RequesterType.GUEST.value,
                    DownloadTask.status.in_({
                        TaskStatus.COMPLETED.value,
                        TaskStatus.WAITING_DOWNLOAD.value,
                    }),
                ) if guest_access else DownloadTask.id.is_(None)
            )
        return query

    @staticmethod
    def can_access(task: DownloadTask, identity_kind: str, *, guest_access: bool) -> bool:
        return identity_kind == RequesterType.ADMIN.value or guest_access and (
            task.requester_type == RequesterType.GUEST.value
            or task.status in {TaskStatus.COMPLETED.value, TaskStatus.WAITING_DOWNLOAD.value}
            and task.archive is not None
        )

    @staticmethod
    def cached_archive(session: Session, task: DownloadTask) -> tuple[Archive, CacheEntry] | None:
        row = session.execute(
            select(Archive, CacheEntry)
            .join(CacheEntry, Archive.cache_sha1 == CacheEntry.sha1)
            .where(
                Archive.gid == task.gid,
                Archive.token == task.token,
                Archive.archive_type == task.archive_type,
                CacheEntry.expire_at > utcnow(),
            )
            .order_by(CacheEntry.created_at.desc())
        ).first()
        return (row[0], row[1]) if row else None
