# 全量、增量、版本与下架判定策略

## 1. 目标与基本约束

本策略解决四个问题：

1. 首次如何建立可对账的完整基线。
2. 每日如何只抓新增和可能变化的内容。
3. 没有更新时间或稳定 API 时如何判断变化。
4. 如何避免因超时、分页漂移或站点故障误判下架。

本文描述目标实现，不代表任何一个平台已经验证支持 ETag、更新时间水位、公开 API 或稳定分页。具体信号必须在 `source_collection` 中按站点配置并经回归测试。

核心不变量：

- 原始响应先保存，解析和发布随后进行。
- 一个成功的网络请求不等于一次完整 collection 扫描。
- `PARTIAL` 或 `FAILED` 运行绝不能触发下架。
- 同一来源记录在同一语义内容下不能重复产生版本。
- 同一任务重跑必须得到相同数据库结果。
- 当前表的切换在质量门禁后原子完成。

## 2. 运行类型

### 2.1 `BASELINE_FULL`

平台首次接入时执行：

- 从所有已核验 collection 入口遍历全部分页。
- 收集所有稳定来源 ID 和详情 URL。
- 抓取所有公开详情及获准附件。
- 建立原始快照、来源记录、标准实体、字段证据和初始版本。
- 若页面或 API 显示总数，完成 `expected_total` 与去重后来源 ID 的对账。
- 运行状态必须为 `COMPLETE` 才能成为后续增量基线。

若存在登录、验证码、不可达页面或未知分页，基线只能标记为 `PARTIAL`/`BLOCKED`，不得将已抓到的数据称为该平台全量。

### 2.2 `DAILY_INCREMENTAL`

Windows 每日 02:30 触发。读取上一次成功检查点，使用条件请求、来源更新时间、水位回退和哈希判断变化。

### 2.3 `WEEKLY_RECONCILE`

每周执行全部列表页扫描，校验来源 ID 集合、分页总数和潜在下架；已知详情使用条件请求或详情刷新 TTL，避免无意义重下载。

### 2.4 `DETAIL_REFRESH`

用于没有可靠列表摘要/更新时间的来源。按 `detail_refresh_interval` 轮换刷新详情，确保长期存在但被静默修改的记录最终被发现。建议默认 30 天，并允许按平台缩短。

### 2.5 `BACKFILL` 与 `REPLAY`

- `BACKFILL`：指定平台、collection、时间范围或 ID 范围补抓。
- `REPLAY`：不访问外网，使用已保存原始快照重新解析，用于解析器、分类体系或模型升级。

两者不得改写历史原始快照；新解析结果必须记录新的解析器/分类版本。

## 3. 身份、幂等和检查点

### 3.1 来源记录身份

`source_record_key` 优先级：

1. 公开 API 的稳定业务 ID。
2. 页面明确产品、组件或场景编号。
3. 去除追踪参数后的规范详情 URL。
4. 名称、提供方、首次发布时间的复合指纹，且必须标记 `LOW_STABILITY`。

数据库唯一约束建议为：

```text
(platform_id, collection_id, object_type, source_record_key)
```

抓取任务幂等键建议为：

```text
(run_id, collection_id, canonical_request_url, request_variant)
```

版本唯一约束建议为：

```text
(source_record_id, semantic_hash)
```

### 3.2 Collection 检查点

每个 collection 保存：

- `last_successful_run_id`
- `last_complete_run_id`
- `last_watermark_value`
- `last_watermark_source_timezone`
- `last_cursor`
- `last_etag`、`last_modified`
- `last_list_semantic_hash`
- `last_complete_source_id_set_hash`
- `last_complete_count`
- `consecutive_complete_misses`，实际按来源记录维护

检查点只在运行发布成功后前移。任务抓到一半失败时，不得提交新水位。

## 4. 增量信号及优先级

增量判断不能依赖单一字段，按下列顺序组合使用。

### 4.1 ETag 与 Last-Modified

已知 URL 再请求时发送：

```http
If-None-Match: <previous-etag>
If-Modified-Since: <previous-last-modified>
```

- 返回 304：记录一次成功观测，更新 `last_seen_at`，不生成新原始正文和实体版本。
- 返回 200：保存新原始快照并继续哈希比较。
- 服务器每次返回随机 ETag 或 Last-Modified 不可信时，关闭相应信号并保留诊断记录。
- HEAD 行为必须单独验证；未验证前直接使用条件 GET，不能假定 HEAD 与 GET 一致。

