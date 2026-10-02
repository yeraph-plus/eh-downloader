import hashlib
import os
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .models import Archive, CacheEntry, DownloadTask, TaskStatus, utcnow
from .settings_store import SettingsStore


class CacheService:
    def __init__(self, settings: Settings, store: SettingsStore):
        self.settings = settings
        self.store = store

    def root(self) -> Path:
        root = self.settings.cache_dir.resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    def path_for(self, relative: str) -> Path:
        path = (self.root() / relative).resolve()
        if path.parent != self.root():
            raise ValueError("Unsafe cache path")
        return path

    def used_bytes(self) -> int:
        return sum(path.stat().st_size for path in self.root().iterdir() if path.is_file())

    def available_bytes(self, session: Session) -> int:
        """Free space under the configured cache limit; a limit of 0 means
        unlimited and answers a sentinel no reservation can exhaust."""
        limit = self.store.get_cache_limit_bytes(session)
        if limit <= 0:
            return 2**62
        return max(0, limit - self.used_bytes())

    @staticmethod
    def verify_zip(path: Path, expected_size: int | None = None) -> str:
        if expected_size is not None and path.stat().st_size != expected_size:
            raise ValueError("Downloaded size does not match Content-Length")
        digest = hashlib.sha1()
        with path.open("rb") as source:
            if source.read(4)[:2] != b"PK":
                raise ValueError("Downloaded file is not a ZIP archive")
            source.seek(0)
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        try:
            with zipfile.ZipFile(path) as archive:
                if not archive.infolist():
                    raise ValueError("ZIP archive is empty")
                if archive.testzip() is not None:
                    raise ValueError("ZIP CRC validation failed")
        except zipfile.BadZipFile as exc:
            raise ValueError("Downloaded file is not a valid ZIP archive") from exc
        return digest.hexdigest()

    def commit(self, session: Session, partial: Path, sha1: str, filename: str) -> CacheEntry:
        relative = f"{sha1}.zip"
        target = self.path_for(relative)
        if target.exists():
            partial.unlink(missing_ok=True)
        else:
            os.replace(partial, target)
        entry = session.get(CacheEntry, sha1)
        if not entry:
            retention_days = self.store.get_retention_days(session)
            entry = CacheEntry(
                sha1=sha1,
                zip_path=relative,
                filename=filename,
                filesize=target.stat().st_size,
                expire_at=(datetime.max if retention_days == 0 else utcnow() + timedelta(days=retention_days)),
            )
            session.add(entry)
        return entry

    def cleanup(self, session: Session) -> None:
        for partial in self.root().glob(".*.download"):
            task_id = partial.name[1:-len(".download")]
            task = session.get(DownloadTask, task_id)
            if task is None or task.status in {TaskStatus.FAILED.value, TaskStatus.EXPIRED.value} or (
                task.status == TaskStatus.QUEUED.value and task.archive_id is None
            ):
                try:
                    partial.unlink(missing_ok=True)
                except OSError:
                    pass

        entries = list(session.scalars(select(CacheEntry)))
        for entry in entries:
            try:
                path = self.path_for(entry.zip_path)
            except ValueError:
                path = None
            file_exists = bool(path and path.is_file())
            if entry.expire_at > utcnow() and file_exists:
                continue
            if file_exists:
                try:
                    path.unlink()
                except OSError:
                    continue
            self._delete_entry_records(session, entry)
        session.commit()

    @staticmethod
    def _delete_entry_records(session: Session, entry: CacheEntry) -> None:
        archives = list(entry.archives)
        for archive in archives:
            for task in list(archive.tasks):
                task.archive = None
                session.delete(task)
            archive.cache_entry = None
        session.flush()
        for archive in archives:
            session.delete(archive)
        session.flush()
        session.delete(entry)

    def remove_entry(self, session: Session, entry: CacheEntry) -> None:
        try:
            self.path_for(entry.zip_path).unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
        self._delete_entry_records(session, entry)
        session.commit()
