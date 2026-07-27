import io
import zipfile

import httpx
import pytest

from app.auth_service import CookieFormatError, parse_netscape_cookies
from app.eh_client import EHClient, EHClientError, parse_archive_page, parse_gallery_url, validate_remote_url


ARCHIVER_HTML = """
<!DOCTYPE html><html><body><div id="db">
<h1>[artist] Test Gallery</h1>
<p>Current Funds:</p><p>787,179 GP &nbsp; 244,925 Credits</p>
<div><div style="width:180px; float:left">
  <div>Download Cost: &nbsp; <strong>Free!</strong></div>
  <form action="https://e-hentai.org/archiver.php?gid=4079841&amp;token=54d9f8b629" method="post">
    <input type="hidden" name="dltype" value="org" />
    <input type="submit" name="dlcheck" value="Download Original Archive" />
  </form>
  <p>Estimated Size: &nbsp; <strong>111.2 MiB</strong></p>
</div><div style="width:180px; float:right">
  <div>Download Cost: &nbsp; <strong>Free!</strong></div>
  <form action="https://e-hentai.org/archiver.php?gid=4079841&amp;token=54d9f8b629" method="post">
    <input type="hidden" name="dltype" value="res" />
    <input type="submit" name="dlcheck" value="Download Resample Archive" />
  </form>
  <p>Estimated Size: &nbsp; <strong>8.34 MiB</strong></p>
</div></div>
<form id="hathdl_form" action="https://e-hentai.org/archiver.php" method="post">
  <input type="hidden" id="hathdl_xres" name="hathdl_xres" value="" />
</form>
<a href="#" onclick="return do_hathdl('1280')">1280x</a>
</div></body></html>
"""


def test_gallery_url_accepts_only_exact_eh_gallery_paths():
    gallery = parse_gallery_url("https://e-hentai.org/g/4023009/76e234c4d3/")
    assert (gallery.gid, gallery.token, gallery.host) == (4023009, "76e234c4d3", "e-hentai.org")
    with pytest.raises(ValueError):
        parse_gallery_url("http://e-hentai.org/g/4023009/76e234c4d3/")
    with pytest.raises(ValueError):
        parse_gallery_url("https://example.com/g/4023009/76e234c4d3/")
    with pytest.raises(ValueError):
        parse_gallery_url("https://e-hentai.org/g/4023009/76e234c4d3/?next=bad")


def test_worker_remote_urls_allow_any_public_https_host(monkeypatch):
    monkeypatch.setattr(
        "app.eh_client.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("8.8.8.8", 443))],
    )
    validate_remote_url("https://e-hentai.org/archiver.php")
    validate_remote_url("https://download.exhentai.org/archive/file.zip")
    validate_remote_url("https://example.com/archive/file.zip")


def test_worker_remote_urls_still_reject_unsafe_targets(monkeypatch):
    monkeypatch.setattr(
        "app.eh_client.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("127.0.0.1", 443))],
    )
    with pytest.raises(EHClientError, match="non-public address"):
        validate_remote_url("https://example.com/archive/file.zip")
    with pytest.raises(EHClientError, match="unsafe download URL"):
        validate_remote_url("http://example.com/archive/file.zip")


def test_parser_extracts_only_normal_archive_forms():
    page = parse_archive_page(ARCHIVER_HTML, "https://e-hentai.org/archiver.php?gid=4079841&token=54d9f8b629")
    assert page.gp == 787179
    assert page.credits == 244925
    assert page.title == "[artist] Test Gallery"
    assert set(page.forms) == {"original", "resample"}
    assert page.forms["original"].fields == {"dltype": "org", "dlcheck": "Download Original Archive"}
    assert page.forms["original"].estimated_size == int(111.2 * 1024**2)
    assert page.forms["resample"].estimated_size == int(8.34 * 1024**2)
    assert all("hathdl_xres" not in form.fields for form in page.forms.values())


def test_parser_recognizes_pending_and_ready_pages():
    pending = parse_archive_page("<div id='db'>Archive is being generated. Please wait.</div>", "https://e-hentai.org/archiver.php")
    ready = parse_archive_page("<div id='db'><a href='/archive/abc.zip'>Click here to download</a></div>", "https://e-hentai.org/archiver.php")
    assert pending.state == "pending"
    assert ready.state == "ready"
    assert ready.download_url == "https://e-hentai.org/archive/abc.zip"


def test_parser_recognizes_prepared_archive_download_page():
    html = """
    <div id="db"><p>The file was successfully prepared, and is ready for download.<br>
    <strong>Test Gallery-1280x.zip</strong><br>
    <a href="/archive/4080126/hash/session/1?start=1">Click Here To Start Downloading</a></p></div>
    """
    page = parse_archive_page(html, "https://e-hentai.org/archiver.php?gid=4080126&token=abc")
    assert page.state == "ready"
    assert page.download_url == "https://e-hentai.org/archive/4080126/hash/session/1?start=1"


