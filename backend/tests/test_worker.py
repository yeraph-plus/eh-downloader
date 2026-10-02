import zipfile
from datetime import timedelta

from app.archive_worker import ArchiveWorker
from app.auth_service import AuthService
from app.cache_service import CacheService
from app.database import Database
from app.eh_client import ArchiveForm, ArchivePage, EHClientError
from app.models import Account, AppSetting, Archive, CacheEntry, DownloadTask, RequesterType, TaskStatus, utcnow
from app.security import SecurityManager
from app.settings_store import SettingsStore
from app.task_service import TaskService


def make_worker(settings):
    database = Database(settings)
    database.create_all()
    security = SecurityManager(settings)
    store = SettingsStore(settings)
    worker = ArchiveWorker(
        settings,
        database,
        store,
        AuthService(settings, security, store),
        CacheService(settings, store),
        TaskService(settings),
    )
    return database, security, worker


def add_task(session, status=TaskStatus.CHECKING_CACHE.value, gid=12345):
    task = DownloadTask(
        gallery_url=f"https://e-hentai.org/g/{gid}/abcdef0123/",
        gid=gid,
        token="abcdef0123",
        source_host="e-hentai.org",
        archive_type="resample",
        requester_type=RequesterType.ADMIN.value,
        requester_id="admin",
        status=status,
        expired_at=utcnow() + timedelta(days=1),
    )
    session.add(task)
    session.flush()
    return task


def test_worker_reuses_valid_cache(test_settings):
    database, _, worker = make_worker(test_settings)
    cache_path = test_settings.cache_dir / "cached.zip"
    cache_path.parent.mkdir(parents=True)
    with zipfile.ZipFile(cache_path, "w") as archive_file:
        archive_file.writestr("001.jpg", b"image")
    with database.session_factory() as session:
        entry = CacheEntry(
            sha1="b" * 40,
            zip_path="cached.zip",
            filename="cached.zip",
            filesize=cache_path.stat().st_size,
            expire_at=utcnow() + timedelta(days=7),
        )
        archive = Archive(
            gid=12345, token="abcdef0123", source_host="e-hentai.org", archive_type="resample",
            status=TaskStatus.COMPLETED.value, cache_sha1=entry.sha1,
        )
        task = add_task(session)
        session.add_all([entry, archive])
        task_id = task.id
        session.commit()
    worker.run_once()
    worker.run_once()
    with database.session_factory() as session:
        task = session.get(DownloadTask, task_id)
        assert task.status == TaskStatus.COMPLETED.value
        assert task.archive.cache_sha1 == "b" * 40


def test_worker_verifies_zip_and_atomically_caches(test_settings):
    database, security, worker = make_worker(test_settings)
    with database.session_factory() as session:
        account = Account(
            name="account",
            encrypted_cookie=security.encrypt("ipb_member_id=1; ipb_pass_hash=x"),
            cookie_fingerprint="c" * 64,
            last_check=utcnow(),
        )
        session.add(account)
        session.flush()
        archive = Archive(
            gid=55555, token="abcdef0123", source_host="e-hentai.org", archive_type="resample",
            status=TaskStatus.VERIFYING.value, title="Test Gallery", owner=account.id,
        )
        task = add_task(session, TaskStatus.VERIFYING.value, gid=55555)
        session.add(archive)
        session.flush()
        task.archive_id = archive.id
        task_id = task.id
        session.commit()
    partial = test_settings.cache_dir / f".{task_id}.download"
    partial.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(partial, "w") as archive_file:
        archive_file.writestr("001.jpg", b"image-data")
    worker.run_once()
    worker.run_once()
    with database.session_factory() as session:
        task = session.get(DownloadTask, task_id)
        assert task.status == TaskStatus.COMPLETED.value
        assert task.archive.cache_sha1
        entry = session.get(CacheEntry, task.archive.cache_sha1)
        assert (test_settings.cache_dir / entry.zip_path).is_file()
        assert not partial.exists()


