from .archive_worker import ArchiveWorker
from .auth_service import AuthService
from .cache_service import CacheService
from .config import get_settings
from .database import Database
from .security import SecurityManager
from .settings_store import SettingsStore
from .task_service import TaskService


def main() -> None:
    settings = get_settings()
    database = Database(settings)
    security = SecurityManager(settings)
    store = SettingsStore(settings)
    ArchiveWorker(
        settings,
        database,
        store,
        AuthService(settings, security, store),
        CacheService(settings, store),
        TaskService(settings),
    ).run_forever()


if __name__ == "__main__":
    main()
