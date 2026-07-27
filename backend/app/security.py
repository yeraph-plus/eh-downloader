import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken

from .config import Settings


@dataclass(frozen=True)
class SessionIdentity:
    kind: str
    username: str
    csrf_token: str


class SecurityManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        key = base64.urlsafe_b64encode(hashlib.sha256(settings.app_secret.encode("utf-8")).digest())
        self.fernet = Fernet(key)
        self.signing_key = hashlib.sha256((settings.app_secret + ":session").encode("utf-8")).digest()

    def verify_admin(self, username: str, password: str) -> bool:
        return hmac.compare_digest(username, self.settings.admin_username) and hmac.compare_digest(
            password,
            self.settings.admin_password,
        )

    def create_session(self) -> tuple[str, SessionIdentity]:
        csrf_token = secrets.token_urlsafe(24)
        payload = {
            "username": self.settings.admin_username,
            "csrf": csrf_token,
            "expires": int(time.time()) + self.settings.session_ttl_seconds,
        }
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        encoded = base64.urlsafe_b64encode(raw).decode("ascii")
        signature = hmac.new(self.signing_key, encoded.encode("ascii"), hashlib.sha256).hexdigest()
        return f"{encoded}.{signature}", SessionIdentity("admin", payload["username"], csrf_token)

    def parse_session(self, value: str | None) -> SessionIdentity | None:
        if not value or "." not in value:
            return None
        encoded, signature = value.rsplit(".", 1)
        expected = hmac.new(self.signing_key, encoded.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        try:
            payload = json.loads(base64.urlsafe_b64decode(encoded.encode("ascii")))
        except (ValueError, json.JSONDecodeError):
            return None
        if int(payload.get("expires", 0)) < int(time.time()):
            return None
        if payload.get("username") != self.settings.admin_username:
            return None
        return SessionIdentity("admin", payload["username"], payload["csrf"])

    def encrypt(self, value: str) -> str:
        return self.fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        try:
            return self.fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Stored secret cannot be decrypted with APP_SECRET") from exc

    @staticmethod
    def generate_api_token() -> str:
        return f"ehd_{secrets.token_urlsafe(32)}"

    @staticmethod
    def hash_api_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()
