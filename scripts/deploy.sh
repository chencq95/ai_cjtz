#!/usr/bin/env bash
set -euo pipefail

deploy_dir="${DEPLOY_DIR:-/ai_cjtz}"
deploy_sha="${DEPLOY_SHA:?DEPLOY_SHA is required}"
health_url="${HEALTH_URL:-http://127.0.0.1:8888/api/health}"

cd "$deploy_dir"
previous_sha="${PREVIOUS_DEPLOY_SHA:-$(git rev-parse HEAD 2>/dev/null || true)}"

if docker compose ps --status running postgres 2>/dev/null | grep -q postgres; then
  bash ./scripts/backup.sh "./backups/pre-deploy-$(date +%Y%m%d-%H%M%S)"
fi

if [[ "${DEPLOY_SKIP_FETCH:-false}" != "true" ]]; then
  git fetch --prune origin main
fi
git checkout --detach "$deploy_sha"

rollback() {
  if [[ -n "$previous_sha" ]]; then
    git checkout --detach "$previous_sha"
    docker compose build --pull
    docker compose up -d --remove-orphans
  fi
}
trap rollback ERR

docker compose config --quiet
docker compose build --pull
docker compose up -d --remove-orphans

for _ in $(seq 1 60); do
  if curl --fail --silent "$health_url" >/dev/null; then
    trap - ERR
    docker image prune -f >/dev/null 2>&1 || true
    echo "Deployment healthy at $deploy_sha"
    exit 0
  fi
  sleep 5
done

echo "Health check timed out: $health_url" >&2
exit 1
