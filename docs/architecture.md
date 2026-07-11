# 数据交易平台采集与 Agent 服务架构

## 1. 文档状态与交付边界

本文定义项目的目标架构和接入合同，不代表 38 个来源已经完成连通性、合规性、栏目覆盖或适配器验证。

当前输入是两张清单截图。截图中的名称和网址只能作为种子数据；每个来源进入生产采集前，必须独立完成：

1. 平台主体和官方域名核验。
2. `robots.txt`、用户协议、隐私政策及自动化访问边界检查。
3. 产品、组件、场景等公开栏目盘点。
4. 列表总数、分页方式、详情主键、更新时间字段和下架语义确认。
5. 静态、公开 API 或 Playwright 适配器的回归测试。
6. 首次完整扫描和覆盖对账。

本项目的“全量”限定为：经核验属于目标官网、无需绕过登录/验证码/付费/访问控制、且允许采集的公开元数据及获准附件。受限内容必须记入覆盖账本，不能用技术绕过代替授权。

## 2. 38 个来源种子注册表

下表逐行转录自输入截图。`PENDING_AUDIT` 表示尚未完成当前可用性和官方性核验；空网址保持为空，后续只能通过平台主体、政府公告或可信渠道补齐。

| 序号 | 地区 | 来源名称 | 截图种子网址 | 初始状态 |
| ---: | --- | --- | --- | --- |
| 1 | 北京市 | 北京国际大数据交易所 | `https://www.bjidex.com/` | `PENDING_AUDIT` |
| 2 | 上海市 | 上海数据交易所 | `https://www.chinadep.com/website/` | `PENDING_AUDIT` |
| 3 | 广东省 | 广州数据交易所 | `https://www.cantonde.com/jydt.html#/jydtIndex` | `PENDING_AUDIT` |
| 4 | 浙江省 | 浙江大数据交易服务平台 | `https://ditm.zjdex.com/home` | `PENDING_AUDIT` |
| 5 | 福建省 | 福建省公共数据资源开发服务平台 | `https://www.fjbigdata.com.cn/#/home` | `PENDING_AUDIT` |
| 6 | 贵州省 | 贵阳大数据交易所 | `https://www.gzdex.com.cn/` | `PENDING_AUDIT` |
| 7 | 江苏省 | 江苏数据交易所 | `https://www.jsdataex.com/trade-home/#/` | `PENDING_AUDIT` |
| 8 | 湖北省 | 湖北省数据流通交易平台 | `https://dex.hubei-data.com/` | `PENDING_AUDIT` |
| 9 | 陕西省 | 陕西丝路数据交易中心 | `https://snsldata.com/` | `PENDING_AUDIT` |
| 10 | 重庆市 | 西部数据交易中心 | `https://westdex.com.cn/` | `PENDING_AUDIT` |
| 11 | 山东省 | 山东数据交易有限公司 | `https://www.sddep.com/` | `PENDING_AUDIT` |
| 12 | 安徽省 | 安徽省数据交易所 | `https://www.ahdexc.com/home` | `PENDING_AUDIT` |
| 13 | 湖南省 | 湖南大数据交易所 | `https://hunandex.com/` | `PENDING_AUDIT` |
| 14 | 江西省 | 江西省公共数据交易平台 | `https://dex.jxggzyjy.cn/about` | `PENDING_AUDIT` |
| 15 | 广西 | 北部湾大数据交易中心 | `https://www.bbgdex.com/` | `PENDING_AUDIT` |
| 16 | 云南省 | 昆明国际数据交易所 | `https://kmide.com/` | `PENDING_AUDIT` |
| 17 | 吉林省 | 长春数据交易中心 | `https://jiaoyi.ccdatacenter.cn/#/` | `PENDING_AUDIT` |
| 18 | 黑龙江省 | 哈尔滨数据交易中心 | `https://www.harbindex.com/#/` | `PENDING_AUDIT` |
| 19 | 甘肃省 | 甘交所数据要素交易平台 | `https://gsdep.cn/homeMain` | `PENDING_AUDIT` |
| 20 | 天津市 | 北方大数据交易中心 | `https://www.datadmz.com/` | `PENDING_AUDIT` |
| 21 | 深圳市 | 深圳数据交易所 | `https://www.szdex.com/portal/home` | `PENDING_AUDIT` |
| 22 | 厦门市 | 厦门公共数据融合开发平台 | `https://www.xmdatax.com/portal/index` | `PENDING_AUDIT` |
| 23 | 宁波市 | 宁波市可信城市数据空间 | `https://portaldataplatform.nbnsjk.com/home` | `PENDING_AUDIT` |
| 24 | 成都市 | 成都市公共数据运营服务平台 | `https://www.cddataos.com/data-operation-web/#/home` | `PENDING_AUDIT` |
| 25 | 郑州市 | 郑州市数据交易中心 | `https://www.zzbdex.com/` | `PENDING_AUDIT` |
| 26 | 杭州市 | 杭州数据交易所 | `https://www.hzdex.cn/` | `PENDING_AUDIT` |
| 27 | 温州市 | 温州数据交易中心 | `https://wzdex.com.cn/` | `PENDING_AUDIT` |
| 28 | 武汉市 | 长江大数据交易中心 | `https://cjdataex.cn/` | `PENDING_AUDIT` |
| 29 | 内蒙古 | 内蒙古数据交易中心 | `https://www.nmcuidb.com/` | `PENDING_AUDIT` |
| 30 | 四川省 | 四川数字中心 | `https://www.sdex.com.cn/` | `PENDING_AUDIT` |
| 31 | 山西省 | 山西数据交易中心 | `http://www.sxfae.com/` | `PENDING_AUDIT` |
| 32 | 河北省 | 河北省数据交易服务中心 | 空 | `PENDING_AUDIT` |
| 33 | 海南省 | 海南公共数据开发服务平台 | `http://www.hainans.net/` | `PENDING_AUDIT` |
| 34 | 宁夏 | 宁夏数据要素运营中心 | 空 | `PENDING_AUDIT` |
| 35 | 青海 | 青海数据要素流通服务创新中心 | 空 | `PENDING_AUDIT` |
| 36 | 新疆 | 广州数据交易所（喀什）服务基地 | 空 | `PENDING_AUDIT` |
| 37 | 西藏 | 广州数据交易所（拉萨）服务基地 | 空 | `PENDING_AUDIT` |
| 38 | 国家数据局 | 国家数据局 | `https://www.nda.gov.cn/sjj/index_pc.html` | `PENDING_SCOPE` |