def test_cleanup_removes_expired_cache_and_invalid_task_records(test_settings):
    database, _, worker = make_worker(test_settings)
    cache_path = test_settings.cache_dir / "expired.zip"
    cache_path.parent.mkdir(parents=True)
    cache_path.write_bytes(b"old")
    with database.session_factory() as session:
        entry = CacheEntry(
            sha1="d" * 40, zip_path="expired.zip", filename="expired.zip", filesize=3,
            expire_at=utcnow() - timedelta(seconds=1),
        )
        archive = Archive(
            gid=77777, token="abcdef0123", source_host="e-hentai.org", archive_type="resample",
            status=TaskStatus.COMPLETED.value, cache_sha1=entry.sha1,
        )
        task = add_task(session, TaskStatus.COMPLETED.value, gid=77777)
        session.add_all([entry, archive])
        session.flush()
        task.archive_id = archive.id
        task_id = task.id
        session.commit()
    worker.run_once()
    with database.session_factory() as session:
        assert session.get(DownloadTask, task_id) is None
        assert session.get(CacheEntry, "d" * 40) is None
    assert not cache_path.exists()


def test_zero_retention_keeps_cache_permanently(test_settings):
    database, _, worker = make_worker(test_settings)
    partial = test_settings.cache_dir / "permanent.partial"
    partial.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(partial, "w") as archive_file:
        archive_file.writestr("001.jpg", b"image")
    with database.session_factory() as session:
        session.add(AppSetting(key="retention_days", value="0"))
        sha1 = worker.cache.verify_zip(partial)
        entry = worker.cache.commit(session, partial, sha1, "permanent.zip")
        session.commit()
        assert entry.expire_at.year == 9999
        cached_path = worker.cache.path_for(entry.zip_path)

    with database.session_factory() as session:
        worker.cache.cleanup(session)

    with database.session_factory() as session:
        assert session.get(CacheEntry, sha1) is not None
    assert cached_path.is_file()


def test_capacity_waits_until_expired_cache_is_cleaned(test_settings):
    database, security, worker = make_worker(test_settings)
    occupied = test_settings.cache_dir / "occupied.zip"
    occupied.parent.mkdir(parents=True, exist_ok=True)
    occupied.write_bytes(b"x" * 900)
    with database.session_factory() as session:
        session.add(AppSetting(key="cache_limit_bytes", value="1000"))
        account = Account(
            name="capacity-account",
            encrypted_cookie=security.encrypt("ipb_member_id=1; ipb_pass_hash=x"),
            cookie_fingerprint="6" * 64,
        )
        entry = CacheEntry(
            sha1="6" * 40, zip_path="occupied.zip", filename="occupied.zip",
            filesize=900, expire_at=utcnow() + timedelta(days=1),
        )
        task = add_task(session, TaskStatus.REQUESTING_ARCHIVE.value, gid=78000)
        session.add_all([account, entry])
        session.flush()
        account_id = account.id
        task_id = task.id
        session.commit()

    page = ArchivePage("Capacity", 1000, 2000, {}, "quote", None, "Quote")
    form = ArchiveForm(
        "resample", "https://e-hentai.org/archiver.php", {"dltype": "res"},
        200, 0, "free", False,
    )
    assert worker._create_archive_record(
        task_id, account_id, "https://e-hentai.org/archiver.php", page, form
    ) is False
    with database.session_factory() as session:
        task = session.get(DownloadTask, task_id)
        assert task.status == TaskStatus.QUEUED.value
        assert task.wait_reason == "Waiting for cache capacity"
        assert task.archive_id is None
        session.get(CacheEntry, "6" * 40).expire_at = utcnow() - timedelta(seconds=1)
        session.commit()

    with database.session_factory() as session:
        worker.cache.cleanup(session)
    assert not occupied.exists()
    assert worker._create_archive_record(
        task_id, account_id, "https://e-hentai.org/archiver.php", page, form
    ) is True


