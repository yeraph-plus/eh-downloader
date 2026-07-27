import hashlib
import re
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .eh_client import EHClient, EHClientError
from .models import Account, utcnow
from .security import SecurityManager


COOKIE_DOMAIN_RE = re.compile(r"(^|\.)e-hentai\.org$|(^|\.)exhentai\.org$", re.IGNORECASE)


class CookieFormatError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedCookies:
    header: str
    fingerprint: str


def parse_netscape_cookies(value: str) -> ParsedCookies:
    cookies: dict[str, str] = {}
    for raw_line in value.replace("\r\n", "\n").split("\n"):
        line = raw_line.strip()
        if not line or line.startswith("#") and not line.startswith("#HttpOnly_"):
            continue
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_") :]
        fields = line.split("\t")
        if len(fields) != 7:
            raise CookieFormatError("Invalid Netscape cookies.txt row; expected 7 tab-separated fields")
        domain, _, _, _, _, name, cookie_value = fields
        domain = domain.lstrip(".").lower()
        if not COOKIE_DOMAIN_RE.search(domain):
            continue
        if not name or any(char in name + cookie_value for char in "\r\n;"):
            raise CookieFormatError("Invalid cookie name or value")
        cookies[name] = cookie_value
    required = {"ipb_member_id", "ipb_pass_hash"}
    if not required.issubset(cookies):
        raise CookieFormatError("cookies.txt must contain ipb_member_id and ipb_pass_hash for EH")
    header = "; ".join(f"{name}={cookies[name]}" for name in sorted(cookies))
    return ParsedCookies(header, hashlib.sha256(header.encode("utf-8")).hexdigest())


class AuthService:
    def __init__(self, settings: Settings, security: SecurityManager):
        self.settings = settings
        self.security = security

    def get_cookie(self, account: Account) -> str:
        return self.security.decrypt(account.encrypted_cookie)

    def add_account(self, session: Session, name: str, cookies_txt: str, priority: int) -> Account:
        parsed = parse_netscape_cookies(cookies_txt)
        account = Account(
            name=name.strip(),
            encrypted_cookie=self.security.encrypt(parsed.header),
            cookie_fingerprint=parsed.fingerprint,
            priority=priority,
        )
        session.add(account)
        session.flush()
        return account

    def eligible_accounts(self, session: Session) -> list[Account]:
        now = utcnow()
        return list(
            session.scalars(
                select(Account)
                .where(
                    Account.enabled.is_(True),
                    (Account.cooldown_until.is_(None)) | (Account.cooldown_until <= now),
                )
                .order_by(Account.priority.asc(), Account.last_used_at.asc().nullsfirst())
            )
        )

    def validate_account(self, session: Session, account: Account) -> None:
        client = EHClient(self.get_cookie(account), self.settings)
        try:
            client.validate_login()
        except EHClientError as exc:
            account.last_check = utcnow()
            account.last_error = str(exc)
            if exc.authentication:
                account.enabled = False
            else:
                account.cooldown_reason = str(exc)
                account.cooldown_until = utcnow() + timedelta(seconds=self.settings.account_cooldown_seconds)
            raise
        account.last_check = utcnow()
        account.last_error = None
        account.cooldown_reason = None
        account.cooldown_until = None
