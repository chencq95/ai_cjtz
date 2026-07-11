#!/usr/bin/env bash
set -euo pipefail

alembic upgrade head
python -c "from data_market_probe.bootstrap import ensure_defaults; from data_market_probe.settings import get_settings; print(ensure_defaults(get_settings()))"
exec "$@"