def test_parser_does_not_invent_missing_resample_form():
    html = """
    <div id="db"><h1>Small gallery</h1><div>
      <div>Download Cost: <strong>Free!</strong></div>
      <form action="/archiver.php?gid=1&amp;token=abc" method="post">
        <input type="hidden" name="dltype" value="org">
        <input type="submit" name="dlcheck" value="Download Original Archive">
      </form>
    </div></div>
    """
    page = parse_archive_page(html, "https://e-hentai.org/archiver.php?gid=1&token=abc")
    assert set(page.forms) == {"original"}


def test_parser_marks_previously_unlocked_archive_without_using_hath():
    html = """
    <div id="db"><h1>Unlocked gallery</h1>
      <p>You unlocked an <strong>original</strong> download of this archive on
      <strong>2026-07-27 13:28</strong></p>
      <div><div>Download Cost: <strong>1,234 GP</strong></div>
        <form action="/archiver.php?gid=1&amp;token=abc" method="post">
          <input type="hidden" name="dltype" value="org">
          <input type="submit" name="dlcheck" value="Download Original Archive">
        </form>
      </div>
      <form id="hathdl_form"><input name="hathdl_xres" value="org"></form>
    </div>
    """
    page = parse_archive_page(html, "https://e-hentai.org/archiver.php?gid=1&token=abc")
    assert page.unlocked_types == frozenset({"original"})
    assert set(page.forms) == {"original"}
    assert page.forms["original"].cost == 0
    assert page.forms["original"].cost_type == "unlocked"


def test_download_follows_html_interstitial_before_streaming_zip(test_settings, monkeypatch):
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("001.jpg", b"image")
    zip_bytes = payload.getvalue()
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/prepared":
            return httpx.Response(200, headers={"Content-Type": "text/html; charset=UTF-8"}, text="""
                <div id="db"><a href="/archive/file?start=1">Click Here To Start Downloading</a></div>
            """)
        return httpx.Response(200, headers={"Content-Type": "application/zip", "Content-Length": str(len(zip_bytes))}, content=zip_bytes)

    client = EHClient("ipb_member_id=1; ipb_pass_hash=x", test_settings)
    monkeypatch.setattr("app.eh_client.validate_remote_url", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(client, "_client", lambda **_kwargs: httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False))
    received = b"".join(chunk for chunk, _ in client.iter_download("https://e-hentai.org/prepared"))
    assert received == zip_bytes
    assert requests == ["/prepared", "/archive/file"]
    with pytest.raises(EHClientError, match="maximum size"):
        b"".join(
            chunk for chunk, _ in client.iter_download(
                "https://e-hentai.org/prepared", max_bytes=len(zip_bytes) - 1
            )
        )


def test_download_does_not_send_account_cookie_to_external_host(test_settings, monkeypatch):
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("001.jpg", b"image")
    zip_bytes = payload.getvalue()
    cookie_modes: list[bool] = []

    def client_factory(*, cookie: bool = True):
        cookie_modes.append(cookie)

        def handler(request: httpx.Request) -> httpx.Response:
            assert "cookie" not in request.headers
            return httpx.Response(
                200,
                headers={"Content-Type": "application/zip"},
                content=zip_bytes,
            )

        return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)

    client = EHClient("ipb_member_id=1; ipb_pass_hash=x", test_settings)
    monkeypatch.setattr("app.eh_client.validate_remote_url", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(client, "_client", client_factory)

    received = b"".join(chunk for chunk, _ in client.iter_download("https://cdn.example.com/file.zip"))

    assert received == zip_bytes
    assert cookie_modes == [False]


def test_parser_distinguishes_payment_confirmation_form():
    html = """
    <div id="db"><form action="/archiver.php?gid=1&amp;token=abc" method="post">
      <input type="hidden" name="dltype" value="org">
      <input type="submit" name="dlconfirm" value="Confirm Download">
    </form></div>
    """
    page = parse_archive_page(html, "https://e-hentai.org/archiver.php?gid=1&token=abc")
    assert page.state == "confirmation"
    assert page.forms["original"].confirmation is True


def test_netscape_cookie_parser_filters_domains_and_requires_login_cookies():
    text = """# Netscape HTTP Cookie File
.e-hentai.org\tTRUE\t/\tTRUE\t0\tipb_member_id\t123
.e-hentai.org\tTRUE\t/\tTRUE\t0\tipb_pass_hash\tsecret
.exhentai.org\tTRUE\t/\tTRUE\t0\tigneous\tabc
.example.com\tTRUE\t/\tTRUE\t0\tignored\tvalue
"""
    parsed = parse_netscape_cookies(text)
    assert "ipb_member_id=123" in parsed.header
    assert "igneous=abc" in parsed.header
    assert "ignored" not in parsed.header
    with pytest.raises(CookieFormatError):
        parse_netscape_cookies(".e-hentai.org\tTRUE\t/\tTRUE\t0\tigneous\tabc")
