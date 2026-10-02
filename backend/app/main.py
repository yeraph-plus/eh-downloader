import hmac
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .aiya_core_integration import (
    MODE_ADMIN,
    MODE_CORE,
    AiyaCoreIntegration,
    AccessContext,
    PUBLIC_REQUESTER_TYPES,
)
from .auth_service import AuthService, CookieFormatError
from .cache_service import CacheService
from .config import Settings, get_settings
from .database import Database
from .delivery_service import DeliveryError, DeliveryService
from .eh_client import EHClient, EHClientError, parse_gallery_url
from .models import ACTIVE_TASK_STATUSES, Account, Archive, CacheEntry, DownloadTask, RequesterType, TaskStatus, utcnow
from .schemas import (
    AccountCreateRequest,
    AccountResponse,
    AccountUpdateRequest,
    LoginRequest,
    LoginResponse,
    SessionResponse,
    SettingsResponse,
    SettingsUpdateRequest,
    StatsDownloads,
    StatsEhPool,
    StatsQueue,
    StatsResponse,
    StatsCredits,
    StatsTraffic,
    TaskCreateRequest,
    TaskResponse,
    TokenRotateResponse,
)
from .security import SecurityManager, SessionIdentity
from .settings_store import SettingsStore
from .task_service import TaskService


class SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 404 and "." not in Path(path).name:
            return await super().get_response("index.html", scope)
        return response


