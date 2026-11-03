# Google Play / Apple App Store 应用池监控

新增“上下架观察”、库外商店搜索入库、可悬停/键盘查看的监控图表和丰富的应用详情。指标来源、开源研究、点点/乐见参考及实现边界见 [观星监控研究与实现](docs/monitoring-design.md)。

现已增加**独立前端项目 `frontend/` + Python HTTP API + 重点池采集 worker**。前端展示应用池、关键词、榜单、ASO 核查、竞品、评论、素材变更和告警；Python 负责抓取、调度、MySQL 存储及数据接口。主题参考天枢，支持预设/自定义、深浅色和布局配置，矩形组件统一直角。详见 [控制台使用与边界](docs/console.md)、[前端项目](frontend/README.md)、[开发规范](docs/frontend-guidelines.md)。

国家与应用分类已改为分组搜索选择器，分类按两商店官方ID独立管理并兼容历史数据；全站离线嵌入阿里妈妈方圆体VF SemiBold。见 [国家、分类与字体说明](docs/store-catalog.md)。

顶部商店与语言已统一为下拉选择，商店显示 logo 与名称，侧栏采用统一线性图标。所有采集目标和基础流程可在 [统一采集中心](docs/collection-center.md) 管理。

应用详情支持当前市场的一键采集及榜单/关键词排名汇总；Apple 市场扩展为175项，榜单中心提供七种直接或明确标注的派生榜单。详见 [应用研究与榜单口径](docs/app-research.md)。

参考点点、七麦、AppTweak、Appark、AppFollow新增事件中心、ASO版本化草稿、评论处理工作台、竞品集合、开发者下钻和采集计划/质量中心，并扩展应用库高级筛选与批量操作。页面设计、证据来源与采集方案见 [五平台研究与设计](docs/platform-research-and-design.md)。

```powershell
# 后端 API：在仓库根目录运行
.\.venv\Scripts\python.exe -m pip install ".[test]"
.\.venv\Scripts\python.exe -m appmon web --port 8765
# 另一个终端启动重点池采集、关键词、榜单、评论、告警和导出
.\.venv\Scripts\python.exe -m appmon console daemon
# 前端：在 frontend 目录运行 npm ci，再运行 npm run dev
```

前端入口 `http://127.0.0.1:5173/`，API 健康检查 `http://127.0.0.1:8765/health`。首次启动会幂等新增控制台表，保留原应用池及历史。没有运行 worker 时仍可查看已有数据，但新建/刷新任务和导出会等待处理。

