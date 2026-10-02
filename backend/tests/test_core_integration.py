"""The three access modes end to end (`admin` / `guest` / `core`), with
the site's HTTP surface faked behind the integration client's transport
hook. Every billing assertion runs against recorded spend calls instead
of a live site."""

import json
import uuid
from datetime import timedelta

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.aiya_core_integration import AiyaCoreIntegration, IntegrationError
from app.main import create_app
from app.models import AppSetting, Archive, CacheEntry, DownloadTask, RequesterType, TaskStatus, utcnow


class FakeCore:
    """Records spend calls and answers canned site responses. The dedupe
    key is honoured the way the site ledger answers it: a repeated one-shot
    charge returns `duplicate: true` and never deducts again."""

    def __init__(self, balance: int = 100):
        self.balance = balance
        self.spends: list[dict] = []
        self.attempts: list[dict] = []
        self.claimed: set[str] = set()
        self.banned = False
        self.unreachable = False

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            if self.unreachable:
                raise httpx.ConnectError("connection refused", request=request)
            path = request.url.path
            if path.endswith("/auth/tickets"):
                return httpx.Response(200, json={"ticket": "a" * 32, "expires_at": 1})
            if path.endswith("/auth/tickets/redeem"):
                return httpx.Response(200, json={"userId": 7, "displayName": "Alice", "banned": self.banned})
            if path.endswith("/credits/spend"):
                body = json.loads(request.content)
                self.attempts.append(body)
                dedupe = body.get("dedupe")
                if dedupe and dedupe in self.claimed:
                    return httpx.Response(200, json={"balance": self.balance, "duplicate": True})
                if dedupe:
                    self.claimed.add(dedupe)
                self.spends.append(body)
                if body["amount"] > self.balance:
                    return httpx.Response(409, json={
                        "code": "aiya_credit_insufficient",
                        "message": "积分余额不足。",
                        "data": {"status": 409, "balance": self.balance},
                    })
                self.balance -= body["amount"]
                return httpx.Response(200, json={"balance": self.balance, "duplicate": False})
            if path.endswith("/credits/balance"):
                return httpx.Response(200, json={"balance": self.balance})
            return httpx.Response(404, json={"code": "rest_no_route", "message": "not found", "data": {"status": 404}})

        return httpx.MockTransport(handler)


SITE_COOKIE = "7.ABCDEFGHIJKLMNOP"


def build(test_settings, *, mode: str = "core", balance: int = 100, configured: bool = True, enabled: bool = True):
    settings = test_settings.model_copy(update={
        "aiya_core_enabled": enabled,
        "aiya_core_base_url": "https://core.test" if configured else "",
        "aiya_core_service_key": "svc-key" if configured else "",
    })
    fake = FakeCore(balance)
    app = create_app(settings, core=AiyaCoreIntegration(settings, transport=fake.transport()))
    client = TestClient(app)
    with client:
        with app.state.database.session_factory() as session:
            session.add_all([
                AppSetting(key="access_mode", value=mode),
                # The public creation lock follows the mode tests' resample
                # submissions; individual tests override it when needed.
                AppSetting(key="guest_download_mode", value="resample"),
            ])
            session.commit()
    return app, fake, client


def set_price(app, key: str, value: str) -> None:
    with app.state.database.session_factory() as session:
        existing = session.get(AppSetting, key)
        if existing:
            existing.value = value
        else:
            session.add(AppSetting(key=key, value=value))
        session.commit()


def task_count(app) -> int:
    with app.state.database.session_factory() as session:
        return int(session.scalar(select(func.count()).select_from(DownloadTask)))


