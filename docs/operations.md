# Windows 调度、运行、监控与故障审计

## 1. 文档状态

本文定义生产运维合同和 Windows 调度目标，不表示项目当前已经存在相应 CLI、任务计划、监控服务或 38 个已验证适配器。

后续实现必须提供统一、非交互式入口。本文暂以如下目标 CLI 表达：

```text
python -m app.cli crawl --mode incremental
python -m app.cli crawl --mode reconcile
python -m app.cli crawl --mode baseline --platform <platform_id>
python -m app.cli replay --run-id <run_id>
python -m app.cli retry --run-id <run_id> --failed-only
python -m app.cli status [--platform <platform_id>]
```

模块名 `app.cli` 是待实现接口合同。创建 Windows 计划任务前，必须在目标环境中验证真实命令、退出码、日志路径和数据库连接；不能直接复制模板后声称调度已生效。

## 2. 生产调度

所有时间使用 `Asia/Shanghai`，Windows 主机时区设为“中国标准时间”。数据库时间统一保存 UTC，同时保存调度本地时间和时区。

### 2.1 每日增量

- 任务名称：`DataProbe-Daily-Incremental`
- 触发时间：每天 02:30。
- 模式：`DAILY_INCREMENTAL`。
- 行为：扫描所有 `ACTIVE` 平台的在范围 collection，使用条件请求、水位回退和详情 TTL。
- 平台启动增加确定性抖动，建议 0 至 30 分钟，避免同时冲击全部官网。
- 同一平台最多一个活动租约；同一计划任务不允许并发实例。

### 2.2 每周校准

- 任务名称：`DataProbe-Weekly-Reconcile`
- 建议触发时间：每周日 04:30。
- 模式：`WEEKLY_RECONCILE`。
- 行为：遍历全部公开列表分页、对账来源 ID 集合、更新 collection 覆盖账本，并累积完整缺失计数。
- 每周校准不是无条件重下载全部详情；已知详情继续使用 ETag、Last-Modified 或刷新 TTL。
- 只有校准运行达到 `COMPLETE` 才能参与连续三次完整扫描下架判断。

### 2.3 可选周期任务

- 每月详情轮换刷新：发现无更新时间的静默修改。
- 每季度来源复核：官方域名、栏目、robots、条款和访问边界。
- 每日数据库备份、每周恢复抽检。
- 每日失败汇总和数据质量报告。

## 3. Windows 任务计划模板

建议使用专用低权限服务账号运行，账号只拥有项目目录读取/必要日志写入、数据库连接和对象存储访问权限。不得使用个人桌面会话中的浏览器 Cookie。

在 CLI 完成并通过手工验收后，可参考以下 PowerShell 模板注册任务。路径、模块名和账号必须按部署环境替换：

```powershell
$ProjectRoot = 'D:\ai大模型\ai场景探针'
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

$DailyAction = New-ScheduledTaskAction `
  -Execute $Python `
  -Argument '-m app.cli crawl --mode incremental' `
  -WorkingDirectory $ProjectRoot
$DailyTrigger = New-ScheduledTaskTrigger -Daily -At '02:30'
$DailySettings = New-ScheduledTaskSettingsSet `
  -StartWhenAvailable `
  -MultipleInstances IgnoreNew `
  -RestartCount 3 `
  -RestartInterval (New-TimeSpan -Minutes 20) `
  -ExecutionTimeLimit (New-TimeSpan -Hours 8)

$WeeklyAction = New-ScheduledTaskAction `
  -Execute $Python `
  -Argument '-m app.cli crawl --mode reconcile' `
  -WorkingDirectory $ProjectRoot
$WeeklyTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At '04:30'
$WeeklySettings = New-ScheduledTaskSettingsSet `
  -StartWhenAvailable `
  -MultipleInstances IgnoreNew `
  -RestartCount 2 `
  -RestartInterval (New-TimeSpan -Minutes 30) `
  -ExecutionTimeLimit (New-TimeSpan -Hours 16)
```

注册动作必须由部署人员显式执行；本文不包含密码，也不建议把任务账号密码写入脚本、仓库或 `.env`。生产凭据应来自 Windows Credential Manager、企业密钥服务或受控环境变量。

任务创建后至少验证：

1. 计划任务显示下一次运行时间正确。
2. “手动运行”能生成唯一 `crawl_run`。
3. 进程退出后退出码与数据库状态一致。
4. 机器重启后 `StartWhenAvailable` 能补跑错过的触发。
5. 重复启动不会创建第二个活动实例。
6. 任务账号能启动 Playwright 浏览器，但访问不到个人配置和凭据。

## 4. 运行状态与退出码合同

顶层运行状态：

