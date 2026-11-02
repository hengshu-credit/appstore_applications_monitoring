# AppGoblin 全组织研究及双应用商店监控方案

核查日期：2026-11-02（Asia/Shanghai）。通过 GitHub 组织 API 分页枚举，核查到 **7 个公开仓库**；无法推断私有仓库。以当前源码、README 和许可证为依据；网页搜索缓存可能比真实源码旧。代码树版本见 `upstream-manifest.json`，源数据见 `data-manifest.json`。

## 七个仓库分别做什么

| 仓库 | 实际用途和技术 | 本项目采用方式 |
|---|---|---|
| [adscrawler](https://github.com/appgoblin-dev/adscrawler) | Python 采集大仓库。包括双商店应用详情、分类榜单、关键词、开发者扩展、第三方商店、app-ads.txt、APK/IPA 下载、SDK 分析和设备流量分析。PostgreSQL、S3、部分 Node.js 脚本；完整能力还涉及 apktool、ipatool、Waydroid。 | 参考“种子池 → 榜单/搜索/开发者补充 → 周期更新 → 历史库”的结构。当前目标只需应用池，因此没有直接部署其整套重型分析系统。MIT。 |
| [appgoblin](https://github.com/appgoblin-dev/appgoblin) | AppGoblin 网站：Litestar Python 后端，SvelteKit/Svelte/Tailwind 前端，展示应用、SDK、广告网络、关键词、排行和趋势；提供 API/MCP，API 使用密钥及配额。 | 用于理解展示和数据模型；网站不是应用池的采集引擎。当前检查未见顶层明确开源许可证，研究副本单独保存，没有将其代码合并进本项目。 |
| [appgoblin-data](https://github.com/appgoblin-dev/appgoblin-data) | 免费应用数据导出和导出脚本。公开目录有全量已知 ID、当时 live 应用及有限指标三份压缩 TSV。更大、更新的数据移到官网。 | 使用公开 ID 包初始化应用池，保留全部三个压缩文件及精确行数、SHA256、Git blob 校验。实际 LICENSE 是 CC BY 4.0，不能因为 GitHub 显示 Other 就忽略署名要求。 |
| [appgoblin-play-scraper](https://github.com/appgoblin-dev/appgoblin-play-scraper) | JoMingyu/google-play-scraper 的独立分支，Python 解析 Google Play 应用详情、搜索、评论、权限等。实际 LICENSE 包含 AppGoblin 与原作者的 MIT 声明。 | 将固定源码树的 Python 包放入 `src/appgoblin_play_scraper/`，保持原代码不变和完整版权声明。使用 `parse_dom`，HTTP、限流、错误判别由本项目控制。原包声明 Python ≥3.12；本项目使用到的解析路径在本机 Python 3.11 实测通过，其他 Python 版本由待运行 CI 检查。 |
| [appgoblin-itunes-scraper](https://github.com/appgoblin-dev/appgoblin-itunes-scraper) | Digital Methods Initiative 的 iTunes scraper 分支。封装详情/批量 lookup、搜索、开发者、榜单、类似应用；部分方法使用旧端点。MIT。 | 参考接口能力；本实现直接调用 Apple lookup、search 和现行 marketingtools RSS，减少旧端点依赖。源码保留供后续扩展和对照。 |
| [appgoblin-android](https://github.com/appgoblin-dev/appgoblin-android) | Kotlin Android 客户端，查看用户手机已安装应用对应的 SDK、广告追踪器与公司信息；依赖 AppGoblin 数据/API。 | 不是全球应用发现器，无需部署。未见明确顶层许可证，不并入产品源码。 |
| [.github](https://github.com/appgoblin-dev/.github) | 组织介绍和 profile README。 | 组织元信息，无采集运行能力。 |

不要简单地同时启动七个仓库：它们是数据生产、公共快照、解析库、网站和终端客户端等不同层次。尤其 `adscrawler` 当前源码依然引用旧 `google_play_scraper` 等路径，不能假设它已完整切换到另外两个新分支；其 README 的 Python 版本说明也存在混杂，直接部署需要额外适配。

## 全量的准确含义

1. **全量导入已知 ID**：可以做到，逐行读取所有输入，按 `(store, app_id)` 去重、校验、断点恢复；本次公开主包 5,440,809 行。
2. **持续发现之前未知的应用**：可以通过新数据包、国家榜单、分类页、关键词、开发者列表扩大集合，但这些有限入口没有完整性证明。Google 当前发现实现提取公开页面可见详情链接，不承诺完整榜单、搜索全结果或每个开发者全部分页。
3. **所有国家的每个应用每日实时检查**：公开接口、当前单机吞吐和数据来源无法保证。配置多个国家会按地区增加任务，默认只查 US；Google Play 的 `gl` 是请求上下文，不等同于在该国真实设备/账号上验证可安装。
4. **当前已上架总量**：历史快照无法证明。2026 年下载的 GitHub 文件中，源记录最晚更新仍为 2025-11-17，不能把下载时间当成采集时间。

[Apple Search API 官方文档](https://performance-partners.apple.com/search-api) 明确搜索结果上限为 200、约 20 次调用/分钟且可能变化，并建议高量使用方评估 [Enterprise Partner Feed](https://performance-partners.apple.com/epf)。EPF 需要符合条件并取得访问权限，应核实当前可获得的应用目录、地区和授权范围，不能视为任意匿名用户可直接下载。

[Google Play Developer API](https://developers.google.com/android-publisher) 用于开发者发布和管理应用；当前官方 API 说明未提供匿名列举全球全部应用的接口。这个结论来自 API 能力范围核查，不表示公开网页上所有应用都能被枚举。因此严格“全球全量”目标需要增加授权全量目录/商业数据源，并建立跨源覆盖核验。

## 数据落地和容量

| 文件 | 行数 | 大小（字节） | 用途 |
|---|---:|---:|---|
| store_apps.tsv.xz | 5,440,809 | 100,484,632 | 所有已知历史 ID |
| live_store_apps.tsv.xz | 3,102,170 | 90,214,084 | 上游导出时 live 应用名称、类别、开发者及其他字段 |
| store_apps_metrics.tsv.xz | 3,102,170 | 32,305,060 | 同一 live 集合的有限安装量/评分数 |

三份文件均已解压流式遍历并核对 Git blob SHA1。源字段完整保存在压缩文件中；ID、名称和类别汇入主池，原始指标文件不会被误标记为本机实时采集的快照。没有解压为巨大 TSV 副本。所有文件在 `data/raw/`，业务库为 MySQL `appstore_applications_monitoring`，持久化在 `data/mysql/`。

以当前默认间隔估算：Google 每 2.5 秒一请求，理论上每天最多约 34,560 次；Apple 每 3.5 秒一请求，理论上每天约 24,686 次。这还没有计算网络耗时、发现请求和重试。Google 3,044,766 个 ID 单轮至少约 88 天，Apple 2,396,043 个 ID 至少约 97 天；当前单 worker 顺序处理两类请求，合计下界约 185 天。并发不能消除服务方限流，更不能通过增加 worker 就承诺每日全量。

所以当前软件的交付定位是：**真实全量基线入库、可扩展发现链路、可靠的持续监控原型和审计数据**。生产级“百万应用 × 多地区 × 每日”需优先解决批量数据供应；之后扩展当前 MySQL 任务队列/分片、对象存储、独立调度和监控。扩大抓取频率应遵守数据提供方的配额和使用范围。

## 已落实的可靠性机制

- 数据源版本和校验清单；按文件内容哈希去重导入。
- 流式导入，5000 行一事务，进度与数据一起提交；磁盘不足停止，继续运行从最后提交行恢复。
- 国家/语言分离，避免把某个地区看不到误认作全球下架。
- 队列原子领取、到期租约、token 拒绝过期 worker 的结果；不同进程共享商店限速记录。
- HTTP 403/429/5xx、TLS/超时/解析异常进入重试，尊重 Retry-After；不改写已知在架状态。
- 三次独立缺失、至少隔 24 小时确认后，才产生地区不可用事件；重新出现产生恢复事件。
- 内容变化生成历史快照和逐字段 diff；首次验证明确不等于新上架。
- 状态、尝试结果、失败次数、发现来源都有查询/JSONL 导出入口。不会把失败任务数量藏在成功总量里。

后续可增加的能力包括：有权使用的新全量数据源、更多官方地区、关键词词典/分页、完整开发者遍历、分布式批量导入、可视化前端和外部告警。本次不包含 APK 下载、反编译、SDK 分析、邮件/消息通知或付费 API 账号。
