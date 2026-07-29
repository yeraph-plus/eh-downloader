# Eh Downloader

[简体中文](README-zh.md)

Eh Downloader is a web application for downloading E-Hentai and ExHentai gallery ZIP archives. It supports both Original Archive and Resample Archive downloads.

Downloads are handled through the official archive pages. The application does not scrape individual image URLs and does not depend on H@H Downloader. Free quota, GP charges, and any required Credits-to-GP conversion are handled entirely by the official EH archive service.

## Features

![Screenshot](https://github.com/yeraph-plus/eh-downloader/blob/main/screenshots/110647.jpg)

![Screenshot](https://github.com/yeraph-plus/eh-downloader/blob/main/screenshots/110815.jpg)

- Submit gallery URLs directly, one URL per line.
- Download original or resampled archives.
- Manage multiple EH accounts and track their GP, Credits, and account status.
- Optional local caching, capacity limits, scheduled cleanup, and fully relayed downloads.
- Single administrator login, Bearer Token authentication, and optional guest access.

## Images

GitHub Actions builds and publishes two images to the GHCR package:

```text
ghcr.io/yeraph-plus/eh-downloader:latest  # Eh Downloader Web/Worker
ghcr.io/yeraph-plus/eh-downloader:hath    # Standalone, easy-to-deploy H@H Client wrapper
```

## Quick Start

Requirements:

- Docker Engine or Docker Desktop
- Docker Compose v2

Clone the repository and prepare the configuration:

```bash
git clone https://github.com/yeraph-plus/eh-downloader.git
cd eh-downloader
cp .env.example .env
mkdir -p data cache
openssl rand -hex 32
```

Edit `.env` and set at least the following values:

```dotenv
APP_SECRET=the-random-string-generated-above
ADMIN_PASSWORD=a-long-and-unique-administrator-password
EH_DOWNLOADER_IMAGE=ghcr.io/yeraph-plus/eh-downloader:latest
```

`APP_SECRET` is used to encrypt account cookies. Do not change it after deployment, or the application will no longer be able to decrypt account credentials already stored in the database.

Start the services:

```bash
docker compose pull
docker compose up -d
docker compose ps
```

The default address is [http://127.0.0.1:8000](http://127.0.0.1:8000). The readiness endpoint is `/health/ready`, and the API documentation is available at `/docs`.

## Common Configuration

Only the following variables need to be set in `.env`. All other settings have sensible defaults and can be changed later from the Settings page.

| Variable | Required | Description |
| --- | --- | --- |
| `APP_SECRET` | Yes | Random string (min 32 chars), used to encrypt account cookies |
| `ADMIN_PASSWORD` | Yes | Administrator login password (min 10 chars) |
| `EH_DOWNLOADER_IMAGE` | No | Container image tag |
| `WEB_BIND_ADDRESS` | No | Web bind address (default `127.0.0.1`) |
| `WEB_PORT` | No | Published web port (default `8000`) |
| `SECURE_COOKIES` | No | Set to `true` when serving over HTTPS |
| `EH_PROXY_URL` | No | HTTP/SOCKS proxy used for EH requests |

The administrator username defaults to `admin`. Cache settings, guest permissions, archive size limits, and Worker concurrency can all be adjusted from the Settings page after startup.

Tip: When Docker uses a proxy running on the host, do not use `127.0.0.1` from inside the container. For example, if the host proxy listens on port `7890`:

```dotenv
EH_PROXY_URL=http://host.docker.internal:7890
```

## Usage

### Add an Account

1. Sign in with the administrator username and password from `.env`.
2. Open the Settings page and choose to add an account.
3. Import a standard Netscape-format `cookies.txt` file.
4. Return to the main page and submit gallery tasks.

The cookies must include at least `ipb_member_id` and `ipb_pass_hash`. ExHentai access usually also requires `igneous`. You can export the file with the “Get cookies.txt LOCALLY” browser extension. Cookies are encrypted in the local database and are never shown again in the UI or API.

EH account status is checked only while an actual download task is being processed. Accounts with invalid authentication are disabled automatically. Temporary network failures, depleted GP, or other conditions that prevent downloads place an account into cooldown.

### Create Tasks

Paste one or more gallery URLs into the text area on the main page, one per line, then select the archive type:

```text
https://e-hentai.org/g/123456/abcdef0123/
https://exhentai.org/g/234567/0123abcdef/
```

Up to 100 URLs can be submitted at once. Original and Resample archives for the same gallery are treated as separate tasks. Submitting the same archive type again does not create a duplicate record.

Tasks are processed automatically by the background Worker. When an archive is ready, the list shows its filename, size, status, error details, and download button. Ordinary network failures are retried automatically up to three times. Balance, authentication, and page protocol errors are reported directly.

### Cache and Relayed Downloads

- Capacity limit: defines a maximum disk usage. When full, new tasks wait for existing cached files to expire and be cleaned up; unexpired files are not deleted automatically.
- Cache enabled: downloaded ZIP files are verified and saved under `./cache`, allowing later tasks for the same gallery to resume from the local copy.
- Cache disabled: only the official archive URL is prepared. Downloads are relayed through the local service without retaining the ZIP locally.
- Expired or missing files: their download links become unavailable, and invalid records are handled by the cleanup process.

### Guest Access

Guest access is disabled by default. Guests share one public identity. Available permissions are:

- Allow guests to view the archive list.
- Prevent or allow guests to create Resample or Original tasks.

## HTTPS and Security

The following configuration is recommended for production:

```dotenv
WEB_BIND_ADDRESS=127.0.0.1
SECURE_COOKIES=true
```

Provide HTTPS through a reverse proxy such as Caddy, Nginx, or Traefik. This application does not include anti-abuse features such as IP-based rate limiting. Add request rate limits at the reverse proxy before exposing it publicly.

## Updates and Backups

Update the images:

```bash
docker compose pull
docker compose up -d
```

Back up the following:

- `.env`: contains the `APP_SECRET` required to decrypt account cookies.
- `./data`: contains the SQLite database and application settings.
- `./cache`: optional; losing it only affects existing cached files.

Stopping the services before backing up the database is recommended:

```bash
docker compose down
```

`docker compose down` does not delete data in bind-mounted directories. Do not use `docker compose down -v` if you need to retain H@H named volumes.

## Optional H@H Client

The H@H Client is an independent, easy-to-deploy companion container and is unrelated to the Eh Downloader application itself.

Its service and named volumes are fully commented out in `compose.yaml` by default. To enable it:

1. Uncomment the `hath` service section.
2. Uncomment the H@H configuration in `.env`.
3. Set `HATH_IMAGE`, `HATH_CLIENT_ID`, `HATH_CLIENT_KEY`, and `HATH_PORT`.
4. Start the H@H service.

```bash
docker compose pull hath
docker compose up -d hath
```

This H@H container uses host networking and listens directly on `HATH_PORT`. The port must match the EH control panel configuration and must be allowed through the VPS firewall.

## Local Development

Run the backend tests:

```bash
python -m pip install "./backend[test]"
python -m pytest backend/tests
```

Build the frontend:

```bash
cd frontend
npm install
npm run build
```

Build the application image locally:

```bash
docker build -f docker/web.Dockerfile -t eh-downloader:local .
```
