import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta

from .auth_service import AuthService
from .config import Settings
from .database import Database
from .eh_client import EHClient, EHClientError, UnknownSubmission
from .models import DownloadTask, TaskStatus, utcnow
from .settings_store import SettingsStore


class DeliveryError(RuntimeError):
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class RemoteDelivery:
    task_id: str
    client: EHClient
    url: str
    filename: str
    max_bytes: int


def _safe_filename(value: str, limit: int = 180) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return (cleaned or "gallery")[:limit].rstrip(" .")


class DeliveryService:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        store: SettingsStore,
        auth: AuthService,
    ):
        self.settings = settings
        self.database = database
        self.store = store
        self.auth = auth

    def prepare_remote(self, session, task: DownloadTask) -> RemoteDelivery:
        archive = task.archive
        if (
            task.status not in {TaskStatus.WAITING_DOWNLOAD.value, TaskStatus.COMPLETED.value}
            or not archive
            or archive.cache_entry
            or not archive.remote_download_url
            or not archive.account
        ):
            raise DeliveryError("Archive is not ready for remote delivery", 409)
        if task.claimed_at is not None:
            raise DeliveryError("Another download is already active", 409)

        task.status = TaskStatus.DOWNLOADING.value
        task.claimed_at = utcnow()
        task.error = None
        task.wait_reason = None
        session.commit()

        client = EHClient(self.auth.get_cookie(archive.account), self.settings)
        url = archive.remote_download_url
        must_check = (
            task.download_count > 0
            or archive.remote_expires_at is None
            or archive.remote_expires_at <= utcnow()
        )
        if must_check:
            try:
                page = client.load_archive_page(archive.archiver_url)
            except EHClientError as exc:
                self._release(task.id, str(exc), failed=not exc.retryable)
                raise DeliveryError(str(exc), 503 if exc.retryable else 410) from exc
            form = page.forms.get(task.archive_type)
            unlocked = task.archive_type in page.unlocked_types or bool(
                form and form.cost_type == "unlocked"
            )
            if not unlocked and page.state != "ready":
                message = "Archive purchase is no longer valid; create the task again"
                self._release(task.id, message, failed=True)
                raise DeliveryError(message, 410)
            if page.state == "ready" and page.download_url:
                url = page.download_url
                self._update_remote_url(task.id, url)
            elif archive.remote_expires_at is None or archive.remote_expires_at <= utcnow():
                if not form or form.cost_type != "unlocked":
                    message = "Archive purchase is valid but EH did not provide a reusable download form"
                    self._release(task.id, message, failed=True)
                    raise DeliveryError(message, 410)
                try:
                    result = client.submit_archive(form)
                except UnknownSubmission as exc:
                    self._queue_remote_poll(task.id, str(exc))
                    raise DeliveryError("EH is preparing a new download URL; try again shortly", 409) from exc
                except EHClientError as exc:
                    self._release(task.id, str(exc), failed=not exc.retryable)
                    raise DeliveryError(str(exc), 503 if exc.retryable else 410) from exc
                if result.state != "ready" or not result.download_url:
                    self._queue_remote_poll(task.id, "EH is preparing a new download URL")
                    raise DeliveryError("EH is preparing a new download URL; try again shortly", 409)
                url = result.download_url
                self._update_remote_url(task.id, url)

        max_bytes = self.store.get_max_archive_size_mb(session) * 1024**2
        filename = (
            f"{task.gid} [{task.archive_type}] - "
            f"{_safe_filename(archive.title or f'Gallery {task.gid}')}.zip"
        )
        return RemoteDelivery(task.id, client, url, filename, max_bytes)

    def stream(self, delivery: RemoteDelivery) -> Iterator[bytes]:
        completed = False
        failure: str | None = None
        last_update = 0.0
        received = 0
        try:
            for chunk, total in delivery.client.iter_download(delivery.url, max_bytes=delivery.max_bytes):
                received += len(chunk)
                now = time.monotonic()
                if now - last_update >= 0.5:
                    last_update = now
                    self._progress(delivery.task_id, received, total)
                yield chunk
            completed = True
        except GeneratorExit:
            failure = "Download was interrupted"
            raise
        except Exception as exc:
            failure = str(exc)
            raise
        finally:
            with self.database.session_factory() as session:
                task = session.get(DownloadTask, delivery.task_id)
                if task:
                    task.claimed_at = None
                    task.downloaded_bytes = received
                    if completed:
                        task.download_count += 1
                        task.status = TaskStatus.WAITING_DOWNLOAD.value
                        task.progress = 100
                        task.wait_reason = "Archive is ready for another on-demand download"
                        task.error = None
                        task.completed_at = utcnow()
                    elif failure == "Download was interrupted":
                        task.status = TaskStatus.WAITING_DOWNLOAD.value
                        task.wait_reason = "Previous download was interrupted"
                        task.error = None
                    else:
                        task.status = TaskStatus.FAILED.value
                        task.error = failure or "Remote download failed"
                    session.commit()

    def _progress(self, task_id: str, received: int, total: int | None) -> None:
        with self.database.session_factory() as session:
            task = session.get(DownloadTask, task_id)
            if task:
                task.downloaded_bytes = received
                task.progress = min(99, int(received * 100 / total)) if total else 0
                session.commit()

    def _release(self, task_id: str, message: str, *, failed: bool) -> None:
        with self.database.session_factory() as session:
            task = session.get(DownloadTask, task_id)
            if task:
                task.claimed_at = None
                task.status = TaskStatus.FAILED.value if failed else TaskStatus.WAITING_DOWNLOAD.value
                task.error = message
                if task.archive and failed:
                    task.archive.status = TaskStatus.FAILED.value
                session.commit()

    def _update_remote_url(self, task_id: str, url: str) -> None:
        with self.database.session_factory() as session:
            task = session.get(DownloadTask, task_id)
            if task and task.archive:
                task.archive.remote_download_url = url
                task.archive.remote_expires_at = utcnow() + timedelta(days=7)
                session.commit()

    def _queue_remote_poll(self, task_id: str, message: str) -> None:
        with self.database.session_factory() as session:
            task = session.get(DownloadTask, task_id)
            if task and task.archive:
                task.claimed_at = None
                task.status = task.archive.status = TaskStatus.ARCHIVE_PENDING.value
                task.next_attempt_at = utcnow() + timedelta(seconds=30)
                task.wait_reason = message
                session.commit()
