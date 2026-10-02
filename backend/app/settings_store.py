from sqlalchemy import Integer, func
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from .config import Settings
from .models import AppSetting


class SettingsStore:
    TOKEN_HASH_KEY = "api_token_hash"
    ACCESS_MODE_KEY = "access_mode"
    CORE_SITE_URL_KEY = "core_site_url"
    GUEST_DOWNLOAD_MODE_KEY = "guest_download_mode"
    RETENTION_KEY = "retention_days"
    CACHE_LIMIT_KEY = "cache_limit_bytes"
    MAX_ARCHIVE_SIZE_MB_KEY = "max_archive_size_mb"
    CONCURRENCY_KEY = "worker_concurrency"
    CACHE_ENABLED_KEY = "cache_enabled"
    PRICE_CREATE_ORIGINAL_KEY = "price_create_original"
    PRICE_CREATE_RESAMPLE_KEY = "price_create_resample"
    PRICE_DOWNLOAD_ORIGINAL_KEY = "price_download_original"
    PRICE_DOWNLOAD_RESAMPLE_KEY = "price_download_resample"
    EH_PROXY_URL_KEY = "eh_proxy_url"
    TASK_MAX_RETRIES_KEY = "task_max_retries"
    STATS_CREDIT_SPENT_KEY = "stats_credit_spent"
    STATS_DOWNLOADS_SERVED_KEY = "stats_downloads_served"
    STATS_BYTES_DOWNLOADED_KEY = "stats_bytes_downloaded"
    STATS_WORKER_BEAT_KEY = "stats_worker_beat"

    # 站点积分默认定价：原始归档创建 5 / 下载 1，重采样归档创建 0（免费）/
    # 下载 0。设置页未保存过的键按此默认计费。
    DEFAULT_PRICES = {
        PRICE_CREATE_ORIGINAL_KEY: 5,
        PRICE_CREATE_RESAMPLE_KEY: 0,
        PRICE_DOWNLOAD_ORIGINAL_KEY: 1,
        PRICE_DOWNLOAD_RESAMPLE_KEY: 0,
    }

    # 每任务可重试次数上限的默认值（可重试的网络类错误），0 = 不重试。
    DEFAULT_TASK_MAX_RETRIES = 3

    def __init__(self, defaults: Settings):
        self.defaults = defaults

    @staticmethod
    def _get(session: Session, key: str) -> str | None:
        value = session.get(AppSetting, key)
        return value.value if value else None

    @staticmethod
    def _set(session: Session, key: str, value: str) -> None:
        setting = session.get(AppSetting, key)
        if setting:
            setting.value = value
        else:
            session.add(AppSetting(key=key, value=value))

    def get_access_mode(self, session: Session) -> str:
        value = self._get(session, self.ACCESS_MODE_KEY)
        if value == "core" and not self.defaults.aiya_core_enabled:
            # Integration master switch off: a stored core mode reads back
            # as guest so no surface ever advertises the hidden mode.
            return "guest"
        return value if value in {"admin", "guest", "core"} else "admin"

    def get_core_site_url(self, session: Session) -> str:
        """The saved main-site link for the visitor sign-in redirect
        (认证主站链接); the env login-page URL remains the fallback."""
        return self._get(session, self.CORE_SITE_URL_KEY) or ""

    def get_guest_download_mode(self, session: Session) -> str:
        value = self._get(session, self.GUEST_DOWNLOAD_MODE_KEY) or "disabled"
        return value if value in {"disabled", "resample", "original"} else "disabled"

    def get_retention_days(self, session: Session) -> int:
        return int(self._get(session, self.RETENTION_KEY) or self.defaults.default_retention_days)

    def get_cache_limit_bytes(self, session: Session) -> int:
        return int(self._get(session, self.CACHE_LIMIT_KEY) or self.defaults.default_cache_limit_bytes)

    def get_max_archive_size_mb(self, session: Session) -> int:
        return int(self._get(session, self.MAX_ARCHIVE_SIZE_MB_KEY) or self.defaults.default_max_archive_size_mb)

    def get_worker_concurrency(self, session: Session) -> int:
        return int(self._get(session, self.CONCURRENCY_KEY) or self.defaults.default_worker_concurrency)

    def get_cache_enabled(self, session: Session) -> bool:
        return self._get(session, self.CACHE_ENABLED_KEY) != "false"

    def get_api_token_hash(self, session: Session) -> str | None:
        return self._get(session, self.TOKEN_HASH_KEY)

    def get_price(self, session: Session, key: str) -> int:
        """Site-credit price for one integration action (`price_*` keys);
        0 means the action is free. Unset keys answer the table's default
        price, garbage answers 0."""
        try:
            raw = self._get(session, key)
            return max(0, int(raw)) if raw is not None else self.DEFAULT_PRICES.get(key, 0)
        except ValueError:
            return 0

    def get_eh_proxy_url(self, session: Session) -> str:
        """EH requests' HTTP/SOCKS proxy (设置页「EH 代理」); empty means
        direct connection. Only the save path validates the scheme, this
        reader stays lenient so a bad value can never crash a worker."""
        return (self._get(session, self.EH_PROXY_URL_KEY) or "").strip()

    def get_task_max_retries(self, session: Session) -> int:
        """How many times one task may retry a retryable error; unset keys
        answer the historical default of 3, garbage clamps into 0–5."""
        try:
            raw = self._get(session, self.TASK_MAX_RETRIES_KEY)
            return max(0, min(5, int(raw))) if raw is not None else self.DEFAULT_TASK_MAX_RETRIES
        except ValueError:
            return self.DEFAULT_TASK_MAX_RETRIES

    def update_public_settings(
        self, session: Session, *, access_mode: str, core_site_url: str, guest_download_mode: str,
        cache_enabled: bool, retention_days: int, cache_limit_bytes: int,
        max_archive_size_mb: int, worker_concurrency: int,
        price_create_original: int, price_create_resample: int,
        price_download_original: int, price_download_resample: int,
        eh_proxy_url: str, task_max_retries: int,
    ) -> None:
        # core is only storable while the integration master switch is on;
        # with the switch off a submitted core falls back to guest (same
        # read-side coercion as get_access_mode), other junk to admin.
        if access_mode == "core" and not self.defaults.aiya_core_enabled:
            access_mode = "guest"
        allowed_modes = {"admin", "guest", "core"} if self.defaults.aiya_core_enabled else {"admin", "guest"}
        self._set(session, self.ACCESS_MODE_KEY, access_mode if access_mode in allowed_modes else "admin")
        # Only http(s) URLs are stored: the value is rendered straight into
        # the visitor hint's href, so anything else is dropped to empty.
        site_url = core_site_url.strip()[:255]
        if not site_url.startswith(("http://", "https://")):
            site_url = ""
        self._set(session, self.CORE_SITE_URL_KEY, site_url)
        self._set(session, self.GUEST_DOWNLOAD_MODE_KEY, guest_download_mode)
        self._set(session, self.CACHE_ENABLED_KEY, "true" if cache_enabled else "false")
        self._set(session, self.RETENTION_KEY, str(retention_days))
        self._set(session, self.CACHE_LIMIT_KEY, str(cache_limit_bytes))
        self._set(session, self.MAX_ARCHIVE_SIZE_MB_KEY, str(max_archive_size_mb))
        self._set(session, self.CONCURRENCY_KEY, str(worker_concurrency))
        self._set(session, self.PRICE_CREATE_ORIGINAL_KEY, str(max(0, price_create_original)))
        self._set(session, self.PRICE_CREATE_RESAMPLE_KEY, str(max(0, price_create_resample)))
        self._set(session, self.PRICE_DOWNLOAD_ORIGINAL_KEY, str(max(0, price_download_original)))
        self._set(session, self.PRICE_DOWNLOAD_RESAMPLE_KEY, str(max(0, price_download_resample)))
        # EH 代理只接受 http(s)/socks5 scheme；其他值一律丢弃为直连，
        # 与 core_site_url 同一套「存前归一」风格。
        proxy = eh_proxy_url.strip()[:255]
        if not proxy.startswith(("http://", "https://", "socks5://", "socks5h://")):
            proxy = ""
        self._set(session, self.EH_PROXY_URL_KEY, proxy)
        self._set(session, self.TASK_MAX_RETRIES_KEY, str(max(0, min(5, task_max_retries))))

    def set_api_token_hash(self, session: Session, token_hash: str) -> None:
        self._set(session, self.TOKEN_HASH_KEY, token_hash)

    def upsert_value(self, session: Session, key: str, value: str) -> None:
        """Insert-or-overwrite without the read-modify-write window of
        `_set` (missing-key inserts from two processes can race on the PK);
        used for last-writer-wins values like the worker heartbeat."""
        statement = sqlite_insert(AppSetting).values(key=key, value=value)
        statement = statement.on_conflict_do_update(index_elements=[AppSetting.key], set_={"value": value})
        session.execute(statement)

    def bump_counter(self, session: Session, key: str, amount: int) -> None:
        """Atomic counter increment for the public stats page. Deliberately
        NOT `_get`+`_set`: the web and worker processes (and concurrent
        requests) would read-modify-write over each other; a SQLite-level
        upsert cannot lose an update. Garbage values cast to 0 by the DB."""
        statement = sqlite_insert(AppSetting).values(key=key, value=str(amount))
        statement = statement.on_conflict_do_update(
            index_elements=[AppSetting.key],
            set_={"value": func.cast(AppSetting.value, Integer) + amount},
        )
        session.execute(statement)

    def get_counter(self, session: Session, key: str) -> int:
        try:
            raw = self._get(session, key)
            return max(0, int(raw)) if raw is not None else 0
        except ValueError:
            return 0
