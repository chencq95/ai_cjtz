#!/usr/bin/env bash
set -euo pipefail

stamp="$(date +%Y%m%d-%H%M%S)"
target="${1:-./backups/$stamp}"
mkdir -p "$target"
docker compose exec -T postgres pg_dump -U dmp -d dmp -Fc > "$target/postgres.dump"
docker compose run --rm -v "$(cd "$target" && pwd):/backup" --entrypoint /bin/sh minio-init \
  -c "mc alias set local http://minio:9000 \$MINIO_ROOT_USER \$MINIO_ROOT_PASSWORD && mc mirror local/data-market-raw /backup/raw"
docker compose config > "$target/compose.resolved.yml"
echo "Backup written to $target"