def test_cleanup_removes_failed_download_temporary_files(test_settings):
    database, _, worker = make_worker(test_settings)
    with database.session_factory() as session:
        task = add_task(session, TaskStatus.FAILED.value, gid=77888)
        task_id = task.id
        session.commit()
    partial = test_settings.cache_dir / f".{task_id}.download"
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial.write_bytes(b"failed-download")

    worker.run_once()

    assert not partial.exists()


def test_worker_error_updates_assigned_account_status(test_settings):
    database, security, worker = make_worker(test_settings)
    with database.session_factory() as session:
        account = Account(
            name="expired-cookie",
            encrypted_cookie=security.encrypt("ipb_member_id=1; ipb_pass_hash=x"),
            cookie_fingerprint="7" * 64,
        )
        session.add(account)
        session.flush()
        archive = Archive(
            gid=77999, token="abcdef0123", source_host="e-hentai.org", archive_type="resample",
            status=TaskStatus.DOWNLOADING.value, owner=account.id,
        )
        task = add_task(session, TaskStatus.DOWNLOADING.value, gid=77999)
        session.add(archive)
        session.flush()
        task.archive_id = archive.id
        task_id = task.id
        account_id = account.id
        session.commit()

    worker._handle_error(
        task_id,
        EHClientError("EH Cookie is not authenticated", authentication=True),
        retryable=False,
    )

    with database.session_factory() as session:
        task = session.get(DownloadTask, task_id)
        account = session.get(Account, account_id)
        assert task.status == TaskStatus.FAILED.value
        assert account.enabled is False
        assert account.last_error == "EH Cookie is not authenticated"
        assert account.last_check is not None


def test_retryable_error_backs_off_until_setting_cap(test_settings):
    database, _, worker = make_worker(test_settings)
    with database.session_factory() as session:
        task = add_task(session)
        task_id = task.id
        session.commit()

    expected = (30, 120, 600)
    for attempt, delay in enumerate(expected):
        worker._handle_error(task_id, EHClientError("EH archive page request failed: boom", retryable=True), retryable=True)
        with database.session_factory() as session:
            task = session.get(DownloadTask, task_id)
            assert task.status == TaskStatus.QUEUED.value or task.status in {
                TaskStatus.CHECKING_CACHE.value,
                TaskStatus.REQUESTING_ARCHIVE.value,
            }
            assert task.retry_count == attempt + 1
            wait = (task.next_attempt_at - utcnow()).total_seconds()
            assert delay - 5 <= wait <= delay + 5, f"attempt {attempt}: wait={wait}"
            assert task.error == "EH archive page request failed: boom"

    worker._handle_error(task_id, EHClientError("EH archive page request failed: boom", retryable=True), retryable=True)
    with database.session_factory() as session:
        task = session.get(DownloadTask, task_id)
        assert task.status == TaskStatus.FAILED.value


def test_zero_retry_setting_fails_immediately(test_settings):
    database, _, worker = make_worker(test_settings)
    with database.session_factory() as session:
        session.add(AppSetting(key="task_max_retries", value="0"))
        task = add_task(session)
        task_id = task.id
        session.commit()

    worker._handle_error(task_id, EHClientError("EH download failed: boom", retryable=True), retryable=True)
    with database.session_factory() as session:
        task = session.get(DownloadTask, task_id)
        assert task.status == TaskStatus.FAILED.value
        assert task.next_attempt_at is None


def test_garbage_retry_setting_falls_back_to_default(test_settings):
    database, _, worker = make_worker(test_settings)
    with database.session_factory() as session:
        session.add(AppSetting(key="task_max_retries", value="not-a-number"))
        task = add_task(session)
        task_id = task.id
        session.commit()

    worker._handle_error(task_id, EHClientError("boom", retryable=True), retryable=True)
    with database.session_factory() as session:
        task = session.get(DownloadTask, task_id)
        assert task.retry_count == 1
        assert task.status != TaskStatus.FAILED.value