第 38 条更可能是政策和标准参考来源而非数据产品交易平台，应在范围评审中决定是否进入业务目录，或者仅作为 `REFERENCE` 来源。不得因为其出现在截图中就默认抓取整个政府网站。

### 2.1 注册表字段

生产库的 `source_platform` 至少包含：

- `platform_id`：内部稳定 UUID。
- `seed_no`：截图序号，便于追溯。
- `name`、`region_code`、`operator_name`。
- `source_role`：`EXCHANGE`、`OPERATING_PLATFORM`、`SERVICE_BASE`、`REFERENCE`。
- `seed_url`、`verified_home_url`、`allowed_domains`。
- `timezone`：默认 `Asia/Shanghai`。
- `onboarding_status`：`PENDING_AUDIT`、`ACTIVE`、`BLOCKED`、`OFFLINE`、`RETIRED`。
- `legal_review_status`、`robots_checked_at`、`terms_checked_at`。
- `default_rate_limit`、`max_concurrency`、`owner`。
- `last_verified_at`、`verification_evidence`。

种子网址和核验后的主页必须分开保存，不能覆盖原始输入。所有跳转域名、市场子域和静态资源域必须逐一加入白名单后才能访问。

## 3. Collection 覆盖账本

站点级“已接入”不足以证明内容完整。一个平台可把产品市场、组件市场、场景、需求和数商拆到不同子域，因此完整性必须在 `source_collection` 粒度核算。

推荐对象类型：

- `DATA_PRODUCT`：数据集、数据报告、数据接口、数据服务、模型交付等。
- `DATA_COMPONENT`：算法、模型、工具、隐私计算、可信流通或加工组件。
- `DATA_SCENARIO`：应用场景、解决方案、案例、专区。
- `DEMAND`：公开需求和供需撮合信息。
- `PROVIDER`：公开数商、服务商和产品提供方。
- `POLICY_STANDARD`：仅在明确纳入范围时采集。

`source_collection` 至少包含：

- `collection_id`、`platform_id`、`object_type`、`name`。
- `entry_url`、`list_url_pattern`、`detail_url_pattern`。
- `fetch_mode`：`STATIC`、`PUBLIC_API`、`PLAYWRIGHT`、`FILE_INDEX`。
- `pagination_mode`、`stable_key_rule`、`source_update_field`。
- `expected_total`、`expected_total_observed_at`。
- `last_complete_run_id`、`last_complete_at`。
- `coverage_status`：`UNKNOWN`、`COMPLETE`、`PARTIAL`、`BLOCKED`、`OUT_OF_SCOPE`。
- `discovered_count`、`detail_success_count`、`restricted_count`。
- `block_reason`、`evidence_snapshot_id`。
- `adapter_name`、`adapter_version`、`adapter_verified_at`。

