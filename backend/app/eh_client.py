import ipaddress
import re
import socket
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Iterator
from typing import Callable
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from .config import Settings


GALLERY_PATH_RE = re.compile(r"^/g/(?P<gid>\d+)/(?P<token>[a-fA-F0-9]+)/?$")
EH_HOSTS = {"e-hentai.org", "exhentai.org"}
FUNDS_RE = re.compile(r"([0-9,]+)\s+GP[\s\S]*?([0-9,]+)\s+Credits", re.IGNORECASE)
SIZE_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*(B|KiB|MiB|GiB|TiB)", re.IGNORECASE)
COST_RE = re.compile(r"([0-9,]+)\s*GP", re.IGNORECASE)
PENDING_RE = re.compile(r"generat|prepar|build|please wait|in progress|queued", re.IGNORECASE)
ERROR_RE = re.compile(r"not enough|insufficient|invalid|unavailable|unable|failed|error|must be logged in", re.IGNORECASE)
UNLOCKED_RE = re.compile(
    r"You\s+unlocked\s+an?\s+(original|resample)\s+download\s+of\s+this\s+archive\s+on\b",
    re.IGNORECASE,
)


class EHClientError(RuntimeError):
    def __init__(self, message: str, *, authentication: bool = False, retryable: bool = False):
        super().__init__(message)
        self.authentication = authentication
        self.retryable = retryable


class UnknownSubmission(EHClientError):
    pass


@dataclass(frozen=True)
class GalleryRef:
    host: str
    gid: int
    token: str

    @property
    def base_url(self) -> str:
        return f"https://{self.host}"

    @property
    def gallery_url(self) -> str:
        return f"{self.base_url}/g/{self.gid}/{self.token}/"


@dataclass(frozen=True)
class ArchiveForm:
    archive_type: str
    action: str
    fields: dict[str, str]
    estimated_size: int | None
    cost: int
    cost_type: str
    confirmation: bool


@dataclass(frozen=True)
class ArchivePage:
    title: str | None
    gp: int | None
    credits: int | None
    forms: dict[str, ArchiveForm]
    state: str
    download_url: str | None
    message: str
    unlocked_types: frozenset[str] = frozenset()


