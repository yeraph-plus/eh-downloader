import os
import sys
import uuid
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("APP_SECRET", "test-secret-that-is-at-least-thirty-two-characters")
os.environ.setdefault("ADMIN_PASSWORD", "test-password-123")
os.environ.setdefault("SECURE_COOKIES", "false")

from app.config import Settings


@pytest.fixture
def test_settings() -> Settings:
    tmp_path = BACKEND_ROOT / "test-output" / str(uuid.uuid4())
    tmp_path.mkdir(parents=True)
    return Settings(
        app_secret="test-secret-that-is-at-least-thirty-two-characters",
        admin_username="admin",
        admin_password="test-password-123",
        database_url=f"sqlite:///{tmp_path / 'eh-downloader.db'}",
        cache_dir=tmp_path / "cache",
        frontend_dir=tmp_path / "missing-frontend",
        worker_poll_seconds=0.25,
        secure_cookies=False,
    )
