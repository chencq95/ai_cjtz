"""Application settings loaded from environment variables and ``.env``."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration shared by the crawler, API, and scheduler.

    Environment variables are case-insensitive and use the ``DMP_`` prefix.
    For example, ``database_url`` is configured with ``DMP_DATABASE_URL``.
    """

    model_config = SettingsConfigDict(
        env_prefix="DMP_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite:///data/catalog.db"
    redis_url: str = "redis://127.0.0.1:6379/0"

    object_store_backend: Literal["filesystem", "minio", "database"] = "filesystem"
    object_store_path: Path = Path("data/raw")
    archive_path: Path = Path("data/archive")
    raw_retention_days: int = Field(default=365, ge=1, le=3650)
    minio_endpoint: str = "127.0.0.1:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "data-market-raw"
    minio_secure: bool = False

    timezone: str = "Asia/Shanghai"
    schedule_hour: int = Field(default=2, ge=0, le=23)
    schedule_minute: int = Field(default=30, ge=0, le=59)

    crawl_concurrency: int = Field(default=8, ge=1, le=128)
    platform_concurrency: int = Field(default=4, ge=1, le=32)
    request_timeout_seconds: float = Field(default=30.0, gt=0, le=600)
    connect_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    rate_limit_requests_per_second: float = Field(default=1.0, gt=0, le=100)
    max_pages_per_platform: int = Field(default=1_000, ge=1)
    max_crawl_depth: int = Field(default=8, ge=0, le=100)
    max_retries: int = Field(default=3, ge=0, le=20)
    retry_backoff_seconds: float = Field(default=2.0, ge=0, le=300)
    max_response_bytes: int = Field(default=15_000_000, ge=100_000, le=200_000_000)
    enable_browser: bool = True
    browser_timeout_seconds: float = Field(default=45.0, gt=0, le=300)
    browser_settle_milliseconds: int = Field(default=1_500, ge=0, le=30_000)
    browser_pagination_enabled: bool = True
    browser_max_pagination_pages: int = Field(default=300, ge=1, le=2000)

    # Incremental jobs re-read a small overlap window to catch late edits. A
    # periodic full recheck catches removals and sites without reliable dates.
    incremental_overlap_days: int = Field(default=3, ge=0, le=90)
    full_recheck_days: int = Field(default=30, ge=1, le=365)
    classification_review_threshold: float = Field(default=0.80, ge=0, le=1)
    llm_enabled: bool = False
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    llm_review_batch_size: int = Field(default=50, ge=1, le=500)

    respect_robots_txt: bool = True
    verify_tls: bool = True
    user_agent: str = "DataMarketProbe/0.1 (+https://localhost)"

    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8_000, ge=1, le=65_535)
    api_reload: bool = False
    api_cors_origins: str = "http://127.0.0.1:8080,http://localhost:8080"
    auth_secret_key: str = "change-me-before-production-use-32-bytes"
    auth_token_minutes: int = Field(default=480, ge=5, le=10080)
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = "ChangeMe123!"
    cookie_secure: bool = False
    celery_eager: bool = False
    scheduler_poll_seconds: int = Field(default=60, ge=10, le=3600)
    log_level: Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"] = "INFO"

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        """Reject invalid IANA timezone names during startup."""

        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown IANA timezone: {value}") from exc
        return value

    @property
    def schedule_label(self) -> str:
        """Return the configured daily schedule in a human-readable form."""

        return f"{self.schedule_hour:02d}:{self.schedule_minute:02d} {self.timezone}"

    @property
    def cors_origins(self) -> list[str]:
        return [value.strip() for value in self.api_cors_origins.split(",") if value.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a process-wide settings instance."""

    return Settings()
