"""Polite HTTP fetching plus optional real-browser rendering for public SPA pages."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from .utils import canonicalize_url, host_allowed, is_public_hostname, registrable_host


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class FetchResult:
    requested_url: str
    final_url: str
    status_code: int
    headers: dict[str, str]
    body: bytes
    mime_type: str
    encoding: str
    method: str = "http"
    truncated: bool = False
    captured: list["FetchResult"] = field(default_factory=list)


class FetchFailure(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False, stage: str = "http"):
        super().__init__(message)
        self.retryable = retryable
        self.stage = stage


class RobotsDenied(FetchFailure):
    def __init__(self, url: str):
        super().__init__(f"robots.txt disallows {url}", retryable=False, stage="robots")


class DomainRateLimiter:
    def __init__(self, requests_per_second: float):
        self.default_interval = 1.0 / max(requests_per_second, 0.001)
        self._intervals: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_request: dict[str, float] = {}

    async def wait(self, url: str) -> None:
        host = registrable_host(url)
        interval = self._intervals.get(host, self.default_interval)
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            elapsed = time.monotonic() - self._last_request.get(host, 0.0)
            if elapsed < interval:
                await asyncio.sleep(interval - elapsed)
            self._last_request[host] = time.monotonic()

    def set_rate(self, url: str, requests_per_second: float) -> None:
        self._intervals[registrable_host(url)] = 1.0 / max(requests_per_second, 0.001)


def domain_family(host: str) -> str:
    labels = host.lower().strip(".").split(".")
    if len(labels) <= 2:
        return ".".join(labels)
    two_level_suffixes = {
        "com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn", "ac.cn",
    }
    if ".".join(labels[-2:]) in two_level_suffixes and len(labels) >= 3:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def allowed_host_for_url(url: str, allowed_families: set[str]) -> bool:
    host = registrable_host(url)
    return bool(host) and domain_family(host) in allowed_families and is_public_hostname(host)


class HttpFetcher:
    def __init__(self, settings: object):
        self.settings = settings
        timeout = httpx.Timeout(
            float(getattr(settings, "request_timeout_seconds", 30.0)),
            connect=float(getattr(settings, "connect_timeout_seconds", 10.0)),
        )
        self.client = httpx.AsyncClient(
            timeout=timeout,
            verify=bool(getattr(settings, "verify_tls", True)),
            follow_redirects=False,
            headers={
                "User-Agent": str(getattr(settings, "user_agent", "DataMarketProbe/0.1")),
                "Accept": "text/html,application/json,application/xhtml+xml,application/pdf;q=0.8,*/*;q=0.5",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
            },
        )
        self.rate_limiter = DomainRateLimiter(
            float(getattr(settings, "rate_limit_requests_per_second", 1.0))
        )
        self.robots_cache: dict[str, RobotFileParser | None] = {}
        self.max_bytes = int(getattr(settings, "max_response_bytes", 15_000_000))

    async def __aenter__(self) -> "HttpFetcher":
        return self

    async def __aexit__(self, *_args: Any) -> None:
        await self.close()

    async def close(self) -> None:
        await self.client.aclose()

    async def _robots(self, url: str, allowed_families: set[str]) -> RobotFileParser | None:
        parts = urlsplit(url)
        origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
        if origin in self.robots_cache:
            return self.robots_cache[origin]
        robots_url = origin + "/robots.txt"
        if not allowed_host_for_url(robots_url, allowed_families):
            self.robots_cache[origin] = None
            return None
        try:
            await self.rate_limiter.wait(robots_url)
            response = await self.client.get(robots_url)
            if response.status_code == 200 and len(response.content) <= 2_000_000:
                parser = RobotFileParser()
                parser.set_url(robots_url)
                parser.parse(response.text.splitlines())
                self.robots_cache[origin] = parser
                return parser
        except httpx.HTTPError:
            pass
        self.robots_cache[origin] = None
        return None

    async def allowed_by_robots(self, url: str, allowed_families: set[str]) -> bool:
        if not bool(getattr(self.settings, "respect_robots_txt", True)):
            return True
        parser = await self._robots(url, allowed_families)
        if parser is None:
            return True
        user_agent = str(getattr(self.settings, "user_agent", "DataMarketProbe")).split("/", 1)[0]
        return parser.can_fetch(user_agent, url)

    async def fetch(
        self,
        url: str,
        *,
        allowed_families: set[str],
        etag: str = "",
        last_modified: str = "",
    ) -> FetchResult:
        canonical = canonicalize_url(url)
        if not canonical or not allowed_host_for_url(canonical, allowed_families):
            raise FetchFailure(f"blocked non-public or off-family URL: {url}", stage="security")
        if not await self.allowed_by_robots(canonical, allowed_families):
            raise RobotsDenied(canonical)
        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        retries = int(getattr(self.settings, "max_retries", 3))
        backoff = float(getattr(self.settings, "retry_backoff_seconds", 2.0))
        current = canonical
        for attempt in range(retries + 1):
            try:
                result = await self._request_with_redirects(
                    current, headers=headers, allowed_families=allowed_families
                )
                if result.status_code in {408, 425, 429, 500, 502, 503, 504} and attempt < retries:
                    retry_after = result.headers.get("retry-after", "")
                    try:
                        delay = min(float(retry_after), 120.0)
                    except ValueError:
                        delay = backoff * (2**attempt)
                    await asyncio.sleep(delay)
                    continue
                return result
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                if attempt >= retries:
                    raise FetchFailure(str(exc), retryable=True, stage="network") from exc
                await asyncio.sleep(backoff * (2**attempt))
            except httpx.HTTPError as exc:
                raise FetchFailure(str(exc), retryable=False, stage="http") from exc
        raise FetchFailure(f"exhausted retries for {canonical}", retryable=True)

    async def _request_with_redirects(
        self,
        url: str,
        *,
        headers: dict[str, str],
        allowed_families: set[str],
    ) -> FetchResult:
        current = url
        for _redirect in range(6):
            await self.rate_limiter.wait(current)
            async with self.client.stream("GET", current, headers=headers) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        break
                    target = canonicalize_url(current, location)
                    if not target or not allowed_host_for_url(target, allowed_families):
                        raise FetchFailure(
                            f"blocked redirect outside allowed public domain family: {target}",
                            stage="security",
                        )
                    current = target
                    continue
                chunks: list[bytes] = []
                length = 0
                truncated = False
                async for chunk in response.aiter_bytes():
                    remaining = self.max_bytes - length
                    if remaining <= 0:
                        truncated = True
                        break
                    chunks.append(chunk[:remaining])
                    length += min(len(chunk), remaining)
                    if len(chunk) > remaining:
                        truncated = True
                        break
                body = b"".join(chunks)
                content_type = response.headers.get("content-type", "")
                mime_type = content_type.split(";", 1)[0].strip().lower()
                encoding = response.encoding or "utf-8"
                return FetchResult(
                    requested_url=url,
                    final_url=str(response.url),
                    status_code=response.status_code,
                    headers={key.lower(): value for key, value in response.headers.items()},
                    body=body,
                    mime_type=mime_type,
                    encoding=encoding,
                    truncated=truncated,
                )
        raise FetchFailure(f"too many redirects for {url}", retryable=False)


class BrowserRenderer:
    """Optional Playwright renderer using the installed Chrome/Edge binary."""

    def __init__(self, settings: object):
        self.settings = settings
        self.playwright: Any = None
        self.browser: Any = None
        self.available = False

    async def start(self) -> bool:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return False
        candidates = [
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
            Path(r"/usr/bin/google-chrome"),
            Path(r"/usr/bin/chromium"),
        ]
        executable = next((str(path) for path in candidates if path.exists()), None)
        self.playwright = await async_playwright().start()
        launch_kwargs: dict[str, Any] = {"headless": True}
        if executable:
            launch_kwargs["executable_path"] = executable
        try:
            self.browser = await self.playwright.chromium.launch(**launch_kwargs)
        except Exception:
            await self.playwright.stop()
            self.playwright = None
            return False
        self.available = True
        return True

    async def close(self) -> None:
        if self.browser is not None:
            await self.browser.close()
        if self.playwright is not None:
            await self.playwright.stop()
        self.available = False

    async def render(self, url: str, *, allowed_families: set[str]) -> FetchResult:
        if not self.available or self.browser is None:
            raise FetchFailure("Playwright browser renderer is unavailable", stage="browser")
        if not allowed_host_for_url(url, allowed_families):
            raise FetchFailure(f"browser blocked URL: {url}", stage="security")
        context = await self.browser.new_context(
            user_agent=str(getattr(self.settings, "user_agent", "DataMarketProbe/0.1")),
            locale="zh-CN",
            ignore_https_errors=False,
        )
        page = await context.new_page()
        captured: list[FetchResult] = []
        capture_tasks: list[asyncio.Task[Any]] = []

        async def capture_response(response: Any) -> None:
            response_url = canonicalize_url(response.url)
            content_type = (response.headers.get("content-type") or "").lower()
            if "json" not in content_type or not allowed_host_for_url(response_url, allowed_families):
                return
            try:
                body = await response.body()
            except Exception:
                return
            max_bytes = int(getattr(self.settings, "max_response_bytes", 15_000_000))
            captured.append(
                FetchResult(
                    requested_url=response_url,
                    final_url=response_url,
                    status_code=response.status,
                    headers={key.lower(): value for key, value in response.headers.items()},
                    body=body[:max_bytes],
                    mime_type=content_type.split(";", 1)[0],
                    encoding="utf-8",
                    method="browser-network",
                    truncated=len(body) > max_bytes,
                )
            )

        def on_response(response: Any) -> None:
            capture_tasks.append(asyncio.create_task(capture_response(response)))

        page.on("response", on_response)
        try:
            timeout_ms = int(float(getattr(self.settings, "browser_timeout_seconds", 45.0)) * 1_000)
            response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                await page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 10_000))
            except Exception:
                pass
            await page.wait_for_timeout(int(getattr(self.settings, "browser_settle_milliseconds", 1_500)))
            html = await page.content()
            initial_html = html
            if bool(getattr(self.settings, "browser_pagination_enabled", True)):
                seen_pages = {hashlib.sha256(html.encode("utf-8")).hexdigest()}
                max_pages = int(getattr(self.settings, "browser_max_pagination_pages", 300))
                next_selector = ", ".join((
                    ".el-pagination button.btn-next",
                    ".ant-pagination-next button",
                    ".ant-pagination-next a",
                    ".pagination .next a",
                    "button[aria-label='Next Page']",
                ))
                for page_number in range(2, max_pages + 1):
                    next_button = page.locator(next_selector)
                    if await next_button.count() != 1:
                        break
                    disabled = await next_button.get_attribute("disabled")
                    classes = (await next_button.get_attribute("class")) or ""
                    parent_classes = await next_button.evaluate("el => el.parentElement ? el.parentElement.className : ''")
                    if disabled is not None or "disabled" in classes or "disabled" in str(parent_classes):
                        break
                    await next_button.click()
                    try:
                        await page.wait_for_load_state("networkidle", timeout=5_000)
                    except Exception:
                        await page.wait_for_timeout(400)
                    page_html = await page.content()
                    digest = hashlib.sha256(page_html.encode("utf-8")).hexdigest()
                    if digest in seen_pages:
                        break
                    seen_pages.add(digest)
                    synthetic_url = canonicalize_url(page.url)
                    separator = "&" if "?" in synthetic_url else "?"
                    captured.append(
                        FetchResult(
                            requested_url=f"{synthetic_url}{separator}dmp_page={page_number}",
                            final_url=f"{synthetic_url}{separator}dmp_page={page_number}",
                            status_code=200,
                            headers={},
                            body=page_html.encode("utf-8"),
                            mime_type="text/html",
                            encoding="utf-8",
                            method="browser-page",
                        )
                    )
            final_url = canonicalize_url(page.url)
            if not allowed_host_for_url(final_url, allowed_families):
                raise FetchFailure(f"browser redirected outside allowed domain family: {final_url}", stage="security")
            if capture_tasks:
                await asyncio.gather(*capture_tasks, return_exceptions=True)
            status = response.status if response else 200
            body = initial_html.encode("utf-8")
            return FetchResult(
                requested_url=url,
                final_url=final_url,
                status_code=status,
                headers={key.lower(): value for key, value in (response.headers.items() if response else [])},
                body=body,
                mime_type="text/html",
                encoding="utf-8",
                method="browser",
                captured=captured,
            )
        finally:
            await context.close()


def looks_like_spa(body: bytes, url: str, mime_type: str) -> bool:
    if "html" not in mime_type and not body.lstrip().lower().startswith(b"<!doctype html"):
        return False
    if urlsplit(url).fragment.startswith(("/", "!/")):
        return True
    head = body[:300_000].decode("utf-8", errors="ignore")
    without_markup = re.sub(r"<[^>]+>", " ", head)
    visible = re.sub(r"\s+", " ", without_markup).strip()
    scripts = len(re.findall(r"<script\b", head, re.I))
    ssr_markers = any(marker in head for marker in ("window.__NUXT__", "__NEXT_DATA__"))
    app_markers = any(
        marker in head
        for marker in (
            "id=\"app\"", "id='app'", "id=\"root\"", "id='root'",
            "id=\"main\"", "id='main'", "webpack", "vite",
        )
    )
    # Nuxt/Next pages may contain substantial server-rendered navigation or
    # promotional copy while their catalog records still load through browser
    # APIs.  Treat those framework markers as render-worthy regardless of the
    # visible-text heuristic; retain the stricter threshold for generic app
    # shells to avoid rendering ordinary static pages unnecessarily.
    return scripts >= 2 and (ssr_markers or (app_markers and len(visible) < 800))
