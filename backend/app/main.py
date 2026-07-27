import hmac
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth_service import AuthService, CookieFormatError
from .cache_service import CacheService
from .config import Settings, get_settings
from .database import Database
from .delivery_service import DeliveryError, DeliveryService
from .eh_client import EHClient, EHClientError, parse_gallery_url
from .models import ACTIVE_TASK_STATUSES, Account, Archive, DownloadTask, RequesterType, TaskStatus, utcnow
from .schemas import (
    AccountCreateRequest,
    AccountResponse,
    AccountUpdateRequest,
    LoginRequest,
    LoginResponse,
    SessionResponse,
    SettingsResponse,
    SettingsUpdateRequest,
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


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    database = Database(settings)
    security = SecurityManager(settings)
    store = SettingsStore(settings)
    auth_service = AuthService(settings, security)
    task_service = TaskService(settings)
    cache_service = CacheService(settings, store)
    delivery_service = DeliveryService(settings, database, store, auth_service)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        settings.cache_dir.mkdir(parents=True, exist_ok=True)
        database.create_all()
        yield

    app = FastAPI(title="Eh Downloader", version="1.0.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.database = database
    app.state.security = security
    app.state.store = store
    app.state.auth_service = auth_service
    app.state.task_service = task_service
    app.state.cache_service = cache_service
    app.state.delivery_service = delivery_service

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
        if not hmac.compare_digest(provided, identity.csrf_token):
            raise HTTPException(status_code=403, detail="Invalid CSRF token")

    def require_admin(
        request: Request,
        session: Session = Depends(db_session),
        authorization: str | None = Header(default=None),
    ) -> SessionIdentity:
        identity = _bearer_identity(authorization, session) or security.parse_session(request.cookies.get("ehd_session"))
        if not identity:
            raise HTTPException(status_code=401, detail="Administrator authentication required")
        _check_csrf(request, identity)
        return identity

    def require_user(
        request: Request,
        session: Session = Depends(db_session),
        authorization: str | None = Header(default=None),
    ) -> SessionIdentity:
        identity = _bearer_identity(authorization, session) or security.parse_session(request.cookies.get("ehd_session"))
        guest_access = store.get_guest_cache_access(session)
        if not identity and guest_access:
            identity = SessionIdentity(RequesterType.GUEST.value, "global", "")
        if not identity:
            raise HTTPException(status_code=401, detail="Authentication required")
        _check_csrf(request, identity)
        return identity

    def _task_access(task: DownloadTask, identity: SessionIdentity, session: Session) -> bool:
        if identity.kind == RequesterType.ADMIN.value:
            return True
        guest_access = store.get_guest_cache_access(session)
        if not task_service.can_access(
            task, identity.kind, guest_access=guest_access
        ):
            return False
        if task.status not in {TaskStatus.COMPLETED.value, TaskStatus.WAITING_DOWNLOAD.value}:
            return task.requester_type == RequesterType.GUEST.value
        archive = task.archive
        if not archive:
            return False
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
        return False

    @app.get("/health/live")
    def health_live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    def health_ready(session: Session = Depends(db_session)) -> dict[str, str]:
        session.execute(select(DownloadTask.id).limit(1))
        return {"status": "ready"}

    @app.get("/api/v1/auth/session", response_model=SessionResponse)
    def current_session(request: Request, session: Session = Depends(db_session)) -> SessionResponse:
        admin = security.parse_session(request.cookies.get("ehd_session"))
        guest_cache_access = store.get_guest_cache_access(session)
        guest_download_mode = store.get_guest_download_mode(session)
        if admin:
            return SessionResponse(
                authenticated=True, role="admin", csrf_token=admin.csrf_token,
                guest_cache_access=guest_cache_access, guest_download_mode=guest_download_mode,
            )
        return SessionResponse(
            authenticated=False,
            role="none",
            csrf_token=None,
            guest_cache_access=guest_cache_access,
            guest_download_mode=guest_download_mode,
        )

    @app.post("/api/v1/auth/login", response_model=LoginResponse)
    def login(payload: LoginRequest, response: Response) -> LoginResponse:
        if not security.verify_admin(payload.username, payload.password):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        value, identity = security.create_session()
        response.set_cookie(
            "ehd_session",
            value,
            httponly=True,
            secure=settings.secure_cookies,
            samesite="strict",
            max_age=settings.session_ttl_seconds,
            path="/",
        )
        return LoginResponse(username=identity.username, csrf_token=identity.csrf_token)

    @app.post("/api/v1/auth/logout", status_code=204)
    def logout(response: Response, _: SessionIdentity = Depends(require_admin)) -> Response:
        response.delete_cookie("ehd_session", path="/")
        return response

    @app.post("/api/v1/tasks", response_model=list[TaskResponse], status_code=status.HTTP_202_ACCEPTED)
    def create_task(
        payload: TaskCreateRequest,
        identity: SessionIdentity = Depends(require_user),
        session: Session = Depends(db_session),
    ) -> list[TaskResponse]:
        if identity.kind == RequesterType.GUEST.value:
            guest_download_mode = store.get_guest_download_mode(session)
            if guest_download_mode == "disabled":
                raise HTTPException(status_code=403, detail="Guest task creation is disabled")
            if payload.archive_type != guest_download_mode:
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
        tasks = []
        for gallery in galleries:
            task, created = task_service.create_or_existing(
                session, gallery, payload.archive_type, identity.kind, identity.username
            )
            if created:
                tasks.append(task)
        session.commit()
        return [task_response(task) for task in tasks]

    @app.get("/api/v1/tasks", response_model=list[TaskResponse])
    def list_tasks(
        identity: SessionIdentity = Depends(require_user), session: Session = Depends(db_session)
    ) -> list[TaskResponse]:
        guest_access = store.get_guest_cache_access(session)
        tasks = session.scalars(task_service.visible_query(
            identity.kind, guest_access=guest_access
        )).all()
        if identity.kind == RequesterType.GUEST.value:
            tasks = [task for task in tasks if _task_access(task, identity, session)]
        return [task_response(task) for task in tasks]

    @app.get("/api/v1/tasks/{task_id}", response_model=TaskResponse)
    def get_task(
        task_id: str, identity: SessionIdentity = Depends(require_user), session: Session = Depends(db_session)
    ) -> TaskResponse:
        task = session.get(DownloadTask, task_id)
        if not task or not _task_access(task, identity, session):
            raise HTTPException(status_code=404, detail="Task not found")
        return task_response(task)

    @app.get("/api/v1/tasks/{task_id}/download")
    def download_task(
        task_id: str, identity: SessionIdentity = Depends(require_user), session: Session = Depends(db_session)
    ) -> Response:
        task = session.get(DownloadTask, task_id)
        if not task or not _task_access(task, identity, session):
            raise HTTPException(status_code=404, detail="Task not found")
        archive = task.archive
        entry = archive.cache_entry if archive else None
        if task.status not in {TaskStatus.COMPLETED.value, TaskStatus.WAITING_DOWNLOAD.value} or not archive:
            raise HTTPException(status_code=409, detail="Archive is not ready")
        if not entry and not archive.cache_enabled and archive.remote_download_url and archive.account:
            try:
                delivery = delivery_service.prepare_remote(session, task)
            except DeliveryError as exc:
                raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
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
        entry.last_accessed_at = utcnow()
        task.download_count += 1
        session.commit()
        return FileResponse(path, filename=entry.filename, media_type="application/zip", headers={"ETag": f'"{entry.sha1}"'})

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
    ) -> Response:
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
        return Response(status_code=204)

    @app.get("/api/v1/settings", response_model=SettingsResponse)
    def get_settings_api(_: SessionIdentity = Depends(require_admin), session: Session = Depends(db_session)):
        return settings_response(session, store)

    @app.put("/api/v1/settings", response_model=SettingsResponse)
    def update_settings_api(
        payload: SettingsUpdateRequest,
        _: SessionIdentity = Depends(require_admin),
        session: Session = Depends(db_session),
    ):
        store.update_public_settings(session, **payload.model_dump())
        session.commit()
        return settings_response(session, store)

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


def settings_response(session: Session, store: SettingsStore) -> SettingsResponse:
    return SettingsResponse(
        guest_cache_access=store.get_guest_cache_access(session),
        guest_download_mode=store.get_guest_download_mode(session),
        cache_enabled=store.get_cache_enabled(session),
        retention_days=store.get_retention_days(session),
        cache_limit_bytes=store.get_cache_limit_bytes(session),
        max_archive_size_mb=store.get_max_archive_size_mb(session),
        worker_concurrency=store.get_worker_concurrency(session),
        api_token_configured=store.get_api_token_hash(session) is not None,
    )


app = create_app()
