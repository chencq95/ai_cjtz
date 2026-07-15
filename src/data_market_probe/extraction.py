"""Evidence-preserving generic extraction for HTML and public JSON APIs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup, Tag

from .taxonomy import TAXONOMY_VERSION, classify_industries, normalize_product_type
from .utils import canonicalize_url, normalize_text, parse_datetime, safe_snippet


EXTRACTOR_VERSION = "generic-v1"
TARGET_TERMS = (
    "数据产品", "数据商品", "产品目录", "产品详情", "数据集", "数据接口", "数据服务",
    "数据组件", "组件", "应用场景", "数据场景", "解决方案", "场景案例", "数据需求",
    "product", "catalog", "dataset", "component", "scenario", "solution", "demand",
)
DETAIL_PATH_TERMS = (
    "detail", "info", "view", "product/", "goods/", "dataset/", "component/", "scene/",
    "scenario/", "solution/", "demand/", "productdetail", "product-detail",
)
GENERIC_NAMES = {
    "首页", "数据产品", "产品中心", "产品目录", "数据服务", "数据组件", "应用场景",
    "解决方案", "数据交易", "更多", "详情", "产品详情", "场景案例",
}

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("产品名称", "商品名称", "数据名称", "资源名称", "场景名称", "组件名称", "名称"),
    "description": ("产品描述", "产品简介", "商品描述", "数据描述", "场景描述", "功能简介", "简介", "描述"),
    "provider": ("数据提供方", "提供单位", "提供方", "供应商", "所属数商", "发布单位", "机构名称"),
    "product_type_raw": ("产品类型", "商品类型", "资源类型", "数据类型", "交付形态", "服务类型"),
    "industry_raw": ("所属行业", "应用行业", "行业分类", "行业领域", "应用领域", "领域"),
    "price_raw": ("产品价格", "挂牌价格", "价格", "计费方式", "参考价格"),
    "delivery_method": ("交付方式", "交付形式", "服务方式", "使用方式", "交付渠道"),
    "refresh_frequency": ("更新频率", "更新周期", "数据频率", "刷新频率"),
    "published_at": ("发布时间", "发布日期", "上架时间", "挂牌时间", "创建时间"),
    "source_updated_at": ("更新时间", "更新日期", "最后更新", "修改时间"),
    "data_period": ("数据时间范围", "数据周期", "覆盖时间", "数据期限", "时间范围"),
    "region_raw": ("覆盖地域", "所属区域", "服务区域", "数据地域", "地域范围"),
    "external_id": ("产品编号", "商品编号", "资源编号", "产品编码", "场景编号", "唯一标识"),
    "tags": ("标签", "关键词", "产品标签"),
}

JSON_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("productname", "goodsname", "dataname", "resourcename", "scenename", "componentname", "title", "name"),
    "description": ("productdesc", "description", "describe", "introduction", "summary", "content", "remark"),
    "provider": ("providername", "suppliername", "companyname", "orgname", "provider", "supplier"),
    "product_type_raw": ("producttype", "goodstype", "datatype", "resourcetype", "deliverytype", "typeName"),
    "industry_raw": ("industryname", "industry", "industrytype", "fieldname", "domain"),
    "price_raw": ("price", "productprice", "listingprice", "charge", "fee"),
    "delivery_method": ("deliverymethod", "deliverytype", "servicemode", "deliverymode"),
    "refresh_frequency": ("updatefrequency", "refreshfrequency", "updatecycle", "frequency"),
    "published_at": ("publishtime", "publishdate", "releasedate", "shelftime", "createdat", "createtime"),
    "source_updated_at": ("updatetime", "updatedat", "modifytime", "lastmodified"),
    "region_raw": ("regionname", "region", "coveragearea", "servicearea", "areaName"),
    "external_id": ("productid", "goodsid", "resourceid", "sceneid", "componentid", "dataid", "id", "code"),
    "tags": ("tags", "taglist", "keywords", "labels"),
}


def _key(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", value.lower())


ALIAS_LOOKUP = {
    _key(alias): field_name
    for field_name, aliases in FIELD_ALIASES.items()
    for alias in aliases
}
JSON_LOOKUP = {
    _key(alias): field_name
    for field_name, aliases in JSON_ALIASES.items()
    for alias in aliases
}


@dataclass(slots=True)
class Evidence:
    field_name: str
    raw_value: str
    locator: str
    method: str = "rule"
    confidence: float = 1.0


@dataclass(slots=True)
class Dimension:
    dimension_type: str
    raw_value: str
    normalized_value: str
    confidence: float
    method: str
    taxonomy_version: str = TAXONOMY_VERSION


@dataclass(slots=True)
class ExtractedItem:
    kind: str
    name: str
    source_url: str
    external_id: str = ""
    description: str = ""
    provider: str = ""
    product_type_raw: str = ""
    product_type: str = "other"
    price_raw: str = ""
    delivery_method: str = ""
    refresh_frequency: str = ""
    published_at: Any = None
    source_updated_at: Any = None
    data_period_start: Any = None
    data_period_end: Any = None
    source_fields: dict[str, Any] = field(default_factory=dict)
    normalized: dict[str, Any] = field(default_factory=dict)
    dimensions: list[Dimension] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    confidence: float = 0.0
    extractor_version: str = EXTRACTOR_VERSION


@dataclass(slots=True)
class ExtractedPage:
    title: str
    text: str
    links: list[tuple[str, str, float]]
    items: list[ExtractedItem]


def link_relevance(url: str, anchor: str) -> float:
    text = f"{url} {anchor}".lower()
    score = sum(1.5 for term in TARGET_TERMS if term.lower() in text)
    if any(term in text for term in DETAIL_PATH_TERMS):
        score += 2.0
    if re.search(r"(?:page|current|pageno|pageindex)=\d+", text):
        score += 1.0
    if re.search(r"\.(?:jpg|jpeg|png|gif|svg|css|woff2?|ttf|mp4|mp3|zip|rar|7z|xlsx?|csv)(?:\?|$)", text):
        score -= 10.0
    if any(term in text for term in ("login", "logout", "register", "javascript:", "mailto:")):
        score -= 10.0
    return score


def _collect_pairs(soup: BeautifulSoup) -> tuple[dict[str, str], dict[str, Evidence]]:
    pairs: dict[str, str] = {}
    evidence: dict[str, Evidence] = {}

    def add(raw_label: str, raw_value: str, locator: str) -> None:
        label = normalize_text(raw_label).rstrip(":：")
        value = normalize_text(raw_value)
        canonical = ALIAS_LOOKUP.get(_key(label))
        if not canonical or not value or value == label or len(value) > 10_000:
            return
        if canonical not in pairs or len(value) > len(pairs[canonical]):
            pairs[canonical] = value
            evidence[canonical] = Evidence(canonical, safe_snippet(value, 2_000), locator)

    for index, row in enumerate(soup.select("table tr")):
        cells = row.find_all(["th", "td"], recursive=False)
        if len(cells) >= 2:
            for offset in range(0, len(cells) - 1, 2):
                add(cells[offset].get_text(" "), cells[offset + 1].get_text(" "), f"table tr[{index}]")
    for index, term in enumerate(soup.find_all("dt")):
        value = term.find_next_sibling("dd")
        if value:
            add(term.get_text(" "), value.get_text(" "), f"dl dt[{index}]+dd")
    for index, node in enumerate(soup.select("li, p, div.form-item, div.info-item, div.detail-item")):
        text = normalize_text(node.get_text(" ", strip=True))
        if 2 < len(text) <= 600 and re.search(r"[:：]", text):
            label, value = re.split(r"[:：]", text, maxsplit=1)
            if len(label) <= 24:
                add(label, value, f"text-pair[{index}]")
    for index, label_node in enumerate(soup.select("[class*='label'], [class*='Label']")):
        label = normalize_text(label_node.get_text(" ", strip=True))
        if _key(label) not in ALIAS_LOOKUP:
            continue
        sibling = label_node.find_next_sibling()
        if sibling:
            add(label, sibling.get_text(" ", strip=True), f"class-label[{index}]")
    return pairs, evidence


def _kind_and_score(url: str, title: str, fields: dict[str, str], body_text: str) -> tuple[str, float]:
    lower_url = url.lower()
    context = normalize_text(f"{url} {title} {body_text[:1500]}").lower()
    # A typed route is stronger evidence than descriptive copy.  Product pages
    # routinely mention the application scenarios they support, which must not
    # turn the product itself into a scenario entity.
    if any(term in lower_url for term in ("/demand", "demand/", "requirement/")):
        kind = "demand"
    elif any(term in lower_url for term in ("/scenario", "scenario/", "/scene/", "/solution/")):
        kind = "scenario"
    elif any(term in lower_url for term in ("/component", "component/", "/tool/", "/algorithm/", "/aifactory")):
        kind = "component"
    elif any(term in lower_url for term in ("/product", "product/", "/goods/", "/dataset/", "/catalog/")):
        kind = "product"
    elif any(term in context for term in ("应用场景", "数据场景", "场景详情", "scenario", "解决方案")):
        kind = "scenario"
    elif any(term in context for term in ("数据组件", "组件详情", "component", "算法工具", "开发工具")):
        kind = "component"
    elif any(term in context for term in ("数据需求", "需求详情", "demand")):
        kind = "demand"
    else:
        kind = "product"

    score = 0.0
    if any(term in lower_url for term in DETAIL_PATH_TERMS):
        score += 3.0
    if any(term.lower() in title.lower() for term in TARGET_TERMS):
        score += 2.0
    score += min(len(fields) * 0.8, 4.0)
    if "name" in fields:
        score += 1.5
    if any(key in fields for key in ("provider", "product_type_raw", "delivery_method", "price_raw")):
        score += 1.5
    if any(term in title for term in ("新闻", "通知", "公告", "政策", "资讯")) and len(fields) < 3:
        score -= 4.0
    return kind, score


def _name_from_page(soup: BeautifulSoup, title: str, fields: dict[str, str]) -> str:
    candidates = [fields.get("name", "")]
    for selector in ("h1", ".detail-title", ".product-title", ".title h2", "h2"):
        node = soup.select_one(selector)
        if node:
            candidates.append(node.get_text(" ", strip=True))
    candidates.extend(re.split(r"[-_|—]", title)[:1])
    for candidate in candidates:
        value = normalize_text(candidate)
        if 2 <= len(value) <= 300 and value not in GENERIC_NAMES:
            return value
    return ""


def _description(soup: BeautifulSoup, fields: dict[str, str]) -> str:
    if fields.get("description"):
        return fields["description"]
    meta = soup.select_one("meta[name='description'], meta[property='og:description']")
    if meta and meta.get("content"):
        value = normalize_text(str(meta["content"]))
        if len(value) >= 15:
            return value
    paragraphs = [normalize_text(node.get_text(" ", strip=True)) for node in soup.select("article p, .content p, .detail p")]
    paragraphs = [value for value in paragraphs if 20 <= len(value) <= 5_000]
    return max(paragraphs, key=len, default="")


def _extract_period(value: str) -> tuple[Any, Any]:
    dates = re.findall(r"(?:19|20)\d{2}[-/.年]\d{1,2}(?:[-/.月]\d{1,2})?", value or "")
    if not dates:
        return None, None
    start = parse_datetime(dates[0])
    end = parse_datetime(dates[-1]) if len(dates) > 1 else None
    return start, end


def _build_item(
    *, kind: str, name: str, source_url: str, values: dict[str, Any], evidence: dict[str, Evidence],
    confidence: float, platform_province: str, platform_city: str,
) -> ExtractedItem:
    description = normalize_text(str(values.get("description") or ""))
    product_type_raw = normalize_text(str(values.get("product_type_raw") or ""))
    product_type, type_confidence = normalize_product_type(product_type_raw, f"{kind} {name} {description[:600]}")
    period_start, period_end = _extract_period(str(values.get("data_period") or ""))
    item = ExtractedItem(
        kind=kind,
        name=normalize_text(name),
        source_url=source_url,
        external_id=normalize_text(str(values.get("external_id") or "")),
        description=description,
        provider=normalize_text(str(values.get("provider") or "")),
        product_type_raw=product_type_raw,
        product_type=product_type,
        price_raw=normalize_text(str(values.get("price_raw") or "")),
        delivery_method=normalize_text(str(values.get("delivery_method") or "")),
        refresh_frequency=normalize_text(str(values.get("refresh_frequency") or "")),
        published_at=parse_datetime(values.get("published_at")),
        source_updated_at=parse_datetime(values.get("source_updated_at")),
        data_period_start=period_start,
        data_period_end=period_end,
        source_fields={key: value for key, value in values.items() if value not in (None, "")},
        normalized={"taxonomy_version": TAXONOMY_VERSION},
        evidence=list(evidence.values()),
        confidence=min(max(confidence / 10.0, 0.0), 0.99),
    )
    item.dimensions.append(Dimension("product_type", product_type_raw, product_type, type_confidence, "source" if product_type_raw else "rule"))
    industry_raw = normalize_text(str(values.get("industry_raw") or ""))
    for industry, industry_confidence, matches in classify_industries(industry_raw, name, description[:1_000]):
        item.dimensions.append(Dimension("industry", industry_raw or matches, industry, industry_confidence, "source+rule" if industry_raw else "rule"))
    region_raw = normalize_text(str(values.get("region_raw") or ""))
    for dimension_type, raw, normalized in (
        ("platform_province", platform_province, platform_province),
        ("platform_city", platform_city, platform_city),
        ("coverage_region", region_raw, region_raw),
    ):
        if normalized:
            item.dimensions.append(Dimension(dimension_type, raw, normalized, 1.0 if dimension_type.startswith("platform") else 0.95, "registry" if dimension_type.startswith("platform") else "source"))
    if item.delivery_method:
        item.dimensions.append(Dimension("delivery_method", item.delivery_method, item.delivery_method, 0.95, "source"))
    if item.refresh_frequency:
        item.dimensions.append(Dimension("refresh_frequency", item.refresh_frequency, item.refresh_frequency, 0.95, "source"))
    return item


def _external_id_from_url(url: str) -> str:
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    for key in ("productId", "goodsId", "resourceId", "sceneId", "id", "code"):
        if query.get(key):
            return str(query[key][0])
    tail = parts.path.rstrip("/").rsplit("/", 1)[-1]
    return tail if re.fullmatch(r"[0-9a-fA-F_-]{6,80}", tail) else ""


def extract_html(
    html: str,
    url: str,
    *,
    platform_province: str = "",
    platform_city: str = "",
) -> ExtractedPage:
    soup = BeautifulSoup(html, "lxml")
    title = normalize_text(soup.title.get_text(" ") if soup.title else "")
    links: list[tuple[str, str, float]] = []
    seen_links: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        target = canonicalize_url(url, str(anchor.get("href")))
        if not target or target in seen_links:
            continue
        seen_links.add(target)
        text = normalize_text(anchor.get_text(" ", strip=True))
        links.append((target, safe_snippet(text, 300), link_relevance(target, text)))

    for node in soup(["script", "style", "noscript", "svg"]):
        node.decompose()
    body_text = normalize_text(soup.get_text(" ", strip=True))
    fields, evidence = _collect_pairs(soup)
    kind, score = _kind_and_score(url, title, fields, body_text)
    items: list[ExtractedItem] = []
    name = _name_from_page(soup, title, fields)
    path_lower = urlsplit(url).path.lower().rstrip("/")
    list_like_page = any(
        marker in path_lower
        for marker in ("/list", "/catalog", "/search", "/index")
    ) or path_lower.endswith(("/product", "/goods", "/dataset", "/scenario", "/demand"))
    # Public exchange detail pages often expose only a meaningful <title> and
    # a stable detail URL; requiring table fields would silently drop those
    # records.  Keep the stricter threshold for ordinary pages, but allow a
    # title-only item when the URL is an explicit detail/product route.
    detail_route = any(term in path_lower for term in DETAIL_PATH_TERMS)
    minimum_score = 3.0 if detail_route else 4.5
    if name and score >= minimum_score and (not list_like_page or bool(fields.get("name"))):
        fields.setdefault("description", _description(soup, fields))
        fields.setdefault("external_id", _external_id_from_url(url))
        items.append(_build_item(kind=kind, name=name, source_url=url, values=fields, evidence=evidence, confidence=score, platform_province=platform_province, platform_city=platform_city))

    # Preserve list-only catalog cards so inaccessible details do not erase public listings.
    if any(term.lower() in f"{title} {url}".lower() for term in TARGET_TERMS):
        card_selectors = ".product-item, .goods-item, .data-item, .scenario-item, .scene-item, [class*='product-card'], [class*='goods-card']"
        cards = list(soup.select(card_selectors))
        cards.extend(
            soup.select(
                "a[href*='/product/detail/'], a[href*='/goods/detail/'], "
                "a[href*='/scenario/detail/'], a[href*='/scene/detail/'], "
                "a[href*='/demand/detail/'], a[href*='/requirement/detail/']"
            )
        )
        seen_card_urls: set[str] = set()
        for index, card in enumerate(cards):
            anchor = card if card.name == "a" and card.get("href") else card.find("a", href=True)
            if not isinstance(anchor, Tag):
                continue
            target = canonicalize_url(url, str(anchor.get("href")))
            if not target or target in seen_card_urls:
                continue
            seen_card_urls.add(target)
            name_node = anchor.select_one(
                ".content-title .title, .product-title, .goods-title, "
                ".card-title, [class~='title'], h2, h3, h4"
            )
            card_name = normalize_text(
                name_node.get_text(" ", strip=True) if name_node else anchor.get_text(" ", strip=True)
            )
            if not target or not (2 <= len(card_name) <= 300) or card_name in GENERIC_NAMES:
                continue
            if any(existing.source_url == target for existing in items):
                continue
            card_text = normalize_text(anchor.get_text(" ", strip=True))
            provider_node = anchor.select_one(".product-org, .provider, [class*='provider']")
            values = {
                "external_id": _external_id_from_url(target),
                "description": safe_snippet(card_text, 2_000),
            }
            if provider_node:
                values["provider"] = normalize_text(provider_node.get_text(" ", strip=True))
            delivery_match = re.search(
                r"交付方式\s*[：:]\s*(.+?)(?=\s+(?:应用场景|所属行业|提供方|供应商)\s*[：:]|[，,；;]|$)",
                card_text,
            )
            if delivery_match:
                values["delivery_method"] = normalize_text(delivery_match.group(1))
            card_kind, _ = _kind_and_score(target, card_name, values, card_text)
            card_evidence = {
                "name": Evidence("name", card_name, f"catalog-card[{index}]", confidence=0.75)
            }
            if values.get("provider"):
                card_evidence["provider"] = Evidence(
                    "provider", str(values["provider"]), f"catalog-card[{index}].provider", confidence=0.70
                )
            items.append(_build_item(kind=card_kind, name=card_name, source_url=target, values=values, evidence=card_evidence, confidence=6.0, platform_province=platform_province, platform_city=platform_city))
    return ExtractedPage(title=title, text=body_text, links=links, items=items)


def _walk_records(value: Any, path: str = "$") -> Iterable[tuple[str, dict[str, Any]]]:
    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            yield from _walk_records(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_records(child, f"{path}[{index}]")


def _json_values(record: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Evidence]]:
    values: dict[str, Any] = {}
    evidence: dict[str, Evidence] = {}
    for key, value in record.items():
        canonical = JSON_LOOKUP.get(_key(str(key)))
        if not canonical or value in (None, "", [], {}):
            continue
        if isinstance(value, (dict, list)):
            rendered = json.dumps(value, ensure_ascii=False, default=str)
        else:
            rendered = str(value)
        if len(rendered) > 20_000:
            continue
        values.setdefault(canonical, rendered)
        evidence.setdefault(canonical, Evidence(canonical, safe_snippet(rendered, 2_000), f"json.{key}", "json", 1.0))
    return values, evidence


def extract_json(
    raw: str | bytes | dict[str, Any] | list[Any],
    url: str,
    *,
    platform_province: str = "",
    platform_city: str = "",
) -> ExtractedPage:
    payload = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    items: list[ExtractedItem] = []
    seen: set[tuple[str, str]] = set()
    url_context = url.lower()
    relevant_url = any(term.lower() in url_context for term in TARGET_TERMS)
    for path, record in _walk_records(payload):
        values, evidence = _json_values(record)
        name = normalize_text(str(values.get("name") or ""))
        supporting = len(set(values) - {"name", "description", "external_id"})
        if not name or name in GENERIC_NAMES or len(name) > 300:
            continue
        if not relevant_url and supporting < 2:
            continue
        external_id = normalize_text(str(values.get("external_id") or ""))
        key = (external_id, name)
        if key in seen:
            continue
        seen.add(key)
        kind, score = _kind_and_score(url, name, {key: str(value) for key, value in values.items()}, str(values.get("description") or ""))
        if relevant_url:
            score += 1.5
        item_url = url
        for candidate_key in ("detailUrl", "url", "link", "productUrl"):
            candidate = record.get(candidate_key)
            if isinstance(candidate, str):
                canonical = canonicalize_url(url, candidate)
                if canonical:
                    item_url = canonical
                    break
        items.append(_build_item(kind=kind, name=name, source_url=item_url, values=values, evidence=evidence, confidence=max(score, 5.0), platform_province=platform_province, platform_city=platform_city))
    text = normalize_text(json.dumps(payload, ensure_ascii=False, default=str))
    return ExtractedPage(title="", text=text, links=[], items=items)