每次运行生成不可变的 `collection_run_manifest`，记录预期页数、已访问页数、列表 ID 数、详情成功数、失败数、受限数和完整性判断。仅当分页闭合、列表无未处理错误且质量门禁通过时，运行才可标记为 `COMPLETE`。

## 4. 系统分层

```text
source_platform / source_collection
                |
                v
       调度与策略控制平面
                |
                v
  静态抓取 -> 公开 API -> Playwright 兜底
                |
                v
         原始快照与附件层
                |
                v
       站点解析器与标准化层
                |
                v
   来源记录 -> 标准实体 -> 版本/证据
                |
                v
 PostgreSQL / 检索索引 / REST / MCP
```

### 4.1 控制平面

控制平面读取平台注册表、collection 账本和适配器版本，生成有界任务。它负责：

- 每域名并发和令牌桶限速。
- 运行租约、幂等键、重试和熔断。
- 每日增量、每周校准及人工回补。
- 完整性计算和原子发布。
- 审计日志、指标和告警。

### 4.2 抓取平面

抓取顺序固定为低成本优先：

1. 公开 Sitemap、RSS、JSON API 和页面内嵌 JSON。
2. Scrapy/httpx 获取静态 HTML。
3. Playwright 观察公开页面自身发起的 XHR/fetch；经合规核验后，优先固化为公开 API 适配器。
4. 没有稳定接口时，才使用浏览器渲染 DOM。
5. PDF、Office 和图片型页面进入文件解析/OCR 流程。

浏览器只访问平台注册表中的白名单域名，阻断第三方广告、分析脚本、私网地址和非必要下载。不得处理验证码、伪造登录状态或破解接口签名。

### 4.3 原始层

每个网络响应都先落原始层，再执行解析。`raw_snapshot` 推荐字段：

- `snapshot_id`、`run_id`、`platform_id`、`collection_id`。
- `requested_url`、`final_url`、`parent_url`、`discovery_method`。
- `http_status`、`fetched_at`、`response_headers`。
- `etag`、`last_modified`、`content_type`、`charset`。
- `raw_sha256`、`normalized_content_sha256`。
- `body_text` 或 `object_uri`、`object_sha256`、`object_size`。
- `rendered_dom_uri`、`screenshot_uri`，仅在 Playwright 需要时写入。
- `adapter_version`、`fetch_error_code`、`fetch_error_detail`。

HTML/JSON 的结构化结果和可检索文本进入 PostgreSQL。大文件放 MinIO/S3，数据库保留不可篡改哈希、解析文本和对象 URI；不建议将大二进制直接放入业务表。

## 5. 实体、挂牌记录、版本与证据

### 5.1 两层身份模型

必须区分：

- `source_record`：某个平台上的一条挂牌或发布记录。
- `catalog_entity`：跨平台归一后的产品、组件或场景概念。

同一产品在两个平台上的价格、状态、挂牌时间和源 ID 均应保留。跨源只有在统一社会信用代码、稳定产品编号等强证据一致时才能自动合并；名称相似只能生成 `POSSIBLE_SAME_AS` 候选。

`source_record` 的稳定键优先级：

1. 来源 API 明确 ID。
2. 页面明确产品/组件编号。
3. 规范化详情 URL。
4. 仅在前三者都不存在时，使用名称、提供方和首次发布时间的复合指纹，并标记低稳定性。

### 5.2 通用字段

`catalog_entity` 至少承载：

- `entity_id`、`entity_type`、名称、摘要、描述。
- 原生类型和规范产品类型。
- 提供方、运营方、品牌和公开产品编号。
- 交付方式、更新频率、价格表达、上架状态。
- 数据覆盖周期、更新/发布时间。
- 行业、地域、标签和应用场景。
- 来源 URL、首次/最后发现时间、当前版本。

产品、组件和场景各自扩展：

- 产品：数据形态、字段/样例、覆盖范围、交付接口、使用限制。
- 组件：能力、输入输出、接口协议、部署方式、依赖和版本。
- 场景：业务问题、参与方、使用产品/组件、业务环节和公开成效。

### 5.3 维度语义