def create_app(settings: Settings | None = None, core: AiyaCoreIntegration | None = None) -> FastAPI:
    settings = settings or get_settings()
    database = Database(settings)
    security = SecurityManager(settings)
    store = SettingsStore(settings)
    auth_service = AuthService(settings, security, store)
    task_service = TaskService(settings)
    cache_service = CacheService(settings, store)
    delivery_service = DeliveryService(settings, database, store, auth_service)
    core = core or AiyaCoreIntegration(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        settings.cache_dir.mkdir(parents=True, exist_ok=True)
        database.create_all()
        yield

    # 文档服务始终可用：/docs（Swagger UI）、/redoc、/openapi.json 显式钉死，
    # 不随部署形态变化（设置页「API远程调用」的接口文档链接依赖它）。
    app = FastAPI(
        title="Eh Downloader",
        version="1.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    app.state.settings = settings
    app.state.database = database
    app.state.security = security
    app.state.store = store
    app.state.auth_service = auth_service
    app.state.task_service = task_service
    app.state.cache_service = cache_service
    app.state.delivery_service = delivery_service
    app.state.core = core

    def db_session():
        with database.session_factory() as session:
            yield session

    def _bearer_identity(authorization: str | None, session: Session) -> SessionIdentity | None:
        if not authorization or not authorization.startswith("Bearer "):
            return None
        expected = store.get_api_token_hash(session)
        provided = security.hash_api_token(authorization[7:].strip())
        if expected and hmac.compare_digest(provided, expected):
            return SessionIdentity(RequesterType.ADMIN.value, "api-token", "")
        raise HTTPException(status_code=401, detail="Invalid Bearer token")

    def _check_csrf(request: Request, identity: SessionIdentity) -> None:
        if request.method in {"GET", "HEAD", "OPTIONS"} or identity.kind == RequesterType.GUEST.value or identity.username == "api-token":
            return
        provided = request.headers.get("X-CSRF-Token", "")
        # compare_digest raises TypeError on non-ASCII str input; encode
        # so a crafted header answers 403 instead of blowing up a 500.
        if not hmac.compare_digest(provided.encode("utf-8"), identity.csrf_token.encode("utf-8")):
            raise HTTPException(status_code=403, detail="Invalid CSRF token")

    def require_admin(
        request: Request,
        session: Session = Depends(db_session),
        authorization: str | None = Header(default=None),
    ) -> SessionIdentity:
        identity = _bearer_identity(authorization, session) or security.parse_session(request.cookies.get("ehd_session"))
        if not identity or identity.kind != RequesterType.ADMIN.value:
            raise HTTPException(status_code=401, detail="Administrator authentication required")
        _check_csrf(request, identity)
        return identity

    def require_user(
        request: Request,
        session: Session = Depends(db_session),
        authorization: str | None = Header(default=None),
    ) -> AccessContext:
        admin_identity = _bearer_identity(authorization, session) or security.parse_session(
            request.cookies.get("ehd_session")
        )
        configured_mode = store.get_access_mode(session)
        return core.guard(request, admin_identity, lambda req: _check_csrf(req, admin_identity), configured_mode)

    def _task_access(task: DownloadTask, context: AccessContext, session: Session) -> bool:
        if context.is_admin:
            return True
        # The public desk sees the guest/user records (attribution doubles
        # as ownership — anonymous guests share one identity) plus every
        # ready archive whose bytes are actually still there.
        if task.status not in {TaskStatus.COMPLETED.value, TaskStatus.WAITING_DOWNLOAD.value}:
            return task.requester_type in PUBLIC_REQUESTER_TYPES
        archive = task.archive
        if not archive:
            return task.requester_type in PUBLIC_REQUESTER_TYPES
        entry = archive.cache_entry
        if entry and entry.expire_at > utcnow():
            try:
                return cache_service.path_for(entry.zip_path).is_file()
            except ValueError:
                return False
        if (
            not archive.cache_enabled
            and archive.remote_download_url
            and archive.account
        ):
            return True
        return task.requester_type in PUBLIC_REQUESTER_TYPES

    @app.get("/health/live")
    def health_live() -> dict[str, str]:
        return {"status": "ok"}

    def _set_session_cookie(response: Response, value: str, max_age: int) -> None:
        response.set_cookie(
            "ehd_session",
            value,
            httponly=True,
            secure=settings.secure_cookies,
            samesite="strict",
            max_age=max_age,
            path="/",
        )

    @app.get("/health/ready")
    def health_ready(session: Session = Depends(db_session)) -> dict[str, str]:
        session.execute(select(DownloadTask.id).limit(1))
        return {"status": "ready"}

    @app.get("/api/v1/auth/session", response_model=SessionResponse)
    def current_session(request: Request, session: Session = Depends(db_session)) -> SessionResponse:
        admin_identity = security.parse_session(request.cookies.get("ehd_session"))
        is_admin = admin_identity is not None
        mode = core.effective_mode(store.get_access_mode(session))
        site_identity = False
        if mode == MODE_CORE:
            bearer = request.cookies.get(settings.aiya_core_session_cookie)
            site_identity = core.resolve_site_identity(bearer) is not None
        # The redirect link is page-configured only (no env fallback).
        core_login_url = store.get_core_site_url(session) or None
        prices = {
            "create_original": store.get_price(session, SettingsStore.PRICE_CREATE_ORIGINAL_KEY),
            "create_resample": store.get_price(session, SettingsStore.PRICE_CREATE_RESAMPLE_KEY),
            "download_original": store.get_price(session, SettingsStore.PRICE_DOWNLOAD_ORIGINAL_KEY),
            "download_resample": store.get_price(session, SettingsStore.PRICE_DOWNLOAD_RESAMPLE_KEY),
        }
        return SessionResponse(
            admin=is_admin,
            mode=MODE_ADMIN if is_admin else mode,
            guest_download_mode=store.get_guest_download_mode(session),
            site_identity=site_identity,
            core_configured=core.enabled(),
            core_login_url=core_login_url,
            prices=prices,
            csrf_token=admin_identity.csrf_token if admin_identity else None,
        )

    @app.post("/api/v1/auth/login", response_model=LoginResponse)
    def login(payload: LoginRequest, response: Response) -> LoginResponse:
        if not security.verify_admin(payload.username, payload.password):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        value, identity = security.create_session()
        _set_session_cookie(response, value, settings.session_ttl_seconds)
        return LoginResponse(username=identity.username, csrf_token=identity.csrf_token)

    @app.post("/api/v1/auth/logout", status_code=204)
    def logout(response: Response, _: SessionIdentity = Depends(require_admin)) -> None:
        # 204 + an explicit Response return makes FastAPI emit a response
        # with a null status that uvicorn cannot even name — return None
        # and let the declared status speak.
        response.delete_cookie("ehd_session", path="/")

    @app.post("/api/v1/tasks", response_model=list[TaskResponse], status_code=status.HTTP_202_ACCEPTED)
    def create_task(
        payload: TaskCreateRequest,
        context: AccessContext = Depends(require_user),
        session: Session = Depends(db_session),
    ) -> list[TaskResponse]:
        if not context.is_admin:
            guest_download_mode = store.get_guest_download_mode(session)
            if guest_download_mode == "disabled":
                raise HTTPException(status_code=403, detail="Guest task creation is disabled")
            # The tier is the highest requestable type, so the top tier
            # (original) admits both archive types; below it only itself.
            if guest_download_mode != "original" and payload.archive_type != guest_download_mode:
                raise HTTPException(status_code=403, detail=f"Guests may only request {guest_download_mode} archives")
        urls = [line.strip() for line in payload.gallery_urls.splitlines() if line.strip()]
        if not urls:
            raise HTTPException(status_code=400, detail="At least one gallery URL is required")
        if len(urls) > 100:
            raise HTTPException(status_code=400, detail="At most 100 gallery URLs may be submitted at once")
        galleries = []
        seen: set[tuple[int, str]] = set()
        for line_number, url in enumerate(urls, start=1):
            try:
                gallery = parse_gallery_url(url)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"Line {line_number}: {exc}") from exc
            key = (gallery.gid, gallery.token)
            if key not in seen:
                galleries.append(gallery)
                seen.add(key)
        # Charged before anything is queued: the pre-check answers which
        # galleries would actually create, and only after every charge
        # stands does creation run — an insufficient balance on the last
        # URL leaves nothing half-queued. The integration module owns the
        # dedupe keys (gallery identity for fresh creations, task id in a
        # 30-second window for requeues).
        if not context.is_admin:
            for gallery in galleries:
                existing = task_service.find_existing(session, gallery, payload.archive_type)
                if existing is None:
                    core.charge(context, store, session, "create", payload.archive_type, gid=gallery.gid, token=gallery.token)
                elif existing.status in {TaskStatus.FAILED.value, TaskStatus.EXPIRED.value}:
                    core.charge(
                        context, store, session, "requeue", payload.archive_type,
                        gid=gallery.gid, token=gallery.token, task_id=existing.id,
                    )
        requester_type = RequesterType.ADMIN.value if context.is_admin else (
            RequesterType.USER.value if context.site_user_id else RequesterType.GUEST.value
        )
        requester_id = (
            context.admin_name if context.is_admin
            else str(context.site_user_id) if context.site_user_id
            else "global"
        )
        tasks = []
        for gallery in galleries:
            task, created = task_service.create_or_existing(
                session, gallery, payload.archive_type, requester_type, requester_id
            )
            if created:
                tasks.append(task)
        session.commit()
        return [task_response(task) for task in tasks]

    @app.get("/api/v1/tasks", response_model=list[TaskResponse])
    def list_tasks(
        context: AccessContext = Depends(require_user), session: Session = Depends(db_session)
    ) -> list[TaskResponse]:
        tasks = session.scalars(task_service.visible_query(
            is_admin=context.is_admin
        )).all()
        if not context.is_admin:
            tasks = [task for task in tasks if _task_access(task, context, session)]
        return [task_response(task) for task in tasks]

    @app.get("/api/v1/tasks/{task_id}", response_model=TaskResponse)
    def get_task(
        task_id: str, context: AccessContext = Depends(require_user), session: Session = Depends(db_session)
    ) -> TaskResponse:
        task = session.get(DownloadTask, task_id)
        if not task or not _task_access(task, context, session):
            raise HTTPException(status_code=404, detail="Task not found")
        return task_response(task)

    @app.get("/api/v1/tasks/{task_id}/download")
    def download_task(
        task_id: str, context: AccessContext = Depends(require_user), session: Session = Depends(db_session)
    ) -> Response:
        task = session.get(DownloadTask, task_id)
        if not task or not _task_access(task, context, session):
            raise HTTPException(status_code=404, detail="Task not found")
        archive = task.archive
        entry = archive.cache_entry if archive else None
        if task.status not in {TaskStatus.COMPLETED.value, TaskStatus.WAITING_DOWNLOAD.value} or not archive:
            raise HTTPException(status_code=409, detail="Archive is not ready")
        # The charge lands only after the delivery is certain (relay
        # prepared / cache file confirmed) and before the first byte
        # moves — the integration module owns the price, the dedupe
        # window and the anonymous refusal.
        if not entry and not archive.cache_enabled and archive.remote_download_url and archive.account:
            try:
                delivery = delivery_service.prepare_remote(session, task)
            except DeliveryError as exc:
                raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
            core.charge(
                context, store, session, "download", task.archive_type,
                task_id=task.id, download_seq=task.download_count + 1,
            )
            return StreamingResponse(
                delivery_service.stream(delivery),
                media_type="application/zip",
                headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(delivery.filename, safe='')}"},
            )
        if not entry:
            raise HTTPException(status_code=409, detail="Archive is not ready")
        try:
            path = cache_service.path_for(entry.zip_path)
        except ValueError:
            path = Path("__invalid__")
        if not path.is_file():
            cache_service.remove_entry(session, entry)
            raise HTTPException(status_code=410, detail="Cached archive is no longer available")
        core.charge(
            context, store, session, "download", task.archive_type,
            task_id=task.id, download_seq=task.download_count + 1,
        )
        entry.last_accessed_at = utcnow()
        task.download_count += 1
        # Stats埋点：缓存命中也算一次交付（字节来自本地盘，不计 EH 流量）。
        store.bump_counter(session, SettingsStore.STATS_DOWNLOADS_SERVED_KEY, 1)
        session.commit()
        return FileResponse(path, filename=entry.filename, media_type="application/zip", headers={"ETag": f'"{entry.sha1}"'})

    @app.get("/api/v1/stats", response_model=StatsResponse)
    def get_stats(
        context: AccessContext = Depends(require_user),
        session: Session = Depends(db_session),
    ) -> StatsResponse:
        """Aggregate desk stats for the public stats page: same gating as
        the task list (admin-mode anonymous visitors get 401), aggregates
        only — no account identities ever leave the server."""
        now = utcnow()
        beat = store.get_counter(session, SettingsStore.STATS_WORKER_BEAT_KEY)
        worker_alive = beat > 0 and (now.timestamp() - beat) < 90

        queued = session.scalar(
            select(func.count()).select_from(DownloadTask).where(DownloadTask.status == TaskStatus.QUEUED.value)
        )
        active = session.scalar(
            select(func.count()).select_from(DownloadTask).where(DownloadTask.status.in_(ACTIVE_TASK_STATUSES))
        )
        completed = session.scalar(
            select(func.count()).select_from(DownloadTask).where(DownloadTask.status == TaskStatus.COMPLETED.value)
        )
        failed = session.scalar(
            select(func.count()).select_from(DownloadTask).where(DownloadTask.status == TaskStatus.FAILED.value)
        )
        archives_ready = session.scalar(
            select(func.count()).select_from(CacheEntry).where(CacheEntry.expire_at > now)
        )
        cache_stored = session.scalar(select(func.coalesce(func.sum(CacheEntry.filesize), 0)))

        accounts_total = session.scalar(select(func.count()).select_from(Account))
        accounts_ready = session.scalar(
            select(func.count()).select_from(Account).where(
                Account.enabled.is_(True),
                or_(Account.cooldown_until.is_(None), Account.cooldown_until <= now),
            )
        )
        gp_total = session.scalar(select(func.sum(Account.gp)))
        credits_total = session.scalar(select(func.sum(Account.credits)))

        return StatsResponse(
            worker_alive=worker_alive,
            worker_last_beat_at=beat or None,
            cache_enabled=store.get_cache_enabled(session),
            queue=StatsQueue(queued=queued, active=active),
            downloads=StatsDownloads(
                archives_ready=archives_ready,
                tasks_completed=completed,
                tasks_failed=failed,
                served_total=store.get_counter(session, SettingsStore.STATS_DOWNLOADS_SERVED_KEY),
            ),
            traffic=StatsTraffic(
                bytes_downloaded_total=store.get_counter(session, SettingsStore.STATS_BYTES_DOWNLOADED_KEY),
                cache_used_bytes=cache_service.used_bytes(),
                cache_stored_bytes=cache_stored,
            ),
            credits=StatsCredits(total_spent=store.get_counter(session, SettingsStore.STATS_CREDIT_SPENT_KEY)),
            eh_pool=StatsEhPool(
                gp=gp_total,
                credits=credits_total,
                accounts_ready=accounts_ready,
                accounts_total=accounts_total,
            ),
        )

    @app.get("/api/v1/accounts", response_model=list[AccountResponse])
    def list_accounts(_: SessionIdentity = Depends(require_admin), session: Session = Depends(db_session)):
        return [account_response(row) for row in session.scalars(select(Account).order_by(Account.priority, Account.name))]

    @app.post("/api/v1/accounts", response_model=AccountResponse, status_code=201)
    def create_account(
        payload: AccountCreateRequest,
        _: SessionIdentity = Depends(require_admin),
        session: Session = Depends(db_session),
    ) -> AccountResponse:
        try:
            account = auth_service.add_account(session, payload.name, payload.cookies_txt, payload.priority)
            session.commit()
        except CookieFormatError as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail="Account name or Cookie already exists") from exc
        return account_response(account)

    @app.patch("/api/v1/accounts/{account_id}", response_model=AccountResponse)
    def update_account(
        account_id: str,
        payload: AccountUpdateRequest,
        _: SessionIdentity = Depends(require_admin),
        session: Session = Depends(db_session),
    ) -> AccountResponse:
        account = session.get(Account, account_id)
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        for field in ("name", "enabled", "priority"):
            value = getattr(payload, field)
            if value is not None:
                setattr(account, field, value.strip() if isinstance(value, str) else value)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail="Account name already exists") from exc
        return account_response(account)

    @app.post("/api/v1/accounts/{account_id}/refresh", response_model=AccountResponse)
    def refresh_account(
        account_id: str,
        _: SessionIdentity = Depends(require_admin),
        session: Session = Depends(db_session),
    ) -> AccountResponse:
        account = session.get(Account, account_id)
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        try:
            auth_service.validate_account(session, account)
            session.commit()
        except EHClientError as exc:
            session.commit()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return account_response(account)

    @app.delete("/api/v1/accounts/{account_id}", status_code=204)
    def delete_account(
        account_id: str,
        _: SessionIdentity = Depends(require_admin),
        session: Session = Depends(db_session),
    ) -> None:
        account = session.get(Account, account_id)
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        active = session.scalar(
            select(DownloadTask.id)
            .join(Archive, DownloadTask.archive_id == Archive.id)
            .where(Archive.owner == account_id, DownloadTask.status.in_(ACTIVE_TASK_STATUSES))
            .limit(1)
        )
        if active:
            raise HTTPException(status_code=409, detail="Account is assigned to an active task; disable it instead")
        session.delete(account)
        session.commit()

    @app.get("/api/v1/settings", response_model=SettingsResponse)
    def get_settings_api(_: SessionIdentity = Depends(require_admin), session: Session = Depends(db_session)):
        return settings_response(session, store, settings.aiya_core_base_url, settings.aiya_core_enabled)

    @app.put("/api/v1/settings", response_model=SettingsResponse)
    def update_settings_api(
        payload: SettingsUpdateRequest,
        _: SessionIdentity = Depends(require_admin),
        session: Session = Depends(db_session),
    ):
        store.update_public_settings(session, **payload.model_dump())
        session.commit()
        return settings_response(session, store, settings.aiya_core_base_url, settings.aiya_core_enabled)

    @app.post("/api/v1/settings/api-token/rotate", response_model=TokenRotateResponse)
    def rotate_api_token(_: SessionIdentity = Depends(require_admin), session: Session = Depends(db_session)):
        token = security.generate_api_token()
        store.set_api_token_hash(session, security.hash_api_token(token))
        session.commit()
        return TokenRotateResponse(token=token)

    if settings.frontend_dir.is_dir():
        app.mount("/", SPAStaticFiles(directory=settings.frontend_dir, html=True), name="frontend")
    return app