def admin_headers(client: TestClient) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password-123"})
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def complete_with_cached_zip(app, task_id: str) -> None:
    with app.state.database.session_factory() as session:
        task = session.get(DownloadTask, task_id)
        entry = CacheEntry(
            sha1=uuid.uuid4().hex + uuid.uuid4().hex[:8], zip_path=f"{uuid.uuid4().hex}.zip",
            filename="cache.zip", filesize=9,
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
    zip_path = app.state.settings.cache_dir / entry.zip_path
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    zip_path.write_bytes(b"PK-site")


# --- admin mode -------------------------------------------------------------


def test_admin_mode_closes_the_public_desk(test_settings):
    app, fake, client = build(test_settings, mode="admin")
    with client:
        assert client.get("/api/v1/auth/session").json()["mode"] == "admin"
        assert client.get("/api/v1/tasks").status_code == 401
        assert client.post(
            "/api/v1/tasks",
            json={"gallery_urls": "https://e-hentai.org/g/12345/abcdef0123/", "archive_type": "resample"},
        ).status_code == 401
        assert fake.spends == []


# --- guest mode -------------------------------------------------------------


def test_guest_mode_keeps_the_classic_free_behavior(test_settings):
    app, fake, client = build(test_settings, mode="guest")
    with client:
        set_price(app, "price_create_resample", "5")
        state = client.get("/api/v1/auth/session").json()
        assert state["mode"] == "guest"
        assert state["site_identity"] is False
        created = client.post(
            "/api/v1/tasks",
            json={"gallery_urls": "https://e-hentai.org/g/12345/abcdef0123/", "archive_type": "resample"},
        )
        assert created.status_code == 202
        # Guest mode never charges — prices are ignored there.
        assert fake.spends == []


def test_guest_mode_type_lock_still_applies(test_settings):
    app, _fake, client = build(test_settings, mode="guest")
    with client:
        set_price(app, "price_create_original", "5")
        forbidden = client.post(
            "/api/v1/tasks",
            json={"gallery_urls": "https://e-hentai.org/g/12345/abcdef0123/", "archive_type": "original"},
        )
        assert forbidden.status_code == 403


def test_admin_mode_is_the_default_when_unset(test_settings):
    app, _fake, client = build(test_settings, mode="unset")
    with client:
        assert client.get("/api/v1/auth/session").json()["mode"] == "admin"


# --- core mode --------------------------------------------------------------


def test_core_mode_anonymous_can_browse_but_not_act(test_settings):
    app, fake, client = build(test_settings, mode="core")
    with TestClient(app) as admin:
        headers = admin_headers(admin)
        created = admin.post(
            "/api/v1/tasks",
            json={"gallery_urls": "https://e-hentai.org/g/12345/abcdef0123/", "archive_type": "resample"},
            headers=headers,
        ).json()[0]
        complete_with_cached_zip(app, created["id"])

    state = client.get("/api/v1/auth/session").json()
    assert state["mode"] == "core"
    assert state["site_identity"] is False
    # Browsing stays alive, actions are refused.
    assert [row["id"] for row in client.get("/api/v1/tasks").json()] == [created["id"]]
    assert client.post(
        "/api/v1/tasks",
        json={"gallery_urls": "https://e-hentai.org/g/999111/abcdef0123/", "archive_type": "resample"},
    ).status_code == 401
    assert client.get(f"/api/v1/tasks/{created['id']}/download").status_code == 401
    assert fake.spends == []


def test_core_mode_charges_the_site_user_per_creation(test_settings):
    app, fake, client = build(test_settings, mode="core")
    with client:
        set_price(app, "price_create_resample", "5")
        client.cookies.set("aiya_session", SITE_COOKIE)
        created = client.post(
            "/api/v1/tasks",
            json={"gallery_urls": "https://e-hentai.org/g/12345/abcdef0123/", "archive_type": "resample"},
        )
        assert created.status_code == 202
        assert len(fake.spends) == 1
        charge = fake.spends[0]
        assert charge["userId"] == 7
        assert charge["amount"] == 5
        assert charge["source"] == "spend_eh"
        assert charge["ref"] == "task:12345:abcdef0123:resample"
        assert charge["dedupe"] == "ehd_task:12345:abcdef0123:resample"

        with app.state.database.session_factory() as session:
            task = session.scalars(select(DownloadTask)).first()
            assert task.requester_type == RequesterType.USER.value
            assert task.requester_id == "7"


def test_core_mode_type_lock_top_tier_admits_resample(test_settings):
    """The creation lock is a ceiling, not an equality: at its top tier
    (original) the site user may still pick resample and is billed at the
    resample price — the same rule the guest desk follows."""
    app, fake, client = build(test_settings, mode="core")
    with client:
        set_price(app, "guest_download_mode", "original")
        set_price(app, "price_create_resample", "3")
        client.cookies.set("aiya_session", SITE_COOKIE)
        created = client.post(
            "/api/v1/tasks",
            json={"gallery_urls": "https://e-hentai.org/g/12345/abcdef0123/", "archive_type": "resample"},
        )
        assert created.status_code == 202
        assert created.json()[0]["archive_type"] == "resample"
        assert [charge["amount"] for charge in fake.spends] == [3]


def test_core_mode_resubmitting_a_live_task_is_free(test_settings):
    app, fake, client = build(test_settings, mode="core")
    with client:
        set_price(app, "price_create_resample", "5")
        client.cookies.set("aiya_session", SITE_COOKIE)
        url = {"gallery_urls": "https://e-hentai.org/g/12345/abcdef0123/", "archive_type": "resample"}
        assert client.post("/api/v1/tasks", json=url).status_code == 202
        assert len(fake.spends) == 1
        assert client.post("/api/v1/tasks", json=url).json() == []
        assert len(fake.spends) == 1


def test_core_mode_requeues_with_their_own_dedupe_key(test_settings):
    app, fake, client = build(test_settings, mode="core")
    with client:
        set_price(app, "price_create_resample", "5")
        client.cookies.set("aiya_session", SITE_COOKIE)
        url = {"gallery_urls": "https://e-hentai.org/g/12345/abcdef0123/", "archive_type": "resample"}
        created = client.post("/api/v1/tasks", json=url).json()[0]

        with app.state.database.session_factory() as session:
            task = session.get(DownloadTask, created["id"])
            task.status = TaskStatus.FAILED.value
            task.error = "Account authentication failed"
            session.commit()

        assert client.post("/api/v1/tasks", json=url).status_code == 202
        assert len(fake.spends) == 2
        assert fake.spends[1]["dedupe"].startswith(f"ehd_requeue:{created['id']}:")


def test_core_mode_a_crashed_creation_retry_does_not_pay_twice(test_settings):
    app, fake, client = build(test_settings, mode="core")
    with client:
        set_price(app, "price_create_resample", "5")
        client.cookies.set("aiya_session", SITE_COOKIE)
        fake.claimed.add("ehd_task:12345:abcdef0123:resample")
        response = client.post(
            "/api/v1/tasks",
            json={"gallery_urls": "https://e-hentai.org/g/12345/abcdef0123/", "archive_type": "resample"},
        )
        assert response.status_code == 202
        assert len(response.json()) == 1
        assert fake.spends == []
        assert task_count(app) == 1


def test_core_mode_an_insufficient_balance_blocks_creation(test_settings):
    app, _fake, client = build(test_settings, mode="core", balance=3)
    with client:
        set_price(app, "price_create_resample", "5")
        client.cookies.set("aiya_session", SITE_COOKIE)
        response = client.post(
            "/api/v1/tasks",
            json={"gallery_urls": "https://e-hentai.org/g/12345/abcdef0123/", "archive_type": "resample"},
        )
        assert response.status_code == 409
        assert "3" in response.json()["detail"]
        assert task_count(app) == 0


def test_core_mode_a_banned_identity_is_answered_like_anonymous(test_settings):
    app, fake, client = build(test_settings, mode="core")
    with client:
        set_price(app, "price_create_resample", "5")
        fake.banned = True
        client.cookies.set("aiya_session", SITE_COOKIE)
        state = client.get("/api/v1/auth/session").json()
        assert state["site_identity"] is False
        assert client.post(
            "/api/v1/tasks",
            json={"gallery_urls": "https://e-hentai.org/g/12345/abcdef0123/", "archive_type": "resample"},
        ).status_code == 401
        assert fake.spends == []


def test_core_mode_an_unreachable_site_keeps_browsing_and_blocks_actions(test_settings):
    app, fake, client = build(test_settings, mode="core")
    with TestClient(app) as admin:
        headers = admin_headers(admin)
        created = admin.post(
            "/api/v1/tasks",
            json={"gallery_urls": "https://e-hentai.org/g/12345/abcdef0123/", "archive_type": "resample"},
            headers=headers,
        ).json()[0]
        complete_with_cached_zip(app, created["id"])

    with client:
        set_price(app, "price_download_resample", "2")
        client.cookies.set("aiya_session", SITE_COOKIE)
        fake.unreachable = True
        # The shared list survives the outage…
        assert client.get("/api/v1/tasks").status_code == 200
        # …while a charged action fails closed with a 503, not a free serve.
        assert client.get(f"/api/v1/tasks/{created['id']}/download").status_code == 503
        assert client.post(
            "/api/v1/tasks",
            json={"gallery_urls": "https://e-hentai.org/g/999111/abcdef0123/", "archive_type": "resample"},
        ).status_code == 503
        assert fake.spends == []


def test_core_mode_downloads_charge_per_serve_with_a_debounce_window(test_settings):
    app, fake, client = build(test_settings, mode="core")
    with client:
        set_price(app, "price_download_resample", "2")
        client.cookies.set("aiya_session", SITE_COOKIE)
        created = client.post(
            "/api/v1/tasks",
            json={"gallery_urls": "https://e-hentai.org/g/12345/abcdef0123/", "archive_type": "resample"},
        ).json()[0]
        complete_with_cached_zip(app, created["id"])

        first = client.get(f"/api/v1/tasks/{created['id']}/download")
        assert first.status_code == 200
        assert first.content == b"PK-site"
        assert len(fake.spends) == 1
        assert fake.spends[0]["meter"] == "download"
        assert fake.spends[0]["amount"] == 2
        assert fake.spends[0]["dedupe"].startswith(f"ehd_dl:{created['id']}:")

        # Same window: the repeat carries the same dedupe key and rides the
        # recorded charge — one spend, two deliveries.
        second = client.get(f"/api/v1/tasks/{created['id']}/download")
        assert second.status_code == 200
        assert len(fake.attempts) == 2
        assert fake.attempts[1]["dedupe"] == fake.attempts[0]["dedupe"]
        assert len(fake.spends) == 1


def test_core_mode_admin_actions_never_charge(test_settings):
    app, fake, client = build(test_settings, mode="core")
    with client:
        set_price(app, "price_create_resample", "5")
        headers = admin_headers(client)
        created = client.post(
            "/api/v1/tasks",
            json={"gallery_urls": "https://e-hentai.org/g/12345/abcdef0123/", "archive_type": "resample"},
            headers=headers,
        )
        assert created.status_code == 202
        assert fake.spends == []


def test_core_mode_settings_round_trip_the_prices(test_settings):
    app, _fake, client = build(test_settings, mode="core")
    with client:
        headers = admin_headers(client)
        current = client.get("/api/v1/settings").json()
        assert current["access_mode"] == "core"
        assert current["price_create_resample"] == 0
        current["price_create_resample"] = 5
        current["price_download_original"] = 12
        saved = client.put("/api/v1/settings", json=current, headers=headers)
        assert saved.status_code == 200
        assert saved.json()["price_create_resample"] == 5
        assert saved.json()["price_download_original"] == 12


def test_core_mode_degrades_to_guest_when_unconfigured(test_settings):
    app, _fake, client = build(test_settings, mode="core", configured=False)
    with client:
        state = client.get("/api/v1/auth/session").json()
        assert state["mode"] == "guest"
        # Guest semantics: free creation inside the type lock.
        created = client.post(
            "/api/v1/tasks",
            json={"gallery_urls": "https://e-hentai.org/g/12345/abcdef0123/", "archive_type": "resample"},
        )
        assert created.status_code == 202


def test_the_visible_set_hides_other_peoples_in_flight_tasks(test_settings):
    app, _fake, client = build(test_settings, mode="core")
    with TestClient(app) as admin:
        headers = admin_headers(admin)
        admin_task = admin.post(
            "/api/v1/tasks",
            json={"gallery_urls": "https://e-hentai.org/g/12345/abcdef0123/", "archive_type": "resample"},
            headers=headers,
        ).json()[0]

    with client:
        client.cookies.set("aiya_session", SITE_COOKIE)
        own_task = client.post(
            "/api/v1/tasks",
            json={"gallery_urls": "https://e-hentai.org/g/67890/abcdef0123/", "archive_type": "resample"},
        ).json()[0]
        listed = {row["id"] for row in client.get("/api/v1/tasks").json()}
        # Own in-flight task visible, the admin's in-flight task not.
        assert listed == {own_task["id"]}

        complete_with_cached_zip(app, admin_task["id"])
        listed = {row["id"] for row in client.get("/api/v1/tasks").json()}
        # Once ready, the admin task surfaces for everyone.
        assert listed == {own_task["id"], admin_task["id"]}


# --- client unit level ------------------------------------------------------


def test_integration_disabled_degrades_core_to_guest(test_settings):
    client = AiyaCoreIntegration(test_settings)
    assert not client.enabled()
    assert client.effective_mode("core") == "guest"
    assert client.effective_mode("guest") == "guest"
    assert client.effective_mode("admin") == "admin"


def test_master_switch_off_hides_integration_even_when_configured(test_settings):
    app, fake, client = build(test_settings, mode="core", enabled=False)
    with client:
        session_state = client.get("/api/v1/auth/session").json()
        assert session_state["mode"] == "guest"
        assert session_state["core_configured"] is False
        assert not fake.spends

        # Anonymous guests keep the free desk: creation succeeds uncharged.
        created = client.post(
            "/api/v1/tasks",
            json={"gallery_urls": "https://e-hentai.org/g/55501/abcdef0123/", "archive_type": "resample"},
        )
        assert created.status_code == 202
        assert not fake.spends

        # The settings surface hides the core mode entirely: the flag reads
        # false and a submitted core access mode is refused back to guest.
        headers = admin_headers(client)
        settings_view = client.get("/api/v1/settings", headers=headers).json()
        assert settings_view["core_enabled"] is False
        assert settings_view["access_mode"] == "guest"
        saved = client.put("/api/v1/settings", json={**settings_view, "access_mode": "core"}, headers=headers)
        assert saved.json()["access_mode"] == "guest"


def test_spend_surfaces_the_ledgers_balance_on_refusal(test_settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={
            "code": "aiya_credit_insufficient", "message": "Not enough credits.",
            "data": {"status": 409, "balance": 42},
        })

    configured = test_settings.model_copy(update={"aiya_core_base_url": "https://core.test", "aiya_core_service_key": "k"})
    client = AiyaCoreIntegration(configured, transport=httpx.MockTransport(handler))
    try:
        client.spend(7, 5, "spend_eh", "ref")
        raise AssertionError("expected IntegrationError")
    except IntegrationError as exc:
        assert exc.status_code == 409
        assert exc.balance == 42


def test_an_unreachable_site_raises_the_503_shape(test_settings):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    configured = test_settings.model_copy(update={"aiya_core_base_url": "https://core.test", "aiya_core_service_key": "k"})
    client = AiyaCoreIntegration(configured, transport=httpx.MockTransport(handler))
    try:
        client.balance(7)
        raise AssertionError("expected IntegrationError")
    except IntegrationError as exc:
        assert exc.status_code == 503