统一分类必须保留原始标签和分类体系版本：

- 产品类型：数据集、数据报告、数据接口、数据服务、模型交付、工具/组件、应用/场景及其他。
- 行业：保存提供方行业、数据内容行业和目标应用行业三种角色；统一映射表带标准版本。
- 地域：区分平台所在地、提供方所在地、数据覆盖地域、服务地域、场景地域。
- 时间：区分发布时间、来源更新时间、数据覆盖起止、首次/最后发现、抓取时间和版本有效期。

解析优先级为：来源 API/显式字段、确定性选择器、规则字典、NLP/LLM。模型不能补造缺失信息；低置信结果保留为空或进入人工复核。

### 5.4 版本与字段证据

`source_record_version` 和 `entity_version` 使用 SCD Type 2：

- `valid_from`、`valid_to`、`is_current`。
- `semantic_hash`、`change_type`、`diff_json`。
- `created_by_run_id`、`parser_version`、`taxonomy_version`。

`field_evidence` 为每个关键字段保存：

- `entity_version_id`、`field_name`、`field_value`。
- `snapshot_id`、原文片段、JSONPath/CSS/XPath。
- `extraction_method`、`extractor_version`、`confidence`。
- `review_status`、`reviewed_by`、`reviewed_at`。

Agent 输出中的可争议字段必须能回溯至该证据，不得只返回无来源的模型结论。

## 6. 存储与推荐技术栈

- Python、Scrapy、httpx、Playwright：采集和动态页面适配。
- Pydantic、SQLAlchemy、Alembic：数据合同、持久化和迁移。
- PostgreSQL：平台注册表、结构化实体、版本、证据、运行和审计。
- JSONB：保留来源特有扩展字段，不替代核心规范字段。
- pgvector：语义召回；先与 PostgreSQL 全文检索组成混合检索。
- MinIO/S3：HTML 大快照、PDF、Office、图片和截图。
- Airflow/Celery 或等价编排：适合后续扩展；当前 Windows 单机交付先由任务计划程序调用统一 CLI。
- Prometheus/Grafana/Loki/OpenTelemetry：指标、日志和链路。

项目初期不要求 Kafka。结构化变更使用 PostgreSQL transactional outbox，只有出现多个高吞吐下游时再引入消息平台。

## 7. REST 与 MCP Agent 服务

对外提供只读 REST/OpenAPI 3.1，并在同一服务层封装 MCP tools。两种接口共享查询服务、权限、审计和返回数据合同。

目标 REST 接口：

- `GET /v1/catalog/items`：关键词、对象类型、产品类型、行业、地域、平台和时间过滤，游标分页。
- `GET /v1/catalog/items/{id}`：当前标准实体、挂牌记录和证据摘要。
- `GET /v1/catalog/items/{id}/history`：历史版本和差异。
- `GET /v1/catalog/items/{id}/as-of`：指定时间点状态。
- `GET /v1/changes`：按游标读取新增、更新、下架和恢复事件。
- `GET /v1/taxonomies`：分类体系和版本。
- `GET /v1/platforms/{id}/coverage`：来源/collection 覆盖与新鲜度。

目标 MCP tools：

- `search_catalog`
- `get_item`
- `get_item_history`
- `get_item_as_of`
- `get_changes`
- `find_related_items`
- `list_taxonomy`
- `get_source_evidence`
- `get_crawl_health`

每个结果至少返回 `source_url`、`data_as_of`、`last_crawled_at`、`version`、字段置信度和来源证据。禁止暴露任意 SQL、文件系统访问或抓取器控制能力。

抓取文本一律视为不可信数据，不能拼入 Agent 的系统指令或被当作可执行操作。具体安全边界见 [compliance.md](compliance.md)。

## 8. 发布门禁

任何平台从 `PENDING_AUDIT` 切换为 `ACTIVE` 前，至少满足：

1. 官方域名、跳转域名和所有访问域名已核验并入白名单。
2. 公开元数据范围及法律审查有记录。
3. 所有纳入范围的 collection 已建账。
4. 适配器有固定回归样本，列表和详情测试通过。
5. 首次完整扫描完成，若页面给出总数则完成 ID 对账。
6. 核心字段质量门禁和重复检查通过。
7. 运行日志、失败审计和告警可以查询。
8. 未声称受限内容已采集，也未使用验证码或访问控制绕过。

增量算法见 [incremental-strategy.md](incremental-strategy.md)，Windows 调度和运维见 [operations.md](operations.md)。
