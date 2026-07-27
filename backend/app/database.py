from collections.abc import Generator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import Settings


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, settings: Settings):
        connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
        self.engine = create_engine(settings.database_url, connect_args=connect_args)
        if settings.database_url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def configure_sqlite(dbapi_connection, _):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=10000")
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)
        if self.engine.url.get_backend_name() == "sqlite":
            self._migrate_sqlite()

    def _migrate_sqlite(self) -> None:
        additions = {
            "accounts": {
                "last_used_at": "DATETIME",
                "last_download_cost": "INTEGER",
                "last_download_cost_type": "VARCHAR(20)",
            },
            "archives": {"cache_enabled": "BOOLEAN NOT NULL DEFAULT 1"},
            "tasks": {"download_count": "INTEGER NOT NULL DEFAULT 0"},
        }
        with self.engine.begin() as connection:
            inspector = inspect(connection)
            tables = set(inspector.get_table_names())
            for table, columns in additions.items():
                if table not in tables:
                    continue
                existing = {column["name"] for column in inspector.get_columns(table)}
                for name, declaration in columns.items():
                    if name not in existing:
                        connection.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {declaration}'))
            if "tasks" in tables:
                connection.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_tasks_gallery_archive "
                    "ON tasks (gid, token, archive_type)"
                ))

    def session(self) -> Generator[Session, None, None]:
        with self.session_factory() as session:
            yield session