def task_response(task: DownloadTask) -> TaskResponse:
    archive = task.archive
    entry = archive.cache_entry if archive else None
    return TaskResponse(
        id=task.id,
        gallery_url=task.gallery_url,
        gid=task.gid,
        archive_type=task.archive_type,
        title=archive.title if archive else None,
        status=task.status,
        progress=task.progress,
        download_count=task.download_count,
        size_bytes=entry.filesize if entry else archive.estimated_size if archive else None,
        filename=entry.filename if entry else None,
        error=task.error,
        wait_reason=task.wait_reason,
        retry_count=task.retry_count,
        created_at=task.created_at,
        updated_at=task.updated_at,
        expired_at=task.expired_at,
        completed_at=task.completed_at,
        download_url=(
            f"/api/v1/tasks/{task.id}/download"
            if task.status in {TaskStatus.COMPLETED.value, TaskStatus.WAITING_DOWNLOAD.value}
            and (
                entry and entry.expire_at > utcnow()
                or archive and not archive.cache_enabled and archive.remote_download_url
            )
            else None
        ),
    )


def account_response(account: Account) -> AccountResponse:
    return AccountResponse(
        id=account.id,
        name=account.name,
        cookie_fingerprint=account.cookie_fingerprint[:12],
        gp=account.gp,
        credits=account.credits,
        enabled=account.enabled,
        priority=account.priority,
        cooldown_reason=account.cooldown_reason,
        cooldown_until=account.cooldown_until,
        last_used_at=account.last_used_at,
        last_download_cost=account.last_download_cost,
        last_download_cost_type=account.last_download_cost_type,
        last_error=account.last_error,
        created_at=account.created_at,
    )