### 4.2 来源更新时间、游标和水位回退

若公开 API 支持 `updatedAt`、`publishTime`、增量游标或 `updated_after`：

1. 将来源时间解析为带时区时间，保留原始文本。
2. 查询起点使用 `last_watermark - overlap_window`，不能从精确水位开始。
3. 默认回退窗口 7 天；来源时间可靠且历史验证充分后可缩至 3 天。
4. 对重叠窗口中的记录按稳定 ID 和语义哈希幂等去重。
5. 仅在运行完整并通过质量门禁后推进水位。

水位回退用于处理迟到发布、置顶、回填、时区错误和历史记录修订。

### 4.3 Sitemap、RSS 与目录 lastmod

Sitemap/RSS 可用于发现候选 URL，但不能单独证明条目未变化或已删除。`lastmod` 的可信度需要按来源验证，并与详情哈希结合。

### 4.4 三类哈希

每个响应至少计算：

- `raw_sha256`：原始字节完全一致性，用于审计和附件完整性。
- `content_hash`：去除压缩、字符集和稳定传输差异后的正文哈希。
- `semantic_hash`：只基于规范业务字段计算，用于决定是否产生新实体版本。

HTML 语义哈希应排除：

- 广告、轮播顺序、访问量、随机推荐。
- CSRF token、会话 ID、构建哈希和时间戳。
- 追踪参数、无业务意义的 DOM class。
- 页面框架和版权年份等站点级噪声。

排除规则必须限定在具体站点和适配器版本中；不能用全局正则删除可能属于产品描述的内容。

规范业务字段在排序和序列化后计算 `semantic_hash`。数组的顺序是否有意义需按字段定义，例如行业标签可排序，套餐阶梯价格不能随意排序。

### 4.5 列表摘要变化

若列表含更新时间、状态、价格、标题等摘要：

- 新稳定 ID：抓取详情。
- 已知 ID 且摘要语义哈希变化：刷新详情。
- 已知 ID 且摘要未变：按 ETag/TTL 决定是否刷新详情。
- 列表只变化排序：不生成详情版本。

### 4.6 无可靠更新时间的来源

按以下组合运行：

- 每日扫描前若干页，发现新 ID。
- 每周扫描全部列表并对账 ID 集合。
- 已知详情按 TTL 分桶刷新。
- 对经常静默修改的平台缩短 TTL。

站点规模允许时，可以每日全量扫描列表；“全扫列表”不等于“每天重抓全部详情”。

## 5. 分页和停止条件

### 5.1 稳定分页

优先级：

1. API 游标分页。
2. API 页码 + 明确总数。
3. HTML 页码 + 末页标识。
4. 无限滚动或“加载更多”。

每页保存页码/游标、请求参数、返回 ID、去重后新增数和页面哈希。出现空页、重复游标、循环分页、总数突变时终止并标记 `PARTIAL`，不能把异常空页当作正常末页。

### 5.2 增量早停

只有同时满足下列条件，日增量才可提前停止向旧页翻页：

- 列表被验证为按目标时间单调倒序，且置顶规则已处理。
- 当前页最老业务时间早于回退后的水位。
- 连续 `K` 页没有新 ID或摘要变化，建议 `K=3`。
- 未发生总数、分页结构或排序模式漂移。

若任何条件不满足，继续扫描至已知末页，或者把运行降级为 `PARTIAL` 并等待每周校准。

## 6. 每日增量算法

伪代码描述如下：

```text
for each ACTIVE platform:
  acquire platform lease
  for each in-scope collection:
    load last committed checkpoint
    create immutable collection_run_manifest

    discover candidate list pages or API cursors
    for each list page:
      conditional fetch where supported
      persist raw response or 304 observation
      parse item summaries with adapter version
      validate pagination and schema

      for each source record summary:
        resolve stable source_record_key
        if new key:
          enqueue detail fetch
        else if summary changed or detail TTL expired:
          enqueue conditional detail fetch
        mark key observed in this run

      if verified early-stop conditions are met:
        stop incremental pagination

    fetch queued details with per-domain rate limit
    persist raw snapshots and parse field evidence
    normalize classifications and relationships
    compute semantic hashes
    stage new records and versions idempotently

    run quality gates
    if run qualifies as COMPLETE:
      apply absence counters and eligible tombstones
    else:
      do not change absence counters

    atomically publish staged current state
    advance checkpoint only after commit
  release platform lease
```

