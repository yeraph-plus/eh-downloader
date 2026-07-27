FROM node:24-alpine AS frontend-builder

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY backend/ /app/backend/
RUN pip install --no-cache-dir /app/backend \
    && apt-get update \
    && apt-get install --no-install-recommends --yes gosu \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin app \
    && mkdir -p /app/data /app/cache /app/frontend \
    && chown -R app:app /app
COPY --from=frontend-builder --chown=app:app /build/frontend/dist/ /app/frontend/
COPY docker/web-entrypoint.sh /usr/local/bin/eh-downloader-entrypoint
RUN chmod 0555 /usr/local/bin/eh-downloader-entrypoint

EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/eh-downloader-entrypoint"]
CMD ["uvicorn", "app.main:app", "--app-dir", "/app/backend", "--host", "0.0.0.0", "--port", "8000"]