def settings_response(session: Session, store: SettingsStore, core_base_url: str, core_enabled: bool) -> SettingsResponse:
    return SettingsResponse(
        access_mode=store.get_access_mode(session),
        core_enabled=core_enabled,
        core_site_url=store.get_core_site_url(session),
        core_base_url=core_base_url,
        guest_download_mode=store.get_guest_download_mode(session),
        cache_enabled=store.get_cache_enabled(session),
        retention_days=store.get_retention_days(session),
        cache_limit_bytes=store.get_cache_limit_bytes(session),
        max_archive_size_mb=store.get_max_archive_size_mb(session),
        worker_concurrency=store.get_worker_concurrency(session),
        task_max_retries=store.get_task_max_retries(session),
        eh_proxy_url=store.get_eh_proxy_url(session),
        api_token_configured=store.get_api_token_hash(session) is not None,
        price_create_original=store.get_price(session, SettingsStore.PRICE_CREATE_ORIGINAL_KEY),
        price_create_resample=store.get_price(session, SettingsStore.PRICE_CREATE_RESAMPLE_KEY),
        price_download_original=store.get_price(session, SettingsStore.PRICE_DOWNLOAD_ORIGINAL_KEY),
        price_download_resample=store.get_price(session, SettingsStore.PRICE_DOWNLOAD_RESAMPLE_KEY),
    )


app = create_app()
