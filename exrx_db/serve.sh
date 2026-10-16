#!/usr/bin/env bash
# Start the local, private ExRx SurrealDB server (RocksDB on disk, localhost only).
# Credentials come from .env (EXRX_DB_USER / EXRX_DB_PASS); defaults are for local dev only.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a
export PATH="$HOME/.surrealdb:$PATH"
export SURREAL_USER="${EXRX_DB_USER:-root}" SURREAL_PASS="${EXRX_DB_PASS:-root}"
exec surreal db start --bind 127.0.0.1:8000 --log warn "rocksdb:data/exrx/surreal.db"
