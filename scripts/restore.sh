#!/usr/bin/env bash
set -euo pipefail

source_dir="${1:?usage: restore.sh BACKUP_DIRECTORY}"
test -f "$source_dir/postgres.dump"
docker compose exec -T postgres pg_restore -U dmp -d dmp --clean --if-exists < "$source_dir/postgres.dump"
if test -d "$source_dir/raw"; then
  docker compose run --rm -v "$(cd "$source_dir" && pwd):/backup:ro" --entrypoint /bin/sh minio-init \
    -c "mc alias set local http://minio:9000 \$MINIO_ROOT_USER \$MINIO_ROOT_PASSWORD && mc mirror /backup/raw local/data-market-raw"
fi
echo "Restore completed from $source_dir"
