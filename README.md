# 衡枢·观星

Google Play / Apple App Store 应用、关键词、榜单及地区可用性监控平台。

## 存储架构

运行时仅使用 **OceanBase + SeaweedFS**。OceanBase 承担账户权限、应用目录、任务租约和历史分析；SeaweedFS 保存完整正文、原始响应、图片、视频及导出文件。SeaweedFS 的目录元数据也存入 OceanBase 的独立 `seaweedfs` 数据库。

- OceanBase CE 4.4.2.1，使用 MySQL 协议客户端；这不需要 MySQL 服务。
- 任务队列按候选键分页并逐条加锁，避免 OceanBase 的 `LIMIT … SKIP LOCKED` 提前返回空结果。
- 关键词排名、榜单明细、每日指标、事件、请求和采集日志使用列式表；任务和配置使用行式表。所有表采用 Zstd 压缩。
- 正文按 SHA256 寻址、无损 Zstd 压缩。图片和视频保留原始字节；同内容共享对象。
- 对象先上传成功，才提交数据库引用。事务回滚可能留下孤立对象，但不会提交悬空引用。当前不自动删除孤立对象。
- 本版本是新的存储格式，不接受旧版备份，不提供 MySQL/本地素材回退。历史数据目录不自动删除。需要恢复旧数据时，使用[一次性历史导入](docs/mysql-history-import.md)，先复制到独立库并完成验证。

## 单机启动

需要 Linux Docker 引擎；Windows 开发通过 Docker Desktop 的 Linux 引擎运行。OceanBase 必须使用命名卷，不要绑定 Windows 文件夹。请为 OceanBase 预留至少当前配置的 6 GiB 内存以及数据/日志空间；此单机拓扑不提供主机级高可用。

```powershell
python scripts/configure_storage.py
docker compose -f compose.yaml --env-file .env --profile console --profile keywords up -d --build
```

初始化脚本只生成本机 `.env` 和 `data/runtime/` 私有配置，不输出密钥。Compose 按 OceanBase → 元数据库 → 已认证的 SeaweedFS → 应用表/对象桶顺序启动。首次启动可能需要数分钟。

前端默认 `http://127.0.0.1:5173`，API `http://127.0.0.1:8765/health`。新库通过前端初始化管理员。默认端口：OceanBase 2881、SeaweedFS S3 8333；均只绑定回环地址。

OceanBase 数据文件默认预分配 15 GiB，可用 `OCEANBASE_DATAFILE_SIZE` 调整。主机磁盘有余量不代表数据库数据文件有余量：旧的 5 GiB 数据卷可能在大词库聚合时返回错误 4184，同时阻塞统计和采集。已有卷不会随 Compose 环境变量自动扩容；确认底层磁盘空间后，以 `root@sys` 在 OceanBase 执行 `ALTER SYSTEM SET datafile_size='15G'`，并在 `.env` 同步 `OCEANBASE_DATAFILE_SIZE=15G`。扩容保留现有数据，不需要删除或重建卷。

线上对象下载需将 `S3_PUBLIC_ENDPOINT` 设置为浏览器可访问的 HTTPS 地址，并由代理转发 S3 请求。修改外部域名后必须重新生成签名，不要修改已签名 URL 的主机或路径。桶必须从创建时启用认证；添加服务器密钥不能自动纠正既有匿名桶的权限。

## 开发与测试

```powershell
.\.venv\Scripts\python.exe -m pip install ".[test]"
python scripts/configure_storage.py
docker compose -f compose.dev.yaml --env-file .env --profile console --profile keywords up -d --build
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试使用真实 OceanBase 和 SeaweedFS，在随机测试数据库及独立对象前缀中运行，不使用 MySQL 或 SQLite 冒充运行时。CI 的 Linux Python 3.11/3.12/3.13 执行全部集成测试；Windows 执行独立的客户端/解析测试。本机 Windows 可以连接 Docker Linux 服务运行全部集成测试。

## 数据与采集

公开应用基线来源为 [AppGoblin](https://github.com/appgoblin-dev/appgoblin-data)，CC BY 4.0。**Data provided by AppGoblin. https://github.com/appgoblin-dev/appgoblin-data**。

```powershell
appmon import data/raw/store_apps.tsv.xz --source appgoblin-public-baseline
appmon enqueue
appmon status
appmon keywords import examples/keywords-sea.json
```

应用 ID 保留大小写和前导零。国家和语言独立，不将某个地区不可见推断为全球下架。网络错误保留上次有效状态；重复缺失按配置确认。没有运行采集 worker 时，可以查看数据，但不会自动更新。公开接口和历史基线不能证明覆盖全球所有应用。

素材仅从已允许的商店 CDN 地址下载，不把任意 URL 当作代理。视频只归档允许 CDN 的直接媒体地址，不下载或转码第三方视频平台页面。`archive_videos` 可控制视频采集，视频封面与视频文件分别存储。

## 备份和恢复

备份包含 OceanBase 表、当前数据库命名空间下的所有对象、配置和必要本地密钥；恢复要求空数据库、空对象前缀和空数据目录。对象内容和文件清单均校验，恢复完成后采集保持暂停。

```powershell
# 先停止 API 和所有采集写入端，再执行：
appmon backup data/backups/backup.zip --writers-stopped
# 使用独立新数据库配置恢复：
appmon --config restore.toml restore data/backups/backup.zip
```

完整操作说明见 [离线部署](docs/offline-deployment.md)、[内容存储](docs/content-storage.md)、[本地开发](docs/local-development.md)。不要直接复制运行中的数据库卷。

## 扩展

先增加 SeaweedFS Volume Server 并设置故障域/副本策略，再按容量需求对稳定数据卷启用纠删码；单机多个容器不等于多机容灾。OceanBase、SeaweedFS Master 和 Filer 的高可用需一起规划。本仓库默认 Compose 为单机启动和验收配置；正式集群使用经资源及故障恢复验证的部署。

## 功能文档

[工作区结构](docs/platform-structure.md) · [应用分组](docs/app-groups.md) · [关键词追踪](docs/keywords.md) · [数据源](docs/data-source-configuration.md) · [账户权限](docs/accounts-and-permissions.md) · [采集中心](docs/collection-center.md) · [市场数据](docs/market-data.md) · [前端开发规范](docs/frontend-guidelines.md)

早期 `mysql-*` / `sqlite-*` 验收文档仅是历史记录，不适用于当前运行时。

远程仓库：[hengshu-credit/appstore_applications_monitoring](https://github.com/hengshu-credit/appstore_applications_monitoring)。[CI](https://github.com/hengshu-credit/appstore_applications_monitoring/actions/workflows/ci.yml)。