- `QUEUED`
- `RUNNING`
- `SUCCEEDED`
- `PARTIAL`
- `FAILED`
- `BLOCKED_QUALITY`
- `CANCELLED`

collection 状态：

- `COMPLETE`
- `PARTIAL`
- `FAILED`
- `BLOCKED_COMPLIANCE`
- `BLOCKED_ACCESS`
- `OUT_OF_SCOPE`

目标 CLI 退出码：

- `0`：全部计划 collection 成功发布。
- `2`：存在 `PARTIAL`，但没有进程级故障。
- `3`：质量门禁阻断发布。
- `4`：合规或访问边界阻断。
- `10`：配置或适配器错误。
- `11`：数据库/对象存储不可用。
- `12`：运行租约冲突，未启动第二实例。
- `20`：未处理异常。

Windows Task Scheduler 结果和数据库 `crawl_run.status` 必须一致。任务进程异常退出后由启动器在下一次启动时将超时租约标记为 `ABANDONED`，但不得自动把对应 collection 视为完整。

## 5. 失败审计

每个失败必须保留结构化记录，至少包含：

- `run_id`、`task_id`、`platform_id`、`collection_id`。
- `adapter_name`、`adapter_version`、`fetch_mode`。
- 请求 URL 的规范化形式；敏感参数应脱敏。
- 失败阶段：`DISCOVER`、`FETCH_LIST`、`FETCH_DETAIL`、`PARSE`、`NORMALIZE`、`QUALITY_GATE`、`PUBLISH`。
- `error_code`、异常类型、可读摘要和重试次数。
- HTTP 状态、Content-Type、重定向链和响应耗时。
- 关联 `snapshot_id`；响应含敏感内容时进入隔离区。
- `first_failed_at`、`last_failed_at`、`next_retry_at`。
- 是否可重试、最终处置、处理人和工单链接。

不得在日志中写入 Cookie、Authorization、数据库密码、对象存储密钥、完整个人联系方式或带签名下载 URL。

### 5.1 错误分类

- `SOURCE_UNAVAILABLE`：DNS、TLS、连接、超时、5xx。
- `RATE_LIMITED`：429 或明确限流提示。
- `ACCESS_RESTRICTED`：403、登录、验证码、风控页。
- `ROBOTS_OR_TERMS_BLOCKED`：合规策略阻断。
- `SCHEMA_DRIFT`：JSON/DOM 合同变化。
- `PAGINATION_LOOP`：重复游标或循环页。
- `PARSE_EMPTY`：历史非零但解析为零。
- `DATA_QUALITY`：数量、字段或关联门禁失败。
- `SECURITY_BLOCK`：SSRF、恶意文件、非白名单域名或超限下载。
- `INTERNAL`：数据库、队列、代码和资源故障。

### 5.2 重试原则

- 超时、连接重置和 5xx：指数退避加抖动，单次运行默认最多 3 次。
- 429：尊重 `Retry-After`，同时降低该域名令牌桶速率。
- 403、验证码、登录和 robots/条款阻断：不自动换 IP或伪造身份重试。
- schema 漂移：立即熔断 collection，保存样本，等待适配器修复。
- 质量门禁失败：不发布；先排查来源变化和适配器，再人工批准重放。

同一错误连续发生时进入熔断，避免每天重复冲击来源。熔断期间保留健康探测，但健康探测同样受限速和合规边界约束。

## 6. 质量门禁

每个 collection 在 staging 到 current 发布前运行：

### 6.1 完整性

- 分页已闭合，无重复游标和未解释空页。
- 若来源显示总数，去重后稳定 ID 数与总数一致，或差异已记录。
- 列表发现数、详情成功数、受限数和失败数可以对账。
- 当前数量相对最近完整基线的异常下降已阻断或审批。

### 6.2 准确性

- 平台、collection、对象类型、稳定键、名称、来源 URL 不为空。
- 来源发布时间早于或等于合理抓取时间，异常日期进入隔离。
- 地域、行业和产品类型代码属于当前分类版本。
- 原始标签与规范值同时保存。
- 字段证据能回溯到对应快照。

### 6.3 一致性

- 同源稳定键唯一。
- 当前版本每条记录最多一条。
- 版本有效期不重叠。
- `source_record`、`entity_version`、`field_evidence` 外键完整。
- 下架仅来自连续三次独立 `COMPLETE` 扫描缺失。

### 6.4 推荐初始阻断阈值

- 条目数较最近 4 次完整运行中位数下降超过 20%。
- 核心名称或来源 URL 缺失率超过 2%。
- 列表到详情成功率低于 95%。
- 历史非零 collection 本次解析为零。
- 页面全部变为登录、验证码、错误页或非白名单跳转。

平台稳定运行后应使用各自历史基线调整阈值，不应永久套用统一值。

## 7. 指标、日志和告警

### 7.1 指标