远程仓库：[hengshu-credit/appstore_applications_monitoring](https://github.com/hengshu-credit/appstore_applications_monitoring)（私有）。[查看远程 CI](https://github.com/hengshu-credit/appstore_applications_monitoring/actions/workflows/ci.yml)。

本地仓库：`E:\workspace\appstore_applications_monitoring`。源码、上游研究副本、压缩原始数据、数据库、历史快照均在本目录。

实现的是**全球已知应用 ID 池的全量导入和可持续扩充，以及可配置地区的增量监控**。公开接口和 AppGoblin 历史数据无法证明覆盖全球所有应用。默认详情地区为 `us`、Google 语言为 `en`，Apple 使用该地区接口返回的默认语言；这不是所有国家逐一验证。更改 `config.toml` 中的 `countries` 后重新 `enqueue` 即可扩展地区。

应用运行时只使用 **MySQL**。本机地址为 `127.0.0.1:3307`，库名 `appstore_applications_monitoring`，应用用户 `appmon`。凭据在 Git 忽略的 `.env` 中，通过 `MYSQL_HOST/PORT/DATABASE/USER/PASSWORD` 配置；程序不会回退到 SQLite。MySQL 数据文件绑定在本仓库 `data/mysql/`，与本机其他项目的 MySQL 实例隔离。

## 研究与数据

- [七个仓库的用途、复用决定和容量分析](docs/research.md)
- [上游代码版本记录](docs/upstream-manifest.json)
- [逐文件行数、时间范围和校验值](docs/data-manifest.json)
- [MySQL 迁移和验收记录](docs/mysql-verification.md)
- [首次基线的历史验收记录](docs/verification.md)
- 数据来源：[AppGoblin](https://appgoblin.info)，CC BY 4.0；**Data provided by AppGoblin. https://appgoblin.info**

公开主数据包共 **5,440,809 行**：Google 3,044,766，Apple 2,396,043。源记录更新日期范围是 **2025-04-08 至 2025-11-17**。文件名称中的 `live` 仅描述上游导出时的状态，导入后不会自动标记为当前在架。最新托管下载需在 [AppGoblin 免费数据页面](https://appgoblin.info/free-app-datasets) 登录免费账号取得；此项目可以导入合法取得的新版 TSV/XZ。

## 安装与运行

Python 3.11+、MySQL 8.4。本机已经建立独立 `.venv`，不再继承系统 Python 的其他项目依赖。

```powershell
cd E:\workspace\appstore_applications_monitoring
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
# 本机 .env 已配置随机密码；全新部署先复制 .env.example 并设置密码。
docker compose -f compose.yaml --env-file .env up -d mysql --wait
.\.venv\Scripts\python.exe -m appmon init
```

使用本次已经下载的数据（导入按文件 SHA256 去重，可以重复执行）：

```powershell
.\.venv\Scripts\python.exe -m appmon import data\raw\store_apps.tsv.xz --source appgoblin-public-baseline
.\.venv\Scripts\python.exe -m appmon import data\raw\live_store_apps.tsv.xz --source appgoblin-public-live-baseline
.\.venv\Scripts\python.exe -m appmon enqueue
.\.venv\Scripts\python.exe -m appmon status
```

从 GitHub 同步种子池时，`sync-seed` 查询最新目录树，校验下载大小、Git blob SHA1，并记录 SHA256；源文件没有变化时跳过重复导入；同一文件的导入使用 MySQL 命名锁，避免多个进程重复累计断点。它不保证上游每日发布新数据。

```powershell
.\.venv\Scripts\python.exe -m appmon sync-seed
.\.venv\Scripts\python.exe -m appmon enqueue
.\.venv\Scripts\python.exe -m appmon discover
.\.venv\Scripts\python.exe -m appmon run --limit 200
.\.venv\Scripts\python.exe -m appmon daemon
```

`daemon` 是持续进程：每日检查种子、按地区检查榜单/分类页/关键词/已验证开发者，循环处理到期任务；Ctrl+C 停止。关闭进程不会删除队列，未完成租约到期后可再次领取。**24 小时是成功任务的期望复查间隔，不代表百万应用每天都能查完**。请看研究报告中的吞吐计算。默认顺序 worker 适合本地建立基线、验证和运行小规模重点池；全池每日刷新需要有足够吞吐的授权批量源和分布式存储/调度。

本机网络需要代理时，可在启动该进程的 PowerShell 中设置 `HTTPS_PROXY` / `HTTP_PROXY`。不要把代理密码写入仓库。本次已验证的本地代理为 `http://127.0.0.1:7897`；此地址只适用于当前电脑，Docker 容器中的 localhost 不是宿主机。配置中的 TLS 1.2 上限用于本机代理的兼容问题，证书校验仍开启；其他网络可删除 `tls_maximum_version` 使用运行时默认 TLS。

要只执行一轮，使用 `run --limit N`。要先验证指定应用，可在大池入队前执行：

```powershell
.\.venv\Scripts\python.exe -m appmon add google com.whatsapp com.google.android.youtube
.\.venv\Scripts\python.exe -m appmon add apple 310633997 544007664
.\.venv\Scripts\python.exe -m appmon run --limit 4
```

已有任务不会被 `add` 重置；更新遵守现有到期时间。抓取失败或发现源失败返回非零退出码；错误保留在数据库中。

## 持续部署

```powershell
docker compose -f compose.yaml --env-file .env up -d --build
docker compose -f compose.yaml logs --tail 100 -f monitor
docker compose -f compose.yaml stop monitor
```

Compose 中的 MySQL 已实际启动并配置自动重启，持久化在 `data/mysql/`；监控 daemon 尚未启动。Windows 前台运行可用 `scripts/start.ps1`。任务通过 `FOR UPDATE SKIP LOCKED` 领取，限速记录通过行锁串行更新，同一 MySQL 数据库可以协调多个 worker。跨主机部署还需共享原始响应存储并配置网络访问；本机数据库端口仅绑定 127.0.0.1。

## 数据模型与查看

新增：[多国家、多语言关键词排名追踪](docs/keywords.md)。可批量维护词库、定期保存双商店有序搜索快照，并通过 `appmon keywords app` 反查应用在各关键词/地区的排名、变化与历史。见 [真实采集验收](docs/keyword-verification.md)。

| 位置/表 | 含义 |
|---|---|
| `upstream/` | 七个仓库的研究副本；数据仓库的大文件在 `data/raw/` |
| `data/raw/` | 三份原始 XZ 和校验清单，保留原始指标、日期、开发者等字段 |
| MySQL `appstore_applications_monitoring` | InnoDB、utf8mb4，所有时间戳采用 UTC epoch；数据文件在 `data/mysql/` |
| `apps` | `(store, app_id)` 唯一，全局 ID 池；名称、分类、首次/最近发现时间和首个来源 |
| `imports` | 文件 SHA256、处理进度、有效/拒绝/新增行数、完成状态；每 5000 行原子提交 |
| `jobs` | `(store, app_id, country, language)` 任务，租约 token、到期时间、重试次数 |
| `listings` | 每地区最近一次有效观察、可用状态和最后成功时间；网络错误不覆盖它 |
| `snapshots` | 内容变化时保存归一化详情版本，包括名称/版本/开发者/评分/价格/描述等 |
| `events` | 首次验证、字段变更、地区不可用、重新出现；变更包含旧值/新值 |
| `requests` | 每次详情尝试的结果；失败单独统计，不能误报为下架 |
| `discovery_runs` | 每个发现源结果、发现数、新增数、失败信息 |
| `data/responses/` | 内容寻址的 gzip 原始响应，便于重放与审计 |
| `migration_progress` | 一次性迁移的内容指纹、分表断点及校验状态 |

新增 ID 可通过 `apps.first_seen` 查询；`first_verified` 仅说明本系统首次验证，并不代表应用刚发布。缺失至少三次、每次间隔至少 24 小时才标记 `region_unavailable`；所有这些结论限定在查询地区，不能推断全球下架。403/429/5xx、JSON 异常、解析器错误和 TLS/超时只触发退避，保留历史有效状态。

```powershell
.\.venv\Scripts\python.exe -m appmon status --output data\status.json
.\.venv\Scripts\python.exe -m appmon export events data\exports\events.jsonl
.\.venv\Scripts\python.exe -m appmon export apps data\exports\apps.jsonl
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

原始响应和请求日志默认保留，长期监控需规划磁盘和归档。数据库导出采用流式 JSONL；百万行导出需预留空间。MySQL 备份应使用 `mysqldump --single-transaction` 或数据库一致性备份工具；不要直接复制运行中的 `data/mysql/` 目录。

## 旧库迁移

本机现有数据已迁入 MySQL。2026-11-02 已按用户要求删除旧 SQLite 备份 `data/legacy/monitor.sqlite3.bak`，不再保留该本地回退文件。全表核对结果见 `docs/mysql-migration.json`。

```powershell
# 示例：需要自行提供旧库文件；本机旧备份已删除。仅用于空目标库的首次迁移或中断续传。
.\.venv\Scripts\python.exe -m appmon migrate-sqlite path\to\legacy.sqlite3 --report data\mysql-migration.json
```

迁移工具只读打开 SQLite，按主键批量复制，数据与断点在同一个 MySQL 事务提交；源文件 SHA256 不同会拒绝续传，已有业务数据的非迁移目标会拒绝覆盖。全部表每个字段的有序 SHA256 和精确行数相符后，才解除运行时迁移锁。`sqlite3` 只存在于这个一次性兼容导入工具中。

本地测试会使用 `.env` 的 `MYSQL_ROOT_PASSWORD` 创建独立随机测试库，结束时删除该测试库，不清理业务库；测试需要真实 MySQL，不会静默跳过或使用 SQLite 模拟。

## Git 与 CI

代码、研究文档、数据校验清单纳入版本管理；大数据、数据库、上游副本、环境和临时文件通过 `.gitignore` 排除，物理位置仍在此仓库下。`.github/workflows/ci.yml` 配置 Linux/Windows × Python 3.11/3.12/3.13 的安装、编译、真实 MySQL 8.4 集成测试及 CLI 检查。

远程仓库为 `https://github.com/hengshu-credit/appstore_applications_monitoring`，默认分支 `master`。后续代码直接在 `master` 上修改和提交，不自行创建功能分支或开发 worktree，详见 [AGENTS.md](AGENTS.md)。每次提交交付必须核对最终 SHA 的全部六项矩阵和任何其他触发的必需检查；以 [GitHub Actions](https://github.com/hengshu-credit/appstore_applications_monitoring/actions/workflows/ci.yml) 中该 SHA 的结果为准。本地测试成功或推送成功不能代替远程 CI 通过。
