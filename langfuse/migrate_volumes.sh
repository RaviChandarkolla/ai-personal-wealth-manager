#!/usr/bin/env bash
# One-time: copy data from the old Docker named volumes into ../data/langfuse/ (bind mounts).
set -euo pipefail
cd "$(dirname "$0")"
DEST="$(cd .. && pwd)/data/langfuse"

docker compose down
for pair in \
  "langfuse_postgres_data:postgres" \
  "langfuse_clickhouse_data:clickhouse/data" \
  "langfuse_clickhouse_logs:clickhouse/logs" \
  "langfuse_minio_data:minio" \
  "langfuse_redis_data:redis"; do
  vol="${pair%%:*}"; dir="${pair#*:}"
  # compose prefixes volume names with the project name (directory name: "langfuse")
  full="langfuse_${vol}"
  docker volume inspect "$full" >/dev/null 2>&1 || full="$vol"
  docker volume inspect "$full" >/dev/null 2>&1 || { echo "skip $vol (not found)"; continue; }
  mkdir -p "$DEST/$dir"
  docker run --rm -v "$full":/from -v "$DEST/$dir":/to alpine sh -c 'cp -a /from/. /to/'
  echo "copied $full -> $DEST/$dir"
done
docker compose up -d
