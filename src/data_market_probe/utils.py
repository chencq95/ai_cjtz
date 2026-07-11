"""Shared URL, text, hashing and security helpers."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
import unicodedata
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from dateutil import parser as date_parser


TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "spm", "from", "source", "timestamp", "_", "t", "v", "rnd", "random",
}
SPACE_RE = re.compile(r"\s+")
NOISE_RE = re.compile(
    r"(?:访问量|浏览量|阅读量|点击量|当前时间|系统时间)\s*[:：]?\s*\d+",
    re.I,
)


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8", errors="replace"))


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    value = NOISE_RE.sub("", value)
    return SPACE_RE.sub(" ", value).strip()


def semantic_text_hash(value: str) -> str:
    return sha256_text(normalize_text(value))


def canonicalize_url(base_url: str, href: str | None = None) -> str:
    """Canonicalize an HTTP URL while retaining meaningful SPA hash routes."""

    raw = urljoin(base_url, href) if href is not None else base_url
    raw = raw.strip().replace(" ", "")
    parts = urlsplit(raw)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        return ""
    scheme = parts.scheme.lower()
    hostname = parts.hostname.encode("idna").decode("ascii").lower()
    port = parts.port
    netloc = hostname
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{hostname}:{port}"
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    query_items = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMS
    ]
    query = urlencode(sorted(query_items), doseq=True)
    fragment = parts.fragment if parts.fragment.startswith(("/", "!/")) else ""
    return urlunsplit((scheme, netloc, path, query, fragment))


def registrable_host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def host_allowed(url: str, allowed_hosts: set[str]) -> bool:
    host = registrable_host(url)
    return any(host == allowed or host.endswith("." + allowed) for allowed in allowed_hosts)


def is_public_hostname(hostname: str) -> bool:
    """Fail closed for literal/private IPs and hostnames resolving only privately."""

    if not hostname or hostname.lower() == "localhost":
        return False
    try:
        literal = ipaddress.ip_address(hostname.strip("[]"))
        return literal.is_global
    except ValueError:
        pass
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(hostname, None)}
    except socket.gaierror:
        return True  # Let the fetcher record a precise DNS failure.
    if not addresses:
        return False
    return all(ipaddress.ip_address(address).is_global for address in addresses)


def parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = normalize_text(str(value))
        match = re.search(r"(20\d{2}|19\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", text)
        if match:
            text = "-".join(match.groups())
        try:
            parsed = date_parser.parse(text, fuzzy=False)
        except (ValueError, TypeError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def safe_snippet(value: str, limit: int = 500) -> str:
    value = normalize_text(value)
    return value if len(value) <= limit else value[: limit - 1] + "…"

