# 全国数据交易所爬虫运维平台

面向全国数据交易所公开目录的采集、增量版本管理、覆盖审计和运维管理平台。系统已内置截图中的 38 个来源，支持数据产品、组件、场景、需求和数商等公开内容，并保存抓取时间、原始证据、解析置信度和历史版本。

## 已实现能力

- 38 个来源建档，缺网址、离线、受限和参考来源均有明确状态。
- HTTP、Sitemap、公开 JSON API、SPA 浏览器渲染和通用分页采集。
- `ETag`、`Last-Modified`、内容哈希、语义哈希和附件 SHA-256 增量判断。
- 相同语义不重复创建版本；只有连续三次完整扫描缺失才下架。
- PostgreSQL 结构化历史、MinIO 原始快照、一年在线留存及归档任务。
- Celery + Redis 任务队列、数据库动态计划、取消、重跑和 SSE 实时日志。
- 管理员/只读权限、平台和栏目配置、低置信审核、站内告警和审计日志。
- React 运维后台：总览、平台、任务、目录、覆盖率、审核、用户与审计。
- REST/OpenAPI 查询合同，可在后续直接封装 MCP Agent tools。
- 低置信分类进入人工审核；可选 OpenAI-compatible LLM 只改进待审建议，不会自动发布分类。

## Docker 部署

目标环境为 Linux + Docker Compose。管理页面仅绑定本机 `127.0.0.1:8080`。

```bash
cp .env.docker.example .env
# 编辑 .env，替换全部数据库、MinIO、认证密钥和初始管理员密码
docker compose up -d --build
docker compose ps
```

打开 `http://127.0.0.1:8080`。首次登录使用 `.env` 中的 `DMP_BOOTSTRAP_ADMIN_USERNAME` 和 `DMP_BOOTSTRAP_ADMIN_PASSWORD`，登录后应立即修改初始化密码。

如需模型辅助分类，在 `.env` 设置 `DMP_LLM_ENABLED=true`、`DMP_LLM_BASE_URL`、`DMP_LLM_API_KEY` 和 `DMP_LLM_MODEL`。密钥不要写入仓库；模型结果仍需管理员在“数据审核”中确认。

服务组成：

| 服务 | 作用 |
| --- | --- |
| `frontend` | React 静态站点与 Nginx API 反向代理 |
| `api` | FastAPI 管理和查询 API |
| `worker` | Celery 采集任务执行器，内含 Chromium |
| `scheduler` | 数据库动态计划与原始快照归档 |
| `postgres` | 结构化实体、版本、运行、用户和审计 |
| `redis` | Celery 队列、任务状态和锁基础设施 |
| `minio` | HTML、JSON、PDF 等压缩原始证据 |

API 文档：`http://127.0.0.1:8080/api/docs`。

## 本地开发

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev,browser]"
.\.venv\Scripts\python -m playwright install chromium
.\.venv\Scripts\dmp bootstrap
.\.venv\Scripts\dmp serve
```

另一个终端启动前端：

```powershell
cd frontend
pnpm install
pnpm dev
```

默认本地存储使用 SQLite 和 `data/raw` 文件目录；生产必须使用 Compose 中的 PostgreSQL、Redis 和 MinIO。

## 常用命令

```bash
dmp bootstrap                    # 初始化表、38 个来源、计划、映射和管理员
dmp crawl --incremental          # 立即增量采集全部启用来源
dmp crawl --full                 # 完整校准
dmp crawl -p 8 --max-pages 200   # 只采湖北样板来源
dmp status                       # 查看数据库和最近运行
dmp archive --limit 500          # 归档过期原始快照
dmp schedule                     # 启动数据库计划调度服务
```

默认计划为每日 `02:30 Asia/Shanghai` 增量采集、周日 `04:30` 完整校准，前端可修改 Cron、时区、模式和平台范围。

## 增量和完整性规则

1. 列表和公开 API 每日检查，更新时间水位回退三天。
2. 新来源 ID 立即抓详情；已知 ID 使用 HTTP 条件请求和内容指纹。
3. 原始响应哈希变化但规范字段不变时，不创建业务版本。
4. 每个关键字段保存来源快照、定位路径、方法和置信度。
5. 未得到预期总数、分页未闭合、遇到未处理错误或达到页数上限时，栏目只能是 `partial`。
6. 失败或部分运行不得触发下架；连续三次 `complete` 完整扫描缺失才下架。

## 来源接入

平台主数据在 [`config/platforms.csv`](config/platforms.csv)，站点专用公共栏目在 [`config/site_rules.json`](config/site_rules.json)。当前湖北平台已配置产品、组件、场景、需求和数商公开入口；其他平台先由通用发现适配器采集，完成官网、栏目、分页、总数和合规核验后再把专用规则加入配置。

“全部”严格定义为官网无需绕过登录、验证码、付费或访问控制即可获得的公开目录元数据和获准附件。无法公开采集的来源以 `blocked`、`offline` 或 `out_of_scope` 状态进入覆盖矩阵，不能伪装为零数据的成功采集。

## 测试与运维

```bash
python -m pytest -q
cd frontend && pnpm run build
alembic upgrade head
```

备份和恢复：

```bash
./scripts/backup.sh
./scripts/restore.sh ./backups/20260711-023000
```

结构化历史永久保留。原始对象在线保留 365 天，归档任务校验 SHA-256 后移动到 `/archive`；归档失败时保留原对象并生成站内告警。

## 安全边界

- 默认遵守 `robots.txt`，使用白名单域族、公共 IP 校验和请求限速。
- 不绕过验证码、WAF、登录或接口签名，不下载真实付费数据载荷。
- 页面正文始终视为不可信内容，不能转化为系统指令或可执行操作。
- 快照下载固定为附件并设置 `nosniff`；敏感配置只通过环境变量注入。
- 对外部署前必须启用 HTTPS、替换所有默认密钥并限制管理入口网络范围。