按平台、collection、适配器版本和抓取模式拆分：

- `crawl_run_total`、运行耗时和状态。
- 请求数、成功率、304 比例、重试率。
- 2xx/3xx/4xx/5xx、超时、TLS、验证码和登录页计数。
- 每域名请求速率、并发、429 和熔断状态。
- 列表页数、来源 ID 数、详情成功数。
- 新增、更新、不变、候选缺失、下架和恢复数。
- 原始快照量、对象存储字节数。
- 核心字段完整率、分类映射率、低置信率、疑似重复率。
- collection 距最近完整运行的时间。
- Playwright 占比、浏览器启动失败和渲染耗时。
- REST/MCP 请求量、P95/P99 延迟、错误率和拒绝率。

### 7.2 日志

使用 JSON 结构化日志，并始终包含 `run_id`、`task_id`、`platform_id`、`collection_id` 和 `request_id`。日志正文不得直接输出大段 HTML/JSON；通过 `snapshot_id` 定位原始内容。

### 7.3 告警

建议告警条件：

- 每日任务未在 02:45 前启动，或超过 8 小时未结束。
- 平台连续 2 次增量失败或 7 天无完整扫描。
- 数量下降、解析空结果或核心字段命中率骤降。
- 429/403/验证码比例持续上升。
- Playwright 占比异常上升，可能说明公开 API 失效。
- 原始对象、数据库、证据链写入失败。
- Agent 数据新鲜度超过 26 小时。
- PII、SSRF、恶意文件或提示注入检测命中。

告警必须指向可检索的 `run_id` 和失败审计，而不是只发送一段异常栈。

## 8. 平台接入运行手册

每接入一个平台按顺序执行：

1. 从 38 条种子记录中选择 `PENDING_AUDIT` 来源。
2. 核验主体、官网、跳转域名和平台角色。
3. 完成合规审查并建立域名白名单。
4. 盘点产品、组件、场景等 collection，记录受限栏目。
5. 选择 `STATIC`、`PUBLIC_API`、`PLAYWRIGHT` 或 `FILE_INDEX`。
6. 固定列表、详情、空结果、分页末页和错误页回归样本。
7. 实现适配器和解析规则，验证稳定键。
8. 执行 `BASELINE_FULL`，完成总数/ID/详情对账。
9. 通过字段准确性、重复和安全门禁。
10. 人工批准后将平台切换为 `ACTIVE`，再进入每日调度。

若其中任何一步未完成，文档和界面只能显示“待核验”“部分覆盖”或“受限”，不能显示“已全量接入”。

## 9. 日常处置手册

### 9.1 来源暂时不可用

检查 DNS、TLS、HTTP 状态和历史可用性；按策略重试。连续失败则熔断并通知，不修改当前数据、不累积缺失计数。

### 9.2 页面结构变化

保存异常快照，停止对应 collection 发布；在保存样本上修复解析器并执行 replay。通过回归测试后再恢复在线抓取。

### 9.3 数量骤降

比较来源总数、分页数、筛选条件、登录/验证码特征和适配器版本。除非完整性和人工复核均通过，否则保持旧 current 数据。

### 9.4 重复激增

检查来源稳定 ID、URL 规范化、分页重排和提供方名称归一规则。跨源疑似重复先建立候选关系，不批量自动合并。

### 9.5 Agent 返回可疑指令

立即冻结对应快照进入 Agent 检索层的权限，保留原始证据，检查提示注入检测、字段清洗和工具调用日志。不得删除审计快照来掩盖问题。

## 10. 备份与恢复

- PostgreSQL：每日备份，保留 WAL/等价增量恢复能力。
- MinIO/S3：启用版本或不可变保留策略，确保快照 URI 与对象一致。
- 适配器配置和数据库迁移：纳入版本控制。
- 任务计划导出：每次变更后保存 XML/等价配置到受控部署制品，不包含凭据。
- 至少每季度执行一次从备份恢复到隔离环境的演练。

建议目标：结构化数据库 RPO 不超过 24 小时，RTO 不超过 4 小时；实际指标需要在部署容量和预算评审后确认。

## 11. 运维验收标准

- Windows 每日 02:30 增量任务和每周日 04:30 校准任务经过手工触发与自然触发验证。
- 主机重启、错过触发、重复触发和超时均有验证记录。
- 任一运行可以通过 `run_id` 追溯到请求、快照、解析、质量门禁和发布。
- `PARTIAL`/`FAILED` 运行不会推进检查点或产生下架。
- 429、验证码、登录页和 schema 漂移不会触发绕过逻辑。
- 日志不包含凭据或未脱敏 PII。
- 告警可定位平台和 collection，并包含处置入口。
- 备份恢复演练能重建当前实体、历史版本、字段证据和原始对象引用。