def test_disabled_cache_skips_existing_cache_entry(test_settings):
    database, _, worker = make_worker(test_settings)
    cache_path = test_settings.cache_dir / "cached.zip"
    cache_path.parent.mkdir(parents=True)
    with zipfile.ZipFile(cache_path, "w") as archive_file:
        archive_file.writestr("001.jpg", b"image")
    with database.session_factory() as session:
        session.add(AppSetting(key="cache_enabled", value="false"))
        entry = CacheEntry(
            sha1="e" * 40, zip_path="cached.zip", filename="cached.zip", filesize=cache_path.stat().st_size,
            expire_at=utcnow() + timedelta(days=7),
        )
        archive = Archive(
            gid=88888, token="abcdef0123", source_host="e-hentai.org", archive_type="resample",
            status=TaskStatus.COMPLETED.value, cache_sha1=entry.sha1,
        )
        task = add_task(session, TaskStatus.CHECKING_CACHE.value, gid=88888)
        session.add_all([entry, archive])
        task_id = task.id
        session.commit()
    worker.run_once()
    with database.session_factory() as session:
        task = session.get(DownloadTask, task_id)
        assert task.status == TaskStatus.REQUESTING_ARCHIVE.value
        assert task.archive_id is None


def test_disabled_cache_skips_cache_state_from_queue(test_settings):
    database, _, worker = make_worker(test_settings)
    with database.session_factory() as session:
        session.add(AppSetting(key="cache_enabled", value="false"))
        task = add_task(session, TaskStatus.QUEUED.value, gid=88889)
        task_id = task.id
        session.commit()

    worker.run_once()

    with database.session_factory() as session:
        assert session.get(DownloadTask, task_id).status == TaskStatus.REQUESTING_ARCHIVE.value


def test_disabled_cache_waits_for_click_after_remote_archive_is_ready(test_settings):
    database, security, worker = make_worker(test_settings)
    with database.session_factory() as session:
        session.add(AppSetting(key="cache_enabled", value="false"))
        account = Account(
            name="on-demand", encrypted_cookie=security.encrypt("ipb_member_id=1; ipb_pass_hash=x"),
            cookie_fingerprint="3" * 64,
        )
        session.add(account)
        session.flush()
        archive = Archive(
            gid=88999, token="abcdef0123", source_host="e-hentai.org", archive_type="resample",
            status=TaskStatus.ARCHIVE_PENDING.value, title="On demand", owner=account.id,
            archiver_url="https://e-hentai.org/archiver.php?gid=88999&token=abcdef0123",
            cache_enabled=False,
        )
        task = add_task(session, TaskStatus.ARCHIVE_PENDING.value, gid=88999)
        session.add(archive)
        session.flush()
        task.archive_id = archive.id
        task_id = task.id
        session.commit()

    worker._record_remote_result(
        task_id, "ready", "https://e-hentai.org/archive/on-demand?start=1"
    )

    with database.session_factory() as session:
        task = session.get(DownloadTask, task_id)
        assert task.status == TaskStatus.WAITING_DOWNLOAD.value
        assert task.archive.status == TaskStatus.WAITING_DOWNLOAD.value
        assert task.download_count == 0
        assert task.archive.cache_sha1 is None


def test_missing_resample_form_has_specific_error(test_settings, monkeypatch):
    database, security, worker = make_worker(test_settings)
    with database.session_factory() as session:
        session.add(Account(
            name="account", encrypted_cookie=security.encrypt("ipb_member_id=1; ipb_pass_hash=x"),
            cookie_fingerprint="f" * 64,
        ))
        task = add_task(session, TaskStatus.REQUESTING_ARCHIVE.value, gid=99999)
        task_id = task.id
        session.commit()

    page = ArchivePage(
        title="Small gallery", gp=1000, credits=2000,
        forms={"original": ArchiveForm("original", "https://e-hentai.org/archiver.php", {"dltype": "org"}, 100, 0, "free", False)},
        state="quote", download_url=None, message="Original only",
    )

    class FakeEHClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def get_archive_page(self, _gallery):
            return "https://e-hentai.org/archiver.php?gid=99999&token=abcdef0123", page

    monkeypatch.setattr("app.archive_worker.EHClient", FakeEHClient)
    worker.run_once()
    with database.session_factory() as session:
        task = session.get(DownloadTask, task_id)
        assert task.status == TaskStatus.FAILED.value
        assert task.error == "Resample archive is not available for this gallery"


