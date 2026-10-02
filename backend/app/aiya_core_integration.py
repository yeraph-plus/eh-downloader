import hashlib
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import HTTPException, Request

from .models import PUBLIC_REQUESTER_TYPES

# Site bearer tokens are `{userId}.{secret}` alphanumerics (the site's own
# session reader applies the same shape gate before trusting a cookie).
SESSION_TOKEN_PATTERN = re.compile(r"^[0-9]{1,10}\.[A-Za-z0-9]{16,128}$")

_TICKET_PATH = "/wp-json/aiya/integrations/v1/auth/tickets"
_REDEEM_PATH = "/wp-json/aiya/integrations/v1/auth/tickets/redeem"
_SPEND_PATH = "/wp-json/aiya/integrations/v1/credits/spend"
_BALANCE_PATH = "/wp-json/aiya/integrations/v1/credits/balance"

MODE_ADMIN = "admin"
MODE_GUEST = "guest"
MODE_CORE = "core"
ACCESS_MODES = (MODE_ADMIN, MODE_GUEST, MODE_CORE)

_IDENTITY_TTL = 60
_FAILURE_TTL = 15

_SPEND_WINDOW = 30


class IntegrationError(RuntimeError):
    """A site-integration failure shaped for HTTP mapping: the status code
    the API should answer with and, for insufficient-credit refusals, the
    holder's current balance."""

    def __init__(self, message: str, status_code: int, balance: int | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.balance = balance


@dataclass
class AccessContext:
    """Who this request is, decided once by guard(): the admin identity,
    or everyone else as a guest of the effective access mode. Site users
    carry `site_user_id` for billing only — every permission question is
    answered by `is_admin` and the mode, nothing else."""

    is_admin: bool
    mode: str
    site_user_id: int | None = None
    site_bearer: str | None = None
    admin_name: str = ""


class AiyaCoreIntegration:
    """The single owner of public access and site billing: a three-mode
    switch (`admin` — everything public is closed; `guest` — the classic
    anonymous download desk; `core` — the same guest behavior with the
    site session cookie layered on as external authentication and credit
    billing). The rest of the application never touches the site HTTP
    surface, the identity cache or the ledger keys — it asks this module
    for a context and for charges.

    In `core` mode the site cookie is the credential: it is resolved per
    request (behind a short in-process cache) into a billing identity, and
    the mode's actions require that identity — anonymous visitors browse
    the shared list but cannot create or download. A present-but-unresolvable
    cookie fails closed for charged actions (503) instead of degrading into
    a free anonymous visit; banned identities are answered exactly like
    anonymous ones, which the same refusal covers.
    """

    def __init__(self, settings: Any, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self._transport = transport
        # token-hash -> ("ok", user_id, expires) | ("none", expires) | ("fail", expires)
        self._identity_cache: dict[str, tuple[str, Any, int]] = {}

    @staticmethod
    def is_site_token(value: str) -> bool:
        return SESSION_TOKEN_PATTERN.match(value) is not None

    def enabled(self) -> bool:
        return bool(self.settings.aiya_core_base_url and self.settings.aiya_core_service_key)

    def effective_mode(self, configured: str) -> str:
        """`core` without the integration configured degrades to `guest`
        (same public behavior, no billing); the settings page surfaces why."""
        if configured == MODE_CORE and not self.enabled():
            return MODE_GUEST
        return configured if configured in ACCESS_MODES else MODE_ADMIN

    # --- Identity resolution (the aiya_session dispatcher) ------------------

    def resolve_site_identity(self, bearer: str | None) -> int | None:
        """Best-effort resolution: the site user id behind the cookie, or
        None (absent, malformed, banned, or the site being unreachable).
        Never raises — read paths stay alive when the site is down."""
        if not bearer or not self.enabled() or not self.is_site_token(bearer):
            return None
        state, user_id = self._resolve(bearer)
        return user_id if state == "ok" else None

    def require_site_identity(self, bearer: str | None) -> int:
        """The charged-action twin: a missing/banned identity is a 401 and
        an unreachable site is a 503 — never a free anonymous fallback."""
        if not bearer or not self.enabled() or not self.is_site_token(bearer):
            raise HTTPException(status_code=401, detail="Sign in on the site first, then retry")
        state, user_id = self._resolve(bearer)
        if state == "ok":
            return user_id
        if state == "fail":
            raise HTTPException(status_code=503, detail="The site service is unreachable, try again shortly")
        raise HTTPException(status_code=401, detail="Sign in on the site first, then retry")

    def _resolve(self, bearer: str) -> tuple[str, int]:
        """Cached resolution shared by both entry points. States: `ok`
        (user id, 60s), `none` (definitive negative — banned/revoked/
        malformed, 60s), `fail` (site unreachable, 15s — retried sooner
        so a blip doesn't pin 503s on charged actions)."""
        key = self._cache_key(bearer)
        cached = self._identity_cache.get(key)
        if cached and cached[2] > time.time():
            return cached[0], int(cached[1] or 0)
        try:
            identity = self.redeem_ticket(self.issue_ticket(bearer))
        except IntegrationError as exc:
            # Unreachable is transient (short cache); a definitive refusal
            # from the site (revoked cookie, bad ticket) is remembered for
            # the full identity TTL so require() can answer 401, not 503.
            if exc.status_code == 503:
                self._identity_cache[key] = ("fail", None, time.time() + _FAILURE_TTL)
                return "fail", 0
            self._identity_cache[key] = ("none", None, time.time() + _IDENTITY_TTL)
            return "none", 0
        if identity["banned"]:
            self._identity_cache[key] = ("none", None, time.time() + _IDENTITY_TTL)
            return "none", 0
        user_id = int(identity["userId"])
        self._identity_cache[key] = ("ok", user_id, time.time() + _IDENTITY_TTL)
        self._sweep()
        return "ok", user_id

    def _sweep(self) -> None:
        """Bounds the cache: cookie-shaped garbage sprayed at public
        endpoints must not grow it without limit — past a cap, expired
        entries are purged and the oldest half of the rest is dropped."""
        if len(self._identity_cache) <= 512:
            return
        now = time.time()
        live = {k: v for k, v in self._identity_cache.items() if v[2] > now}
        if len(live) > 256:
            ordered = sorted(live.items(), key=lambda item: item[1][2])
            live = dict(ordered[:256])
        self._identity_cache = live

    # --- Access guard --------------------------------------------------------

    def guard(
        self,
        request: Request,
        admin_identity: Any,
        check_csrf,
        configured_mode: str,
    ) -> AccessContext:
        """The require_user replacement. The admin gate is decided by the
        caller (the session cookie / API token resolved to an identity or
        not); everyone else becomes a guest of the effective mode."""
        if admin_identity is not None:
            check_csrf(request)
            return AccessContext(is_admin=True, mode=MODE_ADMIN, admin_name=admin_identity.username)

        mode = self.effective_mode(configured_mode)
        if mode == MODE_ADMIN:
            raise HTTPException(status_code=401, detail="Public access is disabled")

        bearer = request.cookies.get(self.settings.aiya_core_session_cookie)
        site_user_id = self.resolve_site_identity(bearer) if mode == MODE_CORE else None
        return AccessContext(is_admin=False, mode=mode, site_user_id=site_user_id, site_bearer=bearer)

    # --- Billing (the only charge point) -------------------------------------

    def charge(
        self,
        context: AccessContext,
        store: Any,
        db_session: Any,
        action: str,
        archive_type: str,
        *,
        gid: int | None = None,
        token: str | None = None,
        task_id: str | None = None,
        download_seq: int | None = None,
    ) -> dict[str, Any] | None:
        """The single billing point. Admins and `guest`-mode visitors pay
        nothing; in `core` mode a resolved site user pays the configured
        price and an anonymous visitor is refused. Answers the ledger's
        `{balance, duplicate}` — a duplicate (the dedupe key already
        recorded this exact charge) is a success: the delivery proceeds on
        the recorded charge. Returns None when the action is free."""

        if context.is_admin or context.mode != MODE_CORE:
            return None
        # The external authentication gate comes before any price look-up:
        # in core mode an anonymous visitor is refused even for free
        # actions, and a present-but-unresolvable cookie is a 503 — never
        # a quiet fallback to a free anonymous serve.
        user_id = context.site_user_id
        if user_id is None:
            user_id = self.require_site_identity(context.site_bearer)
        # A requeue is a brand-new archive request: it bills at the
        # creation price even though its dedupe key rides the task id.
        price_action = "create" if action == "requeue" else action
        price = store.get_price(db_session, f"price_{price_action}_{archive_type}")
        if price <= 0:
            return None

        if action == "download":
            ref = f"dl:{task_id}:{download_seq}"
            dedupe = f"ehd_dl:{task_id}:{int(time.time() // _SPEND_WINDOW)}"
            meter = "download"
        elif action == "requeue":
            ref = f"task:{gid}:{token}:{archive_type}"
            dedupe = f"ehd_requeue:{task_id}:{int(time.time() // _SPEND_WINDOW)}"
            meter = None
        else:
            ref = f"task:{gid}:{token}:{archive_type}"
            dedupe = f"ehd_task:{gid}:{token}:{archive_type}"
            meter = None

        try:
            return self.spend(user_id, price, "spend_eh", ref, dedupe=dedupe, meter=meter)
        except IntegrationError as exc:
            detail = str(exc)
            if exc.balance is not None:
                detail = f"{detail} (current balance: {exc.balance})"
            raise HTTPException(status_code=exc.status_code, detail=detail) from exc

    # --- Raw site surface ----------------------------------------------------

    def issue_ticket(self, bearer: str) -> str:
        data = self._request(
            "POST",
            _TICKET_PATH,
            headers={"Authorization": f"Bearer {bearer}"},
            error_prefix="Site sign-in failed",
        )
        ticket = data.get("ticket")
        if not isinstance(ticket, str) or not ticket:
            raise IntegrationError("Site sign-in failed: malformed ticket answer", 502)
        return ticket

    def redeem_ticket(self, ticket: str) -> dict[str, Any]:
        data = self._request(
            "POST",
            _REDEEM_PATH,
            json_body={"ticket": ticket},
            error_prefix="Site sign-in failed",
        )
        userId = int(data.get("userId") or 0)
        if userId <= 0:
            raise IntegrationError("Site sign-in failed: malformed identity answer", 502)
        return {
            "userId": userId,
            "displayName": str(data.get("displayName") or ""),
            "banned": bool(data.get("banned")),
        }

    def spend(
        self,
        user_id: int,
        amount: int,
        source: str,
        ref: str,
        dedupe: str | None = None,
        meter: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"userId": user_id, "amount": amount, "source": source, "ref": ref}
        if dedupe is not None:
            body["dedupe"] = dedupe
        if meter is not None:
            body["meter"] = meter
        data = self._request("POST", _SPEND_PATH, json_body=body, error_prefix="Credit payment failed")
        return {"balance": int(data.get("balance") or 0), "duplicate": bool(data.get("duplicate"))}

    def balance(self, user_id: int) -> int:
        data = self._request("GET", _BALANCE_PATH, params={"userId": user_id}, error_prefix="Balance read failed")
        return int(data.get("balance") or 0)

    # --- Transport -----------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        error_prefix: str,
    ) -> dict[str, Any]:
        request_headers = {"Authorization": f"Bearer {self.settings.aiya_core_service_key}"}
        if headers:
            request_headers.update(headers)
        try:
            with httpx.Client(
                base_url=self.settings.aiya_core_base_url,
                timeout=httpx.Timeout(self.settings.aiya_core_timeout_seconds),
                transport=self._transport,
            ) as client:
                response = client.request(method, path, headers=request_headers, json=json_body, params=params)
        except httpx.HTTPError:
            raise IntegrationError(f"{error_prefix}: the site service is unreachable", 503)
        if response.status_code >= 400:
            raise IntegrationError(
                self._error_message(response, error_prefix),
                response.status_code,
                balance=self._error_balance(response),
            )
        try:
            data = response.json()
        except ValueError:
            raise IntegrationError(f"{error_prefix}: malformed answer", 502)
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _error_message(response: httpx.Response, error_prefix: str) -> str:
        try:
            body = response.json()
        except ValueError:
            return f"{error_prefix} (HTTP {response.status_code})"
        if isinstance(body, dict):
            detail = body.get("detail")
            if isinstance(detail, dict) and isinstance(detail.get("message"), str):
                return detail["message"]
            if isinstance(detail, str) and detail:
                return detail
            if isinstance(body.get("message"), str) and body["message"]:
                return body["message"]
        return f"{error_prefix} (HTTP {response.status_code})"

    @staticmethod
    def _error_balance(response: httpx.Response) -> int | None:
        try:
            body = response.json()
        except ValueError:
            return None
        if isinstance(body, dict):
            data = body.get("data")
            if isinstance(data, dict) and isinstance(data.get("balance"), int):
                return data["balance"]
        return None

    @staticmethod
    def _cache_key(bearer: str) -> str:
        return hashlib.sha256(bearer.encode("utf-8")).hexdigest()[:24]
