from __future__ import annotations

import socket

import pytest

from data_market_probe import utils
from data_market_probe.fetching import allowed_host_for_url, domain_family, looks_like_spa
from data_market_probe.utils import canonicalize_url, host_allowed, is_public_hostname


def test_canonicalize_url_normalizes_host_port_query_and_spa_fragment() -> None:
    actual = canonicalize_url(
        "HTTPS://ExAmPle.COM:443//catalog/?utm_source=campaign&b=2&a=1#/product/7"
    )

    assert actual == "https://example.com/catalog/?a=1&b=2#/product/7"


def test_canonicalize_url_resolves_relative_url_and_drops_noise() -> None:
    actual = canonicalize_url(
        "https://example.com/catalog/pages/index.html",
        "../detail?id=42&utm_campaign=spring#ordinary-section",
    )

    assert actual == "https://example.com/catalog/detail?id=42"


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "file:///etc/passwd",
        "data:text/plain,secret",
        "https:///missing-host",
    ],
)
def test_canonicalize_url_rejects_non_http_or_missing_host(url: str) -> None:
    assert canonicalize_url(url) == ""


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("example.com", "example.com"),
        ("api.market.example.com", "example.com"),
        ("exchange.com.cn", "exchange.com.cn"),
        ("api.exchange.com.cn", "exchange.com.cn"),
        ("www.nda.gov.cn", "nda.gov.cn"),
    ],
)
def test_domain_family_handles_cn_second_level_suffixes(host: str, expected: str) -> None:
    assert domain_family(host) == expected


def test_domain_family_allowlist_accepts_only_approved_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "data_market_probe.fetching.is_public_hostname",
        lambda hostname: hostname != "private.exchange.com.cn",
    )
    allowed = {"exchange.com.cn"}

    assert allowed_host_for_url("https://exchange.com.cn/products", allowed)
    assert allowed_host_for_url("https://api.exchange.com.cn/products", allowed)
    assert not allowed_host_for_url("https://exchange.com.cn.evil.test/products", allowed)
    assert not allowed_host_for_url("https://other.com.cn/products", allowed)
    assert not allowed_host_for_url("https://private.exchange.com.cn/products", allowed)


def test_host_allowed_does_not_accept_suffix_confusion() -> None:
    allowed = {"example.com"}

    assert host_allowed("https://example.com/product", allowed)
    assert host_allowed("https://api.example.com/product", allowed)
    assert not host_allowed("https://notexample.com/product", allowed)
    assert not host_allowed("https://example.com.evil.test/product", allowed)


@pytest.mark.parametrize(
    "hostname",
    [
        "localhost",
        "127.0.0.1",
        "10.10.0.5",
        "169.254.169.254",
        "::1",
    ],
)
def test_is_public_hostname_rejects_local_and_private_literals(hostname: str) -> None:
    assert not is_public_hostname(hostname)


def test_is_public_hostname_rejects_mixed_public_private_dns_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_getaddrinfo(*_args: object, **_kwargs: object) -> list[tuple[object, ...]]:
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 443)),
        ]

    monkeypatch.setattr(utils.socket, "getaddrinfo", fake_getaddrinfo)

    assert not is_public_hostname("rebind.example.test")


def test_is_public_hostname_accepts_proxy_synthetic_dns_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_getaddrinfo(*_args: object, **_kwargs: object) -> list[tuple[object, ...]]:
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.7", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.19.255.9", 443)),
        ]

    monkeypatch.setattr(utils.socket, "getaddrinfo", fake_getaddrinfo)

    assert is_public_hostname("public-through-proxy.example.test")
    assert not is_public_hostname("198.18.0.7")


def test_is_public_hostname_rejects_mixed_proxy_private_dns_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_getaddrinfo(*_args: object, **_kwargs: object) -> list[tuple[object, ...]]:
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.7", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 443)),
        ]

    monkeypatch.setattr(utils.socket, "getaddrinfo", fake_getaddrinfo)

    assert not is_public_hostname("mixed-through-proxy.example.test")


def test_nuxt_ssr_catalog_is_rendered_even_with_substantial_visible_copy() -> None:
    body = (
        "<!doctype html><html><body><div>" + ("公开数据交易目录介绍" * 100) + "</div>"
        "<script>window.__NUXT__={serverRendered:true}</script>"
        "<script src='/_nuxt/app.js'></script></body></html>"
    ).encode()

    assert looks_like_spa(body, "https://example.com/product/list", "text/html")


def test_ordinary_static_page_with_scripts_is_not_forced_into_browser() -> None:
    body = (
        "<!doctype html><html><body><article>" + ("新闻内容" * 300) + "</article>"
        "<script src='/jquery.js'></script><script src='/site.js'></script></body></html>"
    ).encode()

    assert not looks_like_spa(body, "https://example.com/news/1", "text/html")
