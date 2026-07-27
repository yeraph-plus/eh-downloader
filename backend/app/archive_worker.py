import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

from sqlalchemy import or_, select, update

from .auth_service import AuthService
from .cache_service import CacheService
from .config import Settings
from .database import Database
from .eh_client import ArchiveForm, EHClient, EHClientError, GalleryRef, UnknownSubmission
from .models import (
    ACTIVE_TASK_STATUSES,
    Account,
    Archive,
    DownloadTask,
    TaskStatus,
    utcnow,
)
from .settings_store import SettingsStore
from .task_service import TaskService


RETRY_DELAYS = (30, 120, 600)


def safe_filename(value: str, limit: int = 180) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return (cleaned or "gallery")[:limit].rstrip(" .")


class ArchiveWorker:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        store: SettingsStore,
        auth: AuthService,
        cache: CacheService,
        tasks: TaskService,
    ):
        self.settings = settings
        self.database = database
        self.store = store
        self.auth = auth
        self.cache = cache
        self.tasks = tasks
        self._capacity_lock = threading.Lock()

    def run_forever(self) -> None:
        self.database.create_all()
        self.cache.root()
        while True:
            self.run_once()
            time.sleep(self.settings.worker_poll_seconds)

    def run_once(self) -> None:
        self.cache.root()
        self._recover_stale_claims()
        with self.database.session_factory() as cleanup_session:
            self.cache.cleanup(cleanup_session)
        with self.database.session_factory() as session:
            concurrency = self.store.get_worker_concurrency(session)
            ids = list(
                session.scalars(
                    select(DownloadTask.id)
                    .where(
                        DownloadTask.status.in_(ACTIVE_TASK_STATUSES),
                        or_(DownloadTask.next_attempt_at.is_(None), DownloadTask.next_attempt_at <= utcnow()),
                        DownloadTask.claimed_at.is_(None),
                    )
                    .order_by(DownloadTask.created_at.asc())
                    .limit(concurrency)
                )
            )
        if not ids:
            return
        with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="archive") as executor:
            list(executor.map(self._process_claimed, ids))

    def _process_claimed(self, task_id: str) -> None:
        with self.database.session_factory() as session:
            result = session.execute(
                update(DownloadTask)
                .where(DownloadTask.id == task_id, DownloadTask.claimed_at.is_(None))
                .values(claimed_at=utcnow())
            )
            session.commit()
            if result.rowcount != 1:
                return
        try:
            self._advance(task_id)
        except EHClientError as exc:
            self._handle_error(task_id, exc, retryable=exc.retryable)
        except Exception as exc:
            self._handle_error(task_id, exc, retryable=False)
        finally:
            with self.database.session_factory() as session:
                task = session.get(DownloadTask, task_id)
                if task:
                    task.claimed_at = None
                    session.commit()

    def _advance(self, task_id: str) -> None:
        with self.database.session_factory() as session:
            task = session.get(DownloadTask, task_id)
            if not task or task.status not in ACTIVE_TASK_STATUSES:
                return
            if task.expired_at <= utcnow():
                task.status = TaskStatus.EXPIRED.value
                task.error = "Task deadline expired before the archive became available"
                task.reserved_bytes = 0
                session.commit()
                return
            status = task.status
            if status == TaskStatus.QUEUED.value:
                task.status = (
                    TaskStatus.CHECKING_CACHE.value
                    if self.store.get_cache_enabled(session)
                    else TaskStatus.REQUESTING_ARCHIVE.value
                )
                task.wait_reason = None
                task.next_attempt_at = None
                session.commit()
                return
            if status == TaskStatus.CHECKING_CACHE.value:
                self._check_cache(session, task)
                return
            if status == TaskStatus.CACHED.value:
                task.status = TaskStatus.COMPLETED.value
                task.progress = 100
                task.reserved_bytes = 0
                task.completed_at = utcnow()
                session.commit()
                return
        if status == TaskStatus.REQUESTING_ARCHIVE.value:
            with self.database.session_factory() as session:
                has_remote_record = session.get(DownloadTask, task_id).archive_id is not None
            if has_remote_record:
                self._poll_archive(task_id)
            else:
                self._request_archive(task_id)
        elif status == TaskStatus.ARCHIVE_PENDING.value:
            self._poll_archive(task_id)
        elif status == TaskStatus.ARCHIVE_READY.value:
            self._download(task_id)
        elif status == TaskStatus.DOWNLOADING.value:
            self._download(task_id)
        elif status == TaskStatus.VERIFYING.value:
            self._verify(task_id)

    def _check_cache(self, session, task: DownloadTask) -> None:
        if not self.store.get_cache_enabled(session):
            task.status = TaskStatus.REQUESTING_ARCHIVE.value
            session.commit()
            return
        hit = self.tasks.cached_archive(session, task)
        if hit:
            archive, entry = hit
            try:
                path = self.cache.path_for(entry.zip_path)
            except ValueError:
                path = Path("__invalid__")
            if path.is_file():
                task.archive_id = archive.id
                task.status = TaskStatus.CACHED.value
                entry.last_accessed_at = utcnow()
                session.commit()
                return
        task.status = TaskStatus.REQUESTING_ARCHIVE.value
        session.commit()

    def _request_archive(self, task_id: str) -> None:
        with self.database.session_factory() as session:
            task = session.get(DownloadTask, task_id)
            accounts = self.auth.eligible_accounts(session)
            gallery = GalleryRef(task.source_host, task.gid, task.token)
        if not accounts:
            raise EHClientError("No enabled EH account is currently available", retryable=True)

        chosen: tuple[Account, EHClient, str, object, ArchiveForm] | None = None
        paid_fallback: tuple[Account, EHClient, str, object, ArchiveForm] | None = None
        last_error: Exception | None = None
        loaded_pages = 0
        found_requested_form = False
        for detached in accounts:
            try:
                client = EHClient(self.auth.get_cookie(detached), self.settings)
                archiver_url, page = client.get_archive_page(gallery)
                loaded_pages += 1
                self._record_account_page(detached.id, page.gp, page.credits)
                form = page.forms.get(task.archive_type)
                if not form:
                    continue
                found_requested_form = True
                if form.cost_type in {"free", "unlocked"}:
                    chosen = (detached, client, archiver_url, page, form)
                    break
                if form.cost_type == "gp":
                    paid_fallback = paid_fallback or (detached, client, archiver_url, page, form)
            except EHClientError as exc:
                last_error = exc
                self._record_account_error(detached.id, exc)
                continue
        chosen = chosen or paid_fallback
        if not chosen:
            if loaded_pages and not found_requested_form:
                label = "Resample" if task.archive_type == "resample" else "Original"
                raise EHClientError(f"{label} archive is not available for this gallery")
            raise EHClientError(str(last_error or "EH did not expose a usable archive form"))
        account, client, archiver_url, page, form = chosen

        if not self._create_archive_record(task_id, account.id, archiver_url, page, form):
            return
        try:
            result = client.submit_archive(form)
        except UnknownSubmission:
            with self.database.session_factory() as session:
                task = session.get(DownloadTask, task_id)
                task.status = TaskStatus.ARCHIVE_PENDING.value
                task.reconcile_count += 1
                task.next_attempt_at = utcnow() + timedelta(seconds=30)
                task.expired_at = utcnow() + timedelta(days=7)
                task.archive.status = TaskStatus.ARCHIVE_PENDING.value
                session.commit()
            return
        self._record_remote_result(task_id, result.state, result.download_url)

    def _create_archive_record(
        self, task_id: str, account_id: str, archiver_url: str, page, form: ArchiveForm,
    ) -> bool:
        with self._capacity_lock:
            with self.database.session_factory() as session:
                task = session.get(DownloadTask, task_id)
                db_account = session.get(Account, account_id)
                db_account.gp = page.gp
                db_account.credits = page.credits
                db_account.last_check = utcnow()
                db_account.last_used_at = utcnow()
                if form.estimated_size is None:
                    raise EHClientError(
                        "EH did not report an estimated archive size; the configured size limit cannot be enforced"
                    )
                max_archive_size_mb = self.store.get_max_archive_size_mb(session)
                max_archive_size = max_archive_size_mb * 1024**2
                if form.estimated_size > max_archive_size:
                    estimated_mb = form.estimated_size / 1024**2
                    raise EHClientError(
                        f"Estimated archive size {estimated_mb:.1f} MB exceeds the {max_archive_size_mb} MB limit"
                    )
                required = form.estimated_size
                cache_enabled = self.store.get_cache_enabled(session)
                if cache_enabled and required:
                    reservations = session.execute(
                        select(DownloadTask.reserved_bytes, DownloadTask.downloaded_bytes).where(
                            DownloadTask.id != task.id,
                            DownloadTask.status.in_(ACTIVE_TASK_STATUSES),
                        )
                    ).all()
                    remaining = sum(max(0, reserved - downloaded) for reserved, downloaded in reservations)
                    if remaining + required > self.cache.available_bytes(session):
                        task.status = TaskStatus.QUEUED.value
                        task.wait_reason = "Waiting for cache capacity"
                        task.next_attempt_at = utcnow() + timedelta(minutes=1)
                        task.reserved_bytes = 0
                        session.commit()
                        return False
                archive = Archive(
                    gid=task.gid,
                    token=task.token,
                    source_host=task.source_host,
                    archive_type=task.archive_type,
                    status=TaskStatus.REQUESTING_ARCHIVE.value,
                    title=page.title,
                    estimated_size=form.estimated_size,
                    cost=form.cost,
                    cost_type=form.cost_type,
                    owner=db_account.id,
                    archiver_url=archiver_url,
                    cache_enabled=cache_enabled,
                )
                session.add(archive)
                session.flush()
                task.archive_id = archive.id
                task.reserved_bytes = required if cache_enabled else 0
                task.error = None
                session.commit()
                return True

    def _poll_archive(self, task_id: str) -> None:
        with self.database.session_factory() as session:
            task = session.get(DownloadTask, task_id)
            archive = task.archive
            account = archive.account
            client = EHClient(self.auth.get_cookie(account), self.settings)
            archiver_url = archive.archiver_url
        page = client.load_archive_page(archiver_url)
        if page.state in {"quote", "confirmation"}:
            with self.database.session_factory() as session:
                task = session.get(DownloadTask, task_id)
                if task.reconcile_count >= 3:
                    raise EHClientError(
                        "EH never confirmed archive creation after three safe reconciliation rounds"
                    )
                task.reconcile_count += 1
                session.commit()
            form = page.forms.get(task.archive_type)
            if not form:
                raise EHClientError("Requested archive form disappeared during reconciliation")
            try:
                page = client.submit_archive(form)
            except UnknownSubmission:
                with self.database.session_factory() as session:
                    task = session.get(DownloadTask, task_id)
                    task.next_attempt_at = utcnow() + timedelta(seconds=30)
                    session.commit()
                return
        self._record_remote_result(task_id, page.state, page.download_url)

    def _record_remote_result(self, task_id: str, state: str, download_url: str | None) -> None:
        with self.database.session_factory() as session:
            task = session.get(DownloadTask, task_id)
            archive = task.archive
            task.expired_at = utcnow() + timedelta(days=7)
            task.next_attempt_at = None
            if state == "ready" and download_url:
                archive.remote_download_url = download_url
                archive.remote_expires_at = task.expired_at
                self._record_account_download(archive)
                archive.cache_enabled = self.store.get_cache_enabled(session)
                if archive.cache_enabled:
                    task.status = archive.status = TaskStatus.ARCHIVE_READY.value
                else:
                    task.status = archive.status = TaskStatus.WAITING_DOWNLOAD.value
                    task.wait_reason = "Archive is ready for on-demand download"
                    task.progress = 0
                    task.reserved_bytes = 0
            elif state == "pending":
                task.status = archive.status = TaskStatus.ARCHIVE_PENDING.value
                task.next_attempt_at = utcnow() + timedelta(seconds=30)
            else:
                raise EHClientError("EH archive response did not expose a pending state or download URL", retryable=True)
            session.commit()

    def _download(self, task_id: str) -> None:
        partial = self.cache.path_for(f".{task_id}.download")
        with self.database.session_factory() as session:
            task = session.get(DownloadTask, task_id)
            archive = task.archive
            if not archive.remote_download_url:
                raise EHClientError("Archive download URL is missing")
            client = EHClient(self.auth.get_cookie(archive.account), self.settings)
            max_bytes = self.store.get_max_archive_size_mb(session) * 1024**2
            task.status = archive.status = TaskStatus.DOWNLOADING.value
            task.error = None
            session.commit()

        last_update = 0.0

        def progress(written: int, total: int | None) -> None:
            nonlocal last_update
            now = time.monotonic()
            if now - last_update < 0.5 and total and written < total:
                return
            last_update = now
            with self.database.session_factory() as session:
                task = session.get(DownloadTask, task_id)
                task.downloaded_bytes = written
                task.progress = min(99, int(written * 100 / total)) if total else 0
                session.commit()

        client.download(archive.remote_download_url, partial, progress, max_bytes=max_bytes)
        with self.database.session_factory() as session:
            task = session.get(DownloadTask, task_id)
            task.status = task.archive.status = TaskStatus.VERIFYING.value
            task.next_attempt_at = None
            session.commit()

    def _verify(self, task_id: str) -> None:
        partial = self.cache.path_for(f".{task_id}.download")
        if not partial.is_file():
            raise EHClientError("Temporary archive file is missing", retryable=True)
        sha1 = self.cache.verify_zip(partial)
        with self.database.session_factory() as session:
            task = session.get(DownloadTask, task_id)
            archive = task.archive
            filename = f"{task.gid} [{task.archive_type}] - {safe_filename(archive.title or f'Gallery {task.gid}')}.zip"
            entry = self.cache.commit(session, partial, sha1, filename)
            archive.cache_sha1 = entry.sha1
            archive.status = TaskStatus.CACHED.value
            self._mark_account_success(archive.account)
            task.status = TaskStatus.CACHED.value
            task.progress = 100
            task.error = None
            session.commit()

    def _handle_error(self, task_id: str, exc: Exception, *, retryable: bool) -> None:
        with self.database.session_factory() as session:
            task = session.get(DownloadTask, task_id)
            if not task or task.status not in ACTIVE_TASK_STATUSES:
                return
            task.error = str(exc)
            account = task.archive.account if task.archive else None
            if account:
                account.last_check = utcnow()
                account.last_error = str(exc)
                if isinstance(exc, EHClientError):
                    if exc.authentication:
                        account.enabled = False
                    elif exc.retryable:
                        account.cooldown_reason = str(exc)
                        account.cooldown_until = utcnow() + timedelta(seconds=self.settings.account_cooldown_seconds)
            if retryable and task.retry_count < 3:
                task.next_attempt_at = utcnow() + timedelta(seconds=RETRY_DELAYS[task.retry_count])
                task.retry_count += 1
            else:
                task.status = TaskStatus.FAILED.value
                task.reserved_bytes = 0
            session.commit()

    @staticmethod
    def _record_account_download(archive: Archive | None) -> None:
        if not archive or not archive.account:
            return
        account = archive.account
        account.last_download_cost = archive.cost
        account.last_download_cost_type = archive.cost_type
        account.last_used_at = utcnow()
        ArchiveWorker._mark_account_success(account)

    @staticmethod
    def _mark_account_success(account: Account | None) -> None:
        if not account:
            return
        account.last_check = utcnow()
        account.last_error = None
        account.cooldown_reason = None
        account.cooldown_until = None

    def _recover_stale_claims(self) -> None:
        cutoff = utcnow() - timedelta(seconds=self.settings.worker_stale_seconds)
        with self.database.session_factory() as session:
            session.execute(
                update(DownloadTask)
                .where(DownloadTask.claimed_at.is_not(None), DownloadTask.claimed_at < cutoff)
                .values(claimed_at=None)
            )
            session.commit()

    def _record_account_page(self, account_id: str, gp: int | None, credits: int | None) -> None:
        with self.database.session_factory() as session:
            account = session.get(Account, account_id)
            if not account:
                return
            account.gp = gp
            account.credits = credits
            account.last_check = utcnow()
            account.last_used_at = utcnow()
            account.last_error = None
            account.cooldown_reason = None
            account.cooldown_until = None
            session.commit()

    def _record_account_error(self, account_id: str, exc: EHClientError) -> None:
        with self.database.session_factory() as session:
            account = session.get(Account, account_id)
            if not account:
                return
            account.last_error = str(exc)
            if exc.authentication:
                account.enabled = False
            elif exc.retryable:
                account.cooldown_reason = str(exc)
                account.cooldown_until = utcnow() + timedelta(seconds=self.settings.account_cooldown_seconds)
            session.commit()
