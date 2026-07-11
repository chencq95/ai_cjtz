"""Versioned deterministic taxonomy mappings used by the generic extractor."""

from __future__ import annotations

from .utils import normalize_text


TAXONOMY_VERSION = "2026.1"

PRODUCT_TYPE_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("api", ("api", "接口", "查询服务", "核验服务")),
    ("dataset", ("数据集", "数据包", "样本集", "语料库", "数据库")),
    ("report", ("数据报告", "分析报告", "研究报告", "指数报告")),
    ("model", ("模型", "算法", "评分卡", "画像")),
    ("component", ("组件", "工具", "连接器", "中间件", "软件")),
    ("application", ("应用", "解决方案", "场景服务", "平台服务")),
    ("data_service", ("数据服务", "定制服务", "咨询服务", "加工服务")),
]

INDUSTRY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "finance": ("金融", "银行", "保险", "证券", "信贷", "征信", "基金"),
    "healthcare": ("医疗", "医药", "健康", "医院", "医保", "疾病"),
    "transportation": ("交通", "道路", "公路", "铁路", "航空", "出行", "车辆"),
    "logistics": ("物流", "快递", "仓储", "供应链", "港口"),
    "government": ("政务", "公共数据", "政府", "行政", "监管"),
    "manufacturing": ("工业", "制造", "工厂", "设备", "机械"),
    "agriculture": ("农业", "农村", "农产品", "种植", "养殖", "林业", "渔业"),
    "energy": ("能源", "电力", "电网", "煤炭", "石油", "天然气", "新能源"),
    "tourism": ("文旅", "旅游", "景区", "酒店", "文化"),
    "education": ("教育", "学校", "高校", "教学", "培训"),
    "commerce": ("商业", "零售", "消费", "电商", "贸易", "市场"),
    "telecom": ("通信", "电信", "运营商", "网络"),
    "geospatial": ("地理", "地图", "测绘", "遥感", "空间", "位置"),
    "environment": ("环境", "气象", "生态", "碳排放", "水务", "污染"),
    "real_estate": ("房地产", "房产", "住房", "土地", "物业"),
    "human_resources": ("人力资源", "招聘", "人才", "就业", "社保"),
    "legal": ("法律", "司法", "法院", "法务", "知识产权"),
    "science": ("科研", "科学", "实验", "技术创新"),
}


def normalize_product_type(raw_value: str, context: str = "") -> tuple[str, float]:
    text = normalize_text(f"{raw_value} {context}").lower()
    for value, keywords in PRODUCT_TYPE_KEYWORDS:
        if any(keyword.lower() in text for keyword in keywords):
            return value, 0.96 if raw_value else 0.75
    return "other", 0.4


def classify_industries(*values: str) -> list[tuple[str, float, str]]:
    text = normalize_text(" ".join(filter(None, values)))
    results: list[tuple[str, float, str]] = []
    for normalized, keywords in INDUSTRY_KEYWORDS.items():
        matches = [keyword for keyword in keywords if keyword in text]
        if matches:
            results.append((normalized, min(0.65 + 0.08 * len(matches), 0.95), "/".join(matches)))
    return results