## 7. 新增、更新、恢复与下架状态机

来源记录状态：

```text
NEW -> ACTIVE -> MISSING_CANDIDATE -> REMOVED
                     |                  |
                     +---- ACTIVE <-----+
```

### 7.1 新增

稳定键第一次出现且详情达到最低质量门禁时写入 `NEW` 事件，随后作为 `ACTIVE` 当前记录发布。详情失败时保留发现事实和失败审计，但不得发布缺少核心身份字段的伪完整实体。

### 7.2 更新

只有 `semantic_hash` 改变才生成新版本：

- 关闭旧版本：`valid_to = new.valid_from`，`is_current = false`。
- 写新版本并保存 `diff_json`。
- 生成 `UPDATED` outbox 事件。

仅 raw hash 变化、语义字段不变时保存新原始快照和观测，不生成实体版本。

### 7.3 恢复

已标记 `REMOVED` 的稳定键再次出现时，写入新版本和 `RESTORED` 事件，不复写原下架历史。

### 7.4 连续三次完整扫描下架

一条来源记录只有在以下条件全部满足时才能变为 `REMOVED`：

1. 所属 collection 连续完成 3 次 `COMPLETE` 列表扫描。
2. 该稳定键在这 3 次完整扫描中都未出现。
3. 3 次扫描不是同一逻辑周期的重试副本。
4. collection 范围、筛选条件和适配器没有缩小。
5. 期间不存在分页异常、登录跳转、验证码、限流或 schema 漂移。

首次缺失：`MISSING_CANDIDATE`、计数 1；再次完整缺失计数 2；第三次完整缺失才写 `REMOVED`。任何一次重新出现立即清零。

稳定详情 URL 返回 404/410 可作为强信号，但仍需至少一次独立复核，防止短期路由故障。403、429、5xx、超时、空白 DOM 和验证码不是下架证据。

## 8. 原子发布与质量门禁

抓取、解析结果先写 staging。至少通过以下检查后才切换当前版本：

- 核心身份字段：平台、collection、对象类型、稳定键、名称、来源 URL。
- 来源稳定键在 collection 内唯一。
- 分页没有循环、缺口或未解释的总数差异。
- 当前条目数相对最近完整基线的下降未超过告警阈值，或已人工批准。
- 核心字段解析率未出现显著下降。
- 原始快照、版本和字段证据外键完整。
- 分类映射失败率、低置信率和跨源疑似重复在阈值内。
- 适配器和数据合同版本已记录。

建议默认阻断发布的异常：

- 完整扫描条目数较最近 4 次完整扫描中位数下降超过 20%。
- 名称或来源 URL 缺失超过 2%。
- 列表详情成功率低于 95%。
- 解析器返回零条但历史基线非零。
- 页面全部跳转到登录/验证码或非白名单域名。

阈值是初始建议，必须按平台历史基线调整。被阻断的数据仍保留在原始层和失败审计中。

## 9. Schema 和页面漂移

每个适配器保存固定回归样本及预期字段。每日运行比较：

- JSON Schema、关键 JSONPath 是否存在。
- DOM 锚点、列表项数和详情字段命中率。
- 分页参数、游标结构和总数字段。
- 响应 Content-Type、登录/验证码特征。

漂移处理：

1. 熔断对应 collection，不扩大抓取范围。
2. 运行标记 `PARTIAL` 或 `BLOCKED`。
3. 原始响应进入隔离区并告警。
4. 更新适配器并使用保存快照执行 `REPLAY`。
5. 回归测试通过后再恢复在线抓取。

## 10. 验收用例

实现至少覆盖以下自动化测试：

- ETag 返回 304 时不生成新版本，但更新最后观测时间。
- Last-Modified 不可信时自动降级到哈希，不漏掉真实变化。
- 水位回退能捕获晚到和回填记录。
- 列表置顶或重排不产生虚假更新。
- 页面访问量变化只影响 raw hash，不影响 semantic hash。
- 任务中途失败不推进检查点、不增加缺失计数。
- 同一运行重试不会重复写来源记录、版本和事件。
- 两次完整缺失仍为 `MISSING_CANDIDATE`，第三次独立完整缺失才下架。
- 403、429、5xx、超时和验证码不会触发下架。
- 被下架记录再次出现时产生 `RESTORED`，历史连续可查。
- 解析器升级可从原始快照重放，不访问来源站点。
- collection 规则缩小后不把范围外记录批量误判下架。
