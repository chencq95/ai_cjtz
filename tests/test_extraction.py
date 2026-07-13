from __future__ import annotations

from data_market_probe.extraction import extract_html, extract_json


def test_html_product_fields_dimensions_and_evidence() -> None:
    page = extract_html(
        """
        <html><head><title>企业征信数据产品详情</title></head><body>
          <h1>企业经营风险查询 API</h1>
          <table>
            <tr><th>产品名称</th><td>企业经营风险查询 API</td></tr>
            <tr><th>数据提供方</th><td>示例数据科技有限公司</td></tr>
            <tr><th>产品类型</th><td>数据接口</td></tr>
            <tr><th>所属行业</th><td>金融</td></tr>
            <tr><th>发布时间</th><td>2026-07-01</td></tr>
            <tr><th>交付方式</th><td>API 接口</td></tr>
          </table>
          <p class="detail">用于银行信贷风险管理的企业经营风险核验服务。</p>
        </body></html>
        """,
        "https://example.com/product/detail/1001",
        platform_province="湖北省",
        platform_city="武汉市",
    )
    assert len(page.items) == 1
    item = page.items[0]
    assert item.kind == "product"
    assert item.name == "企业经营风险查询 API"
    assert item.product_type == "api"
    assert item.provider == "示例数据科技有限公司"
    assert item.published_at is not None
    assert any(value.dimension_type == "industry" and value.normalized_value == "finance" for value in item.dimensions)
    assert any(value.dimension_type == "platform_province" and value.normalized_value == "湖北省" for value in item.dimensions)
    assert {value.field_name for value in item.evidence} >= {"name", "provider", "product_type_raw"}


def test_typed_product_route_wins_over_scenario_copy() -> None:
    page = extract_html(
        """
        <html><head><title>数据产品详情</title></head><body>
          <h1>城市水环境综合分析数据集</h1>
          <table><tr><th>产品名称</th><td>城市水环境综合分析数据集</td></tr></table>
          <p>面向水环境治理应用场景提供综合分析能力。</p>
        </body></html>
        """,
        "https://exchange.example/product/detail/2056",
    )

    assert len(page.items) == 1
    assert page.items[0].kind == "product"


def test_utility_class_product_links_are_preserved_as_catalog_items() -> None:
    page = extract_html(
        """
        <html><head><title>数据产品列表</title></head><body>
          <a href="/product/detail/1904" class="flex-1 flex flex-col">
            <div class="content-title"><div class="title">武汉燃气供气点活跃度数据</div></div>
            <div>交付方式：API接口</div>
            <div>应用场景：智慧城市，保险科技</div>
            <span class="product-org">武汉市燃气集团有限公司</span>
          </a>
        </body></html>
        """,
        "https://exchange.example/product/list",
    )

    assert len(page.items) == 1
    item = page.items[0]
    assert item.kind == "product"
    assert item.name == "武汉燃气供气点活跃度数据"
    assert item.provider == "武汉市燃气集团有限公司"
    assert item.delivery_method == "API接口"
    assert item.source_url == "https://exchange.example/product/detail/1904"


def test_json_catalog_records_are_extracted_without_html() -> None:
    page = extract_json(
        {
            "data": {
                "records": [
                    {
                        "productId": "p-1",
                        "productName": "智慧交通流量数据集",
                        "productType": "数据集",
                        "industryName": "交通运输",
                        "providerName": "城市交通研究院",
                        "publishTime": "2026-06-01",
                    }
                ]
            }
        },
        "https://example.com/api/product/list?page=1",
    )
    assert len(page.items) == 1
    item = page.items[0]
    assert item.external_id == "p-1"
    assert item.product_type == "dataset"
    assert any(value.normalized_value == "transportation" for value in item.dimensions)
