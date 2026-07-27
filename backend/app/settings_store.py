from sqlalchemy.orm import Session

from .config import Settings
from .models import AppSetting


class SettingsStore:
    TOKEN_HASH_KEY = "api_token_hash"
    GUEST_CACHE_ACCESS_KEY = "guest_cache_access"
    GUEST_DOWNLOAD_MODE_KEY = "guest_download_mode"
    RETENTION_KEY = "retention_days"
    CACHE_LIMIT_KEY = "cache_limit_bytes"
    MAX_ARCHIVE_SIZE_MB_KEY = "max_archive_size_mb"
    CONCURRENCY_KEY = "worker_concurrency"
    CACHE_ENABLED_KEY = "cache_enabled"

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

    def get_guest_cache_access(self, session: Session) -> bool:
        return self._get(session, self.GUEST_CACHE_ACCESS_KEY) == "true"

    def get_guest_download_mode(self, session: Session) -> str:
        if not self.get_guest_cache_access(session):
            return "disabled"
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

    def update_public_settings(
        self, session: Session, *, guest_cache_access: bool, guest_download_mode: str,
        cache_enabled: bool, retention_days: int, cache_limit_bytes: int,
        max_archive_size_mb: int, worker_concurrency: int
    ) -> None:
        self._set(session, self.GUEST_CACHE_ACCESS_KEY, "true" if guest_cache_access else "false")
        self._set(session, self.GUEST_DOWNLOAD_MODE_KEY, guest_download_mode if guest_cache_access else "disabled")
        self._set(session, self.CACHE_ENABLED_KEY, "true" if cache_enabled else "false")
        self._set(session, self.RETENTION_KEY, str(retention_days))
        self._set(session, self.CACHE_LIMIT_KEY, str(cache_limit_bytes))
        self._set(session, self.MAX_ARCHIVE_SIZE_MB_KEY, str(max_archive_size_mb))
        self._set(session, self.CONCURRENCY_KEY, str(worker_concurrency))

    def set_api_token_hash(self, session: Session, token_hash: str) -> None:
        self._set(session, self.TOKEN_HASH_KEY, token_hash)