def test_archive_size_limit_fails_before_remote_submission(test_settings, monkeypatch):
    database, security, worker = make_worker(test_settings)
    with database.session_factory() as session:
        session.add(Account(
            name="limited", encrypted_cookie=security.encrypt("ipb_member_id=1; ipb_pass_hash=x"),
            cookie_fingerprint="1" * 64,
        ))
        session.add(AppSetting(key="max_archive_size_mb", value="100"))
        task = add_task(session, TaskStatus.REQUESTING_ARCHIVE.value, gid=123456)
        task_id = task.id
        session.commit()

    submitted = False
    page = ArchivePage(
        title="Large gallery", gp=0, credits=999999,
        forms={"resample": ArchiveForm(
            "resample", "https://e-hentai.org/archiver.php", {"dltype": "res"},
            101 * 1024**2, 500, "gp", False,
        )},
        state="quote", download_url=None, message="Quote",
    )

    class FakeEHClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def get_archive_page(self, _gallery):
            return "https://e-hentai.org/archiver.php?gid=123456&token=abcdef0123", page

        def submit_archive(self, _form):
            nonlocal submitted
            submitted = True
            raise AssertionError("size limit must be checked before submission")

    monkeypatch.setattr("app.archive_worker.EHClient", FakeEHClient)
    worker.run_once()
    with database.session_factory() as session:
        task = session.get(DownloadTask, task_id)
        assert task.status == TaskStatus.FAILED.value
        assert task.error == "Estimated archive size 101.0 MB exceeds the 100 MB limit"
        assert task.archive_id is None
    assert submitted is False


def test_paid_archive_is_left_to_eh_for_credit_conversion(test_settings, monkeypatch):
    database, security, worker = make_worker(test_settings)
    with database.session_factory() as session:
        account = Account(
            name="credits", encrypted_cookie=security.encrypt("ipb_member_id=1; ipb_pass_hash=x"),
            cookie_fingerprint="2" * 64,
        )
        session.add(account)
        task = add_task(session, TaskStatus.REQUESTING_ARCHIVE.value, gid=234567)
        task_id = task.id
        session.commit()

    page = ArchivePage(
        title="Paid gallery", gp=0, credits=50000,
        forms={"resample": ArchiveForm(
            "resample", "https://e-hentai.org/archiver.php", {"dltype": "res", "dlcheck": "Download"},
            20 * 1024**2, 1000, "gp", False,
        )},
        state="quote", download_url=None, message="Quote",
    )
    submitted = False

    class FakeEHClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def get_archive_page(self, _gallery):
            return "https://e-hentai.org/archiver.php?gid=234567&token=abcdef0123", page

        def submit_archive(self, _form):
            nonlocal submitted
            submitted = True
            return ArchivePage(
                title="Paid gallery", gp=0, credits=50000, forms={}, state="ready",
                download_url="https://e-hentai.org/archive/file?start=1", message="Ready",
            )

    monkeypatch.setattr("app.archive_worker.EHClient", FakeEHClient)
    worker.run_once()
    with database.session_factory() as session:
        task = session.get(DownloadTask, task_id)
        assert task.status == TaskStatus.ARCHIVE_READY.value
        assert task.archive.cost == 1000
        assert task.archive.cost_type == "gp"
        assert task.archive.account.gp == 0
        assert task.archive.account.credits == 50000
        assert task.archive.account.last_download_cost == 1000
    assert submitted is True