def parse_gallery_url(value: str) -> GalleryRef:
    parsed = urlparse(value.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in EH_HOSTS:
        raise ValueError("Only HTTPS E-Hentai and ExHentai gallery URLs are supported")
    match = GALLERY_PATH_RE.match(parsed.path)
    if not match or parsed.query or parsed.fragment:
        raise ValueError("Invalid gallery URL; expected /g/{gid}/{token}/")
    return GalleryRef(host, int(match.group("gid")), match.group("token"))


def parse_size(value: str) -> int | None:
    match = SIZE_RE.search(value)
    if not match:
        return None
    powers = {"b": 0, "kib": 1, "mib": 2, "gib": 3, "tib": 4}
    return int(float(match.group(1)) * 1024 ** powers[match.group(2).lower()])


def _form_fields(form) -> dict[str, str]:
    fields: dict[str, str] = {}
    for element in form.select("input[name], button[name]"):
        if element.get("disabled") is not None:
            continue
        name = str(element.get("name"))
        kind = str(element.get("type", "")).lower()
        if kind in {"checkbox", "radio"} and element.get("checked") is None:
            continue
        fields[name] = str(element.get("value", ""))
    return fields


def parse_archive_page(html: str, base_url: str) -> ArchivePage:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    funds = FUNDS_RE.search(text)
    gp = int(funds.group(1).replace(",", "")) if funds else None
    credits = int(funds.group(2).replace(",", "")) if funds else None
    title_element = soup.select_one("#db h1") or soup.select_one("h1")
    unlocked_types = frozenset(match.group(1).lower() for match in UNLOCKED_RE.finditer(text))
    forms: dict[str, ArchiveForm] = {}
    for form in soup.find_all("form"):
        fields = _form_fields(form)
        dltype = fields.get("dltype")
        if dltype not in {"org", "res"} or "hathdl_xres" in fields or form.get("id") == "hathdl_form":
            continue
        archive_type = "original" if dltype == "org" else "resample"
        container = form.parent
        context = container.get_text(" ", strip=True) if container else form.get_text(" ", strip=True)
        size = parse_size(context)
        if archive_type in unlocked_types:
            cost, cost_type = 0, "unlocked"
        elif re.search(r"Download Cost:\s*Free!", context, re.IGNORECASE):
            cost, cost_type = 0, "free"
        else:
            cost_match = COST_RE.search(context)
            cost, cost_type = (int(cost_match.group(1).replace(",", "")), "gp") if cost_match else (0, "unknown")
        confirmation = any(
            "confirm" in name.lower() or "confirm" in value.lower()
            for name, value in fields.items()
        )
        forms[archive_type] = ArchiveForm(
            archive_type,
            urljoin(base_url, str(form.get("action") or base_url)),
            fields,
            size,
            cost,
            cost_type,
            confirmation,
        )

    download_url = None
    for anchor in soup.find_all("a", href=True):
        href = urljoin(base_url, str(anchor["href"]))
        label = anchor.get_text(" ", strip=True)
        parsed = urlparse(href)
        if re.search(r"download|click here", label, re.IGNORECASE) and (
            "archiver.php" in parsed.path or "archive" in parsed.path or "download" in parsed.path
        ):
            download_url = href
            break
    if not download_url:
        refresh = soup.select_one('meta[http-equiv="refresh" i][content]')
        if refresh:
            match = re.search(r"url\s*=\s*(.+)$", str(refresh.get("content")), re.IGNORECASE)
            if match:
                download_url = urljoin(base_url, match.group(1).strip(" '\""))
    if download_url:
        state = "ready"
    elif PENDING_RE.search(text) and not forms:
        state = "pending"
    elif forms:
        state = "confirmation" if any(form.confirmation for form in forms.values()) else "quote"
    elif ERROR_RE.search(text):
        state = "error"
    else:
        state = "unknown"
    message_node = soup.select_one("#db")
    return ArchivePage(
        title_element.get_text(" ", strip=True) if title_element else None,
        gp,
        credits,
        forms,
        state,
        download_url,
        (message_node or soup).get_text(" ", strip=True)[:2000],
        unlocked_types,
    )


def is_eh_host(host: str) -> bool:
    host = host.lower().rstrip(".")
    return any(host == allowed or host.endswith(f".{allowed}") for allowed in EH_HOSTS)


def validate_remote_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise EHClientError("EH returned an unsafe download URL")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise EHClientError(f"Download host lookup failed: {exc}", retryable=True) from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise EHClientError("EH download URL resolves to a non-public address")


class EHClient:
    def __init__(self, cookie: str, settings: Settings):
        self.cookie = cookie
        self.settings = settings
        self.base_headers = {
            "User-Agent": "EhDownloader/1.0",
            "Accept": "text/html,application/xhtml+xml,application/zip,*/*",
            "Accept-Encoding": "identity",
        }

    def _client(self, *, cookie: bool = True) -> httpx.Client:
        headers = dict(self.base_headers)
        if cookie:
            headers["Cookie"] = self.cookie
        return httpx.Client(
            headers=headers,
            proxy=self.settings.eh_proxy_url,
            timeout=httpx.Timeout(self.settings.eh_request_timeout_seconds),
            follow_redirects=False,
        )

    @staticmethod
    def _authenticated(response: httpx.Response) -> None:
        text = response.text.lower()
        if response.status_code in {401, 403} or "you must be logged in" in text or "please log in" in text:
            raise EHClientError("EH Cookie is not authenticated", authentication=True)

    def validate_login(self) -> None:
        try:
            with self._client() as client:
                response = client.get("https://e-hentai.org/home.php")
        except httpx.HTTPError as exc:
            raise EHClientError(f"EH account check failed: {exc}", retryable=True) from exc
        self._authenticated(response)
        if response.status_code != 200:
            raise EHClientError(f"EH account check returned HTTP {response.status_code}", retryable=True)

    def get_archive_page(self, gallery: GalleryRef) -> tuple[str, ArchivePage]:
        try:
            with self._client() as client:
                detail = client.get(gallery.gallery_url)
                self._authenticated(detail)
                if detail.status_code != 200:
                    raise EHClientError(f"Gallery request returned HTTP {detail.status_code}", retryable=True)
                soup = BeautifulSoup(detail.text, "html.parser")
                archiver_url = next(
                    (urljoin(gallery.base_url, str(a["href"])) for a in soup.find_all("a", href=True) if "archiver.php" in str(a["href"])),
                    f"{gallery.base_url}/archiver.php?gid={gallery.gid}&token={gallery.token}",
                )
                validate_remote_url(archiver_url)
            with self._client(cookie=is_eh_host(urlparse(archiver_url).hostname or "")) as client:
                response = client.get(archiver_url)
        except httpx.HTTPError as exc:
            raise EHClientError(f"EH archive page request failed: {exc}", retryable=True) from exc
        self._authenticated(response)
        if response.status_code != 200:
            raise EHClientError(f"Archive page returned HTTP {response.status_code}", retryable=True)
        return archiver_url, parse_archive_page(response.text, archiver_url)

    def load_archive_page(self, archiver_url: str) -> ArchivePage:
        validate_remote_url(archiver_url)
        try:
            with self._client(cookie=is_eh_host(urlparse(archiver_url).hostname or "")) as client:
                response = client.get(archiver_url)
        except httpx.HTTPError as exc:
            raise EHClientError(f"Archive status request failed: {exc}", retryable=True) from exc
        self._authenticated(response)
        if response.status_code != 200:
            raise EHClientError(f"Archive status returned HTTP {response.status_code}", retryable=True)
        return parse_archive_page(response.text, archiver_url)

    def submit_archive(self, form: ArchiveForm) -> ArchivePage:
        validate_remote_url(form.action)
        try:
            with self._client(cookie=is_eh_host(urlparse(form.action).hostname or "")) as client:
                response = client.post(form.action, data=form.fields)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise UnknownSubmission(f"Archive submission result is unknown: {exc}", retryable=True) from exc
        self._authenticated(response)
        if response.status_code in {301, 302, 303, 307, 308}:
            location = urljoin(form.action, response.headers.get("location", ""))
            validate_remote_url(location)
            if urlparse(location).path.endswith("/archiver.php"):
                try:
                    with self._client(cookie=is_eh_host(urlparse(location).hostname or "")) as redirect_client:
                        follow = redirect_client.get(location)
                except httpx.HTTPError as exc:
                    raise UnknownSubmission(f"Archive redirect result is unknown: {exc}", retryable=True) from exc
                if follow.status_code != 200:
                    raise UnknownSubmission("Archive redirect did not return a status page", retryable=True)
                response = follow
            else:
                return ArchivePage(None, None, None, {}, "ready", location, "Archive download is ready")
        self._authenticated(response)
        if response.status_code != 200:
            raise EHClientError(f"Archive submission returned HTTP {response.status_code}", retryable=response.status_code >= 500)
        page = parse_archive_page(response.text, form.action)
        if page.state == "confirmation":
            confirmation = page.forms.get(form.archive_type)
            if not confirmation:
                raise UnknownSubmission("EH confirmation page did not preserve the requested archive type", retryable=True)
            validate_remote_url(confirmation.action)
            try:
                with self._client(cookie=is_eh_host(urlparse(confirmation.action).hostname or "")) as confirmation_client:
                    response = confirmation_client.post(confirmation.action, data=confirmation.fields)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                raise UnknownSubmission(f"Archive confirmation result is unknown: {exc}", retryable=True) from exc
            self._authenticated(response)
            if response.status_code in {301, 302, 303, 307, 308}:
                location = urljoin(confirmation.action, response.headers.get("location", ""))
                validate_remote_url(location)
                if urlparse(location).path.endswith("/archiver.php"):
                    try:
                        with self._client(cookie=is_eh_host(urlparse(location).hostname or "")) as redirect_client:
                            response = redirect_client.get(location)
                    except httpx.HTTPError as exc:
                        raise UnknownSubmission(f"Archive confirmation redirect is unknown: {exc}", retryable=True) from exc
                    if response.status_code != 200:
                        raise UnknownSubmission("Archive confirmation redirect did not return a status page", retryable=True)
                else:
                    return ArchivePage(None, None, None, {}, "ready", location, "Archive download is ready")
            self._authenticated(response)
            if response.status_code != 200:
                raise EHClientError(
                    f"Archive confirmation returned HTTP {response.status_code}",
                    retryable=response.status_code >= 500,
                )
            page = parse_archive_page(response.text, confirmation.action)
        if page.state == "error":
            raise EHClientError(page.message)
        if page.state in {"unknown", "quote", "confirmation"}:
            raise UnknownSubmission("EH did not confirm whether archive creation was accepted", retryable=True)
        return page

    def iter_download(self, url: str, max_bytes: int | None = None) -> Iterator[tuple[bytes, int | None]]:
        current = url
        for _ in range(8):
            validate_remote_url(current)
            host = (urlparse(current).hostname or "").lower()
            send_cookie = is_eh_host(host)
            try:
                with self._client(cookie=send_cookie) as client, client.stream("GET", current) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        current = urljoin(current, response.headers.get("location", ""))
                        continue
                    if response.status_code != 200:
                        raise EHClientError(f"Archive download returned HTTP {response.status_code}", retryable=response.status_code >= 500)
                    total = int(response.headers["content-length"]) if response.headers.get("content-length", "").isdigit() else None
                    chunks = response.iter_bytes(1024 * 1024)
                    first = next(chunks, b"")
                    content_type = response.headers.get("content-type", "").lower()
                    if "text/html" in content_type or first.lstrip().lower().startswith((b"<!doctype html", b"<html")):
                        body_parts = [first]
                        body_size = len(first)
                        for chunk in chunks:
                            body_size += len(chunk)
                            if body_size > 2 * 1024 * 1024:
                                raise EHClientError("EH returned an unexpectedly large HTML download page")
                            body_parts.append(chunk)
                        body = b"".join(body_parts)
                        encoding = response.encoding or "utf-8"
                        html = body.decode(encoding, errors="replace")
                        lowered = html.lower()
                        if "you must be logged in" in lowered or "please log in" in lowered:
                            raise EHClientError("EH Cookie is not authenticated", authentication=True)
                        page = parse_archive_page(html, current)
                        if page.state != "ready" or not page.download_url or page.download_url == current:
                            raise EHClientError(f"EH download page did not contain a ZIP link: {page.message}")
                        current = page.download_url
                        continue
                    if not first.startswith(b"PK"):
                        raise EHClientError("EH download response is neither a ZIP archive nor a recognized download page")
                    if max_bytes is not None and total is not None and total > max_bytes:
                        raise EHClientError("Archive download exceeds the configured maximum size")
                    received = len(first)
                    if max_bytes is not None and received > max_bytes:
                        raise EHClientError("Archive download exceeds the configured maximum size")
                    if first:
                        yield first, total
                    for chunk in chunks:
                        if chunk:
                            received += len(chunk)
                            if max_bytes is not None and received > max_bytes:
                                raise EHClientError("Archive download exceeds the configured maximum size")
                            yield chunk, total
                    return
            except httpx.HTTPError as exc:
                raise EHClientError(f"Archive download failed: {exc}", retryable=True) from exc
        raise EHClientError("Archive download exceeded the redirect limit")

    def download(
        self, url: str, target: Path, progress: Callable[[int, int | None], None],
        max_bytes: int | None = None,
    ) -> int:
        written = 0
        expected_total: int | None = None
        with target.open("wb") as output:
            for chunk, total in self.iter_download(url, max_bytes=max_bytes):
                output.write(chunk)
                written += len(chunk)
                expected_total = total
                progress(written, total)
            output.flush()
        if expected_total is not None and written != expected_total:
            raise EHClientError("Archive download ended before Content-Length was reached", retryable=True)
        return written
