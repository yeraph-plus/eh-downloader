#!/bin/sh
set -eu

mkdir -p /app/data /app/cache
chown app:app /app/data /app/cache
exec gosu app "$@"
