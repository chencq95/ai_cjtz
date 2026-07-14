FROM mcr.microsoft.com/playwright/python:v1.51.0-noble

ARG PIP_INDEX_URL=https://pypi.org/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src

WORKDIR /app
COPY pyproject.toml alembic.ini ./
COPY src ./src
COPY migrations ./migrations
RUN pip install --no-cache-dir --index-url "$PIP_INDEX_URL" ".[browser]"
COPY config ./config
COPY scripts ./scripts
RUN chmod +x /app/scripts/*.sh 2>/dev/null || true

CMD ["uvicorn", "data_market_probe.api:app", "--host", "0.0.0.0", "--port", "8000"]
