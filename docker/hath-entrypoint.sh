#!/bin/sh
set -eu

login_file=/hath/data/client_login

if [ ! -f "$login_file" ]; then
  if [ -z "${HATH_CLIENT_ID:-}" ] || [ -z "${HATH_CLIENT_KEY:-}" ]; then
    echo "HATH_CLIENT_ID and HATH_CLIENT_KEY are required on first start" >&2
    exit 1
  fi
  umask 077
  printf '%s-%s' "$HATH_CLIENT_ID" "$HATH_CLIENT_KEY" > "$login_file"
fi

case "${HATH_PORT:-}" in
  ''|*[!0-9]*)
    echo "HATH_PORT must be a numeric TCP port" >&2
    exit 1
    ;;
esac

exec java -jar /opt/hath/HentaiAtHome.jar \
  --cache-dir=/hath/cache \
  --data-dir=/hath/data \
  --download-dir=/hath/downloads \
  --log-dir=/hath/log \
  --temp-dir=/tmp/hath \
  "--port=${HATH_PORT}"
