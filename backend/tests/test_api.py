from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import create_app
from app.models import Account, AppSetting, Archive, CacheEntry, DownloadTask, TaskStatus, utcnow
from app.settings_store import SettingsStore


def login(client: TestClient) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password-123"})
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def create_task(client: TestClient, headers: dict[str, str], gid: int, archive_type: str = "resample") -> dict:
    response = client.post(
        "/api/v1/tasks",
        json={"gallery_urls": f"https://e-hentai.org/g/{gid}/abcdef0123/", "archive_type": archive_type},
        headers=headers,
    )
    assert response.status_code == 202
    return response.json()[0]


def test_settings_roundtrips_proxy_and_retry_cap(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        headers = login(client)
        current = client.get("/api/v1/settings").json()
        assert current["eh_proxy_url"] == ""
        assert current["task_max_retries"] == 3

        current["eh_proxy_url"] = "socks5://127.0.0.1:1080"
        current["task_max_retries"] = 5
        saved = client.put("/api/v1/settings", json=current, headers=headers)
        assert saved.json()["eh_proxy_url"] == "socks5://127.0.0.1:1080"
        assert saved.json()["task_max_retries"] == 5

        # A non-proxy scheme is dropped to empty (direct connection).
        current["eh_proxy_url"] = "ftp://example.com:21"
        saved = client.put("/api/v1/settings", json=current, headers=headers)
        assert saved.json()["eh_proxy_url"] == ""

        rejected = client.put("/api/v1/settings", json={**current, "task_max_retries": 6}, headers=headers)
        assert rejected.status_code == 422


def test_stats_endpoint_gates_and_aggregates(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        # admin mode is the default: the public stats surface is closed.
        assert client.get("/api/v1/stats").status_code == 401
        headers = login(client)
        stats = client.get("/api/v1/stats", headers=headers).json()
        assert stats["worker_alive"] is False
        assert stats["worker_last_beat_at"] is None
        assert stats["downloads"]["served_total"] == 0
        assert stats["credits"]["total_spent"] == 0

        with app.state.database.session_factory() as session:
            session.add(Account(name="pool-a", encrypted_cookie="x", cookie_fingerprint="fa", gp=1200, credits=30, enabled=True))
            session.add(Account(name="pool-b", encrypted_cookie="y", cookie_fingerprint="fb", enabled=False))
            session.add(CacheEntry(sha1="c" * 40, zip_path="ready.zip", filename="ready.zip", filesize=500, expire_at=utcnow() + timedelta(days=1)))
            session.commit()

        stats = client.get("/api/v1/stats", headers=headers).json()
        assert stats["eh_pool"]["gp"] == 1200
        assert stats["eh_pool"]["credits"] == 30
        assert stats["eh_pool"]["accounts_ready"] == 1
        assert stats["eh_pool"]["accounts_total"] == 2
        assert stats["downloads"]["archives_ready"] == 1
        assert stats["traffic"]["cache_stored_bytes"] == 500

        # Guest mode keeps the stats desk public like the task list.
        with app.state.database.session_factory() as session:
            session.add(AppSetting(key="access_mode", value="guest"))
            session.commit()
        assert client.get("/api/v1/stats").status_code == 200


def test_counter_bump_survives_garbage_and_is_atomic(test_settings):
    app = create_app(test_settings)
    store = SettingsStore(test_settings)
    with TestClient(app):
        with app.state.database.session_factory() as session:
            session.add(AppSetting(key="stats_credit_spent", value="not-a-number"))
            session.commit()
            store.bump_counter(session, "stats_credit_spent", 5)
            session.commit()
            store.bump_counter(session, "stats_credit_spent", 7)
            session.commit()
            assert store.get_counter(session, "stats_credit_spent") == 12
            # Last-writer-wins upsert for the heartbeat-style keys.
            store.upsert_value(session, "stats_worker_beat", "1700000000")
            session.commit()
            assert store.get_counter(session, "stats_worker_beat") == 1700000000


def test_batch_task_creation_is_atomic(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        headers = login(client)
        response = client.post(
            "/api/v1/tasks",
            json={
                "gallery_urls": "\nhttps://e-hentai.org/g/12345/abcdef0123/\n\nhttps://exhentai.org/g/67890/abcdef0123/\n",
                "archive_type": "original",
            },
            headers=headers,
        )
        assert response.status_code == 202
        assert [row["gid"] for row in response.json()] == [12345, 67890]
        assert all(row["status"] == "queued" for row in response.json())

        invalid = client.post(
            "/api/v1/tasks",
            json={
                "gallery_urls": "https://e-hentai.org/g/11111/abcdef0123/\nhttps://example.com/not-a-gallery",
                "archive_type": "resample",
            },
            headers=headers,
        )
        assert invalid.status_code == 400
        assert invalid.json()["detail"].startswith("Line 2:")
        with app.state.database.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(DownloadTask)) == 2


def test_task_creation_deduplicates_eh_and_exh_but_not_archive_type(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        headers = login(client)
        first = client.post(
            "/api/v1/tasks",
            json={
                "gallery_urls": (
                    "https://e-hentai.org/g/13579/abcdef0123/\n"
                    "https://exhentai.org/g/13579/abcdef0123/"
                ),
                "archive_type": "resample",
            },
            headers=headers,
        )
        assert first.status_code == 202
        assert len(first.json()) == 1

        same = client.post(
            "/api/v1/tasks",
            json={"gallery_urls": "https://e-hentai.org/g/13579/abcdef0123/", "archive_type": "resample"},
            headers=headers,
        )
        original = create_task(client, headers, 13579, "original")
        assert same.status_code == 202
        assert same.json() == []
        assert original["id"] != first.json()[0]["id"]
        with app.state.database.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(DownloadTask)) == 2


def test_expired_unique_task_is_requeued_in_place(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        headers = login(client)
        original = create_task(client, headers, 24680)
        with app.state.database.session_factory() as session:
            task = session.get(DownloadTask, original["id"])
            task.status = TaskStatus.EXPIRED.value
            task.error = "Remote archive download window expired"
            task.progress = 100
            session.commit()

        response = client.post(
            "/api/v1/tasks",
            json={
                "gallery_urls": "https://exhentai.org/g/24680/abcdef0123/",
                "archive_type": "resample",
            },
            headers=headers,
        )
        assert response.status_code == 202
        assert len(response.json()) == 1
        revived = response.json()[0]
        assert revived["id"] == original["id"]
        assert revived["status"] == TaskStatus.QUEUED.value
        assert revived["progress"] == 0
        assert revived["error"] is None
        with app.state.database.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(DownloadTask)) == 1


def test_account_import_does_not_contact_eh(test_settings, monkeypatch):
    app = create_app(test_settings)
    monkeypatch.setattr(
        app.state.auth_service,
        "validate_account",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("import must not contact EH")),
    )
    cookies_txt = (
        ".e-hentai.org\tTRUE\t/\tTRUE\t0\tipb_member_id\t123\n"
        ".e-hentai.org\tTRUE\t/\tTRUE\t0\tipb_pass_hash\tsecret\n"
    )
    with TestClient(app) as client:
        headers = login(client)
        response = client.post(
            "/api/v1/accounts",
            json={"name": "offline-import", "cookies_txt": cookies_txt, "priority": 100},
            headers=headers,
        )
        assert response.status_code == 201
        assert response.json()["name"] == "offline-import"


def test_public_guest_download_modes_share_one_identity(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as admin:
        headers = login(admin)
        current = admin.get("/api/v1/settings").json()
        current["access_mode"] = "guest"
        current["guest_download_mode"] = "resample"
        assert admin.put("/api/v1/settings", json=current, headers=headers).status_code == 200

    with TestClient(app) as guest_one:
        state = guest_one.get("/api/v1/auth/session").json()
        assert state["admin"] is False
        assert state["mode"] == "guest"
        forbidden = guest_one.post(
            "/api/v1/tasks",
            json={"gallery_urls": "https://e-hentai.org/g/22222/abcdef0123/", "archive_type": "original"},
        )
        assert forbidden.status_code == 403
        accepted = guest_one.post(
            "/api/v1/tasks",
            json={"gallery_urls": "https://e-hentai.org/g/22222/abcdef0123/", "archive_type": "resample"},
        )
        assert accepted.status_code == 202
        task_id = accepted.json()[0]["id"]

    with TestClient(app) as guest_two:
        tasks = guest_two.get("/api/v1/tasks").json()
        assert [task["id"] for task in tasks] == [task_id]
        assert guest_two.get(f"/api/v1/tasks/{task_id}").status_code == 200

    with TestClient(app) as admin:
        headers = login(admin)
        current = admin.get("/api/v1/settings").json()
        current["guest_download_mode"] = "original"
        admin.put("/api/v1/settings", json=current, headers=headers)
    with TestClient(app) as guest:
        assert [task["id"] for task in guest.get("/api/v1/tasks").json()] == [task_id]
        accepted = guest.post(
            "/api/v1/tasks",
            json={"gallery_urls": "https://e-hentai.org/g/33333/abcdef0123/", "archive_type": "original"},
        )
        assert accepted.status_code == 202


def test_guest_archive_access_supports_local_cache_and_remote_relay(test_settings, monkeypatch):
    app = create_app(test_settings)
    monkeypatch.setattr(
        "app.main.EHClient.iter_download",
        lambda _self, _url, **_kwargs: iter([(b"PK-relayed", 10)]),
    )
    cache_path = test_settings.cache_dir / "public.zip"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(b"PK-public")
    with TestClient(app) as admin:
        headers = login(admin)
        task_id = create_task(admin, headers, 33444)["id"]
        with app.state.database.session_factory() as session:
            task = session.get(DownloadTask, task_id)
            entry = CacheEntry(
                sha1="b" * 40, zip_path="public.zip", filename="public.zip", filesize=9,
                expire_at=utcnow() + timedelta(days=7),
            )
            archive = Archive(
                gid=task.gid, token=task.token, source_host=task.source_host, archive_type=task.archive_type,
                status=TaskStatus.COMPLETED.value, cache_sha1=entry.sha1,
            )
            session.add_all([entry, archive])
            session.flush()
            task.archive_id = archive.id
            task.status = TaskStatus.COMPLETED.value
            session.commit()
        current = admin.get("/api/v1/settings").json()
        current["access_mode"] = "guest"
        saved = admin.put("/api/v1/settings", json=current, headers=headers)
        assert saved.json()["access_mode"] == "guest"

    with TestClient(app) as guest:
        assert guest.get("/api/v1/tasks").json()[0]["id"] == task_id
        assert guest.get(f"/api/v1/tasks/{task_id}/download").content == b"PK-public"

    with TestClient(app) as admin:
        headers = login(admin)
        current = admin.get("/api/v1/settings").json()
        current["cache_enabled"] = False
        saved = admin.put("/api/v1/settings", json=current, headers=headers)
        assert saved.json()["access_mode"] == "guest"

        remote_task_id = create_task(admin, headers, 33555)["id"]
        with app.state.database.session_factory() as session:
            account = Account(
                name="relay-account",
                encrypted_cookie=app.state.security.encrypt("ipb_member_id=1; ipb_pass_hash=x"),
                cookie_fingerprint="8" * 64,
            )
            session.add(account)
            session.flush()
            task = session.get(DownloadTask, remote_task_id)
            remote_expiry = utcnow() + timedelta(days=1)
            archive = Archive(
                gid=task.gid, token=task.token, source_host=task.source_host,
                archive_type=task.archive_type, status=TaskStatus.COMPLETED.value,
                owner=account.id, cache_enabled=False,
                remote_download_url="https://e-hentai.org/archive/relay?start=1",
                remote_expires_at=remote_expiry,
            )
            session.add(archive)
            session.flush()
            task.archive_id = archive.id
            task.status = TaskStatus.COMPLETED.value
            task.expired_at = remote_expiry
            session.commit()
    with TestClient(app) as guest:
        state = guest.get("/api/v1/auth/session").json()
        assert state["admin"] is False
        assert state["mode"] == "guest"
        assert {task["id"] for task in guest.get("/api/v1/tasks").json()} == {task_id, remote_task_id}
        response = guest.get(f"/api/v1/tasks/{remote_task_id}/download")
        assert response.status_code == 200
        assert response.content == b"PK-relayed"


def test_admin_mode_keeps_the_type_lock_as_configured(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as admin:
        headers = login(admin)
        current = admin.get("/api/v1/settings").json()
        current["access_mode"] = "admin"
        current["guest_download_mode"] = "original"
        saved = admin.put("/api/v1/settings", json=current, headers=headers)
        assert saved.status_code == 200
        # The type lock is stored as configured; the access mode alone
        # decides whether the public desk is open at all.
        assert saved.json()["guest_download_mode"] == "original"

    with TestClient(app) as guest:
        assert guest.get("/api/v1/auth/session").json()["admin"] is False
        response = guest.post(
            "/api/v1/tasks",
            json={"gallery_urls": "https://e-hentai.org/g/33666/abcdef0123/", "archive_type": "original"},
        )
        assert response.status_code == 401


def test_missing_cached_file_returns_410_and_removes_invalid_task(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        headers = login(client)
        task_id = create_task(client, headers, 44555)["id"]
        with app.state.database.session_factory() as session:
            task = session.get(DownloadTask, task_id)
            cache = CacheEntry(
                sha1="a" * 40, zip_path="missing.zip", filename="missing.zip", filesize=100,
                expire_at=utcnow() + timedelta(days=7),
            )
            archive = Archive(
                gid=task.gid, token=task.token, source_host=task.source_host, archive_type=task.archive_type,
                status=TaskStatus.COMPLETED.value, cache_sha1=cache.sha1,
            )
            session.add_all([cache, archive])
            session.flush()
            task.archive_id = archive.id
            task.status = TaskStatus.COMPLETED.value
            session.commit()
        assert client.get(f"/api/v1/tasks/{task_id}/download").status_code == 410
        with app.state.database.session_factory() as session:
            assert session.get(DownloadTask, task_id) is None


def test_guest_access_is_off_by_default(test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        assert client.get("/api/v1/auth/session").json() == {
            "admin": False,
            "mode": "admin",
            "guest_download_mode": "disabled",
            "site_identity": False,
            "core_configured": False,
            "core_login_url": None,
            "prices": {
                "create_original": 5,
                "create_resample": 0,
                "download_original": 1,
                "download_resample": 0,
            },
            "csrf_token": None,
        }
        assert client.get("/api/v1/tasks").status_code == 401


def test_cache_can_be_disabled_and_remote_download_is_streamed(test_settings, monkeypatch):
    app = create_app(test_settings)
    monkeypatch.setattr(
        "app.main.EHClient.iter_download",
        lambda _self, _url, **_kwargs: iter([(b"PK-test-zip", 11)]),
    )
    with TestClient(app) as client:
        headers = login(client)
        current = client.get("/api/v1/settings").json()
        current["cache_enabled"] = False
        saved = client.put("/api/v1/settings", json=current, headers=headers)
        assert saved.status_code == 200
        assert saved.json()["cache_enabled"] is False

        task_id = create_task(client, headers, 55666)["id"]
        with app.state.database.session_factory() as session:
            account = Account(
                name="stream-account",
                encrypted_cookie=app.state.security.encrypt("ipb_member_id=1; ipb_pass_hash=x"),
                cookie_fingerprint="9" * 64,
            )
            session.add(account)
            session.flush()
            task = session.get(DownloadTask, task_id)
            archive = Archive(
                gid=task.gid, token=task.token, source_host=task.source_host, archive_type=task.archive_type,
                status=TaskStatus.COMPLETED.value, title="Streamed gallery", owner=account.id,
                cache_enabled=False, remote_download_url="https://e-hentai.org/archive/file?start=1",
                remote_expires_at=utcnow() + timedelta(days=1),
            )
            session.add(archive)
            session.flush()
            task.archive_id = archive.id
            task.status = TaskStatus.COMPLETED.value
            session.commit()

        response = client.get(f"/api/v1/tasks/{task_id}/download")
        assert response.status_code == 200
        assert response.content == b"PK-test-zip"
        assert response.headers["content-type"] == "application/zip"
        with app.state.database.session_factory() as session:
            task = session.get(DownloadTask, task_id)
            assert task.status == TaskStatus.WAITING_DOWNLOAD.value
            assert task.download_count == 1


def test_relay_rechecks_previous_purchase_and_fails_when_unlock_is_gone(test_settings, monkeypatch):
    from app.eh_client import ArchiveForm, ArchivePage

    app = create_app(test_settings)
    monkeypatch.setattr(
        "app.main.EHClient.iter_download",
        lambda _self, _url, **_kwargs: iter([(b"PK-repeat", 9)]),
    )
    unlocked_page = ArchivePage(
        title="Repeat gallery", gp=1000, credits=2000,
        forms={"resample": ArchiveForm(
            "resample", "https://e-hentai.org/archiver.php", {"dltype": "res"},
            9, 0, "unlocked", False,
        )},
        state="quote", download_url=None, message="Previously unlocked",
        unlocked_types=frozenset({"resample"}),
    )
    current_page = unlocked_page
    monkeypatch.setattr("app.main.EHClient.load_archive_page", lambda _self, _url: current_page)

    with TestClient(app) as client:
        headers = login(client)
        task_id = create_task(client, headers, 55777)["id"]
        with app.state.database.session_factory() as session:
            account = Account(
                name="repeat-account",
                encrypted_cookie=app.state.security.encrypt("ipb_member_id=1; ipb_pass_hash=x"),
                cookie_fingerprint="4" * 64,
            )
            session.add(account)
            session.flush()
            task = session.get(DownloadTask, task_id)
            archive = Archive(
                gid=task.gid, token=task.token, source_host=task.source_host,
                archive_type=task.archive_type, status=TaskStatus.WAITING_DOWNLOAD.value,
                title="Repeat gallery", owner=account.id, cache_enabled=False,
                archiver_url="https://e-hentai.org/archiver.php?gid=55777&token=abcdef0123",
                remote_download_url="https://e-hentai.org/archive/repeat?start=1",
                remote_expires_at=utcnow() + timedelta(days=1),
            )
            session.add(archive)
            session.flush()
            task.archive_id = archive.id
            task.status = TaskStatus.WAITING_DOWNLOAD.value
            task.download_count = 1
            session.commit()

        response = client.get(f"/api/v1/tasks/{task_id}/download")
        assert response.status_code == 200
        assert response.content == b"PK-repeat"
        with app.state.database.session_factory() as session:
            assert session.get(DownloadTask, task_id).download_count == 2

        current_page = ArchivePage(
            title="Repeat gallery", gp=1000, credits=2000,
            forms={"resample": ArchiveForm(
                "resample", "https://e-hentai.org/archiver.php", {"dltype": "res"},
                9, 100, "gp", False,
            )},
            state="quote", download_url=None, message="Unlock expired",
        )
        response = client.get(f"/api/v1/tasks/{task_id}/download")
        assert response.status_code == 410
        assert "create the task again" in response.json()["detail"]
        with app.state.database.session_factory() as session:
            task = session.get(DownloadTask, task_id)
            assert task.status == TaskStatus.FAILED.value
