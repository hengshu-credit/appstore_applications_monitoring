# 观星离线备份、迁移与 Docker Compose 部署

## 数据范围与研究结论

2026-11-05 本机只读盘点：50 张表，应用池共 5,447,416 条（Google 3,045,113，Apple 2,402,303），数据库数据与索引的元数据统计约 1.81 GB，另有原始响应和文件目录。本次已完成 148 项后端测试、47 项前端测试，以及独立 Compose 环境中 50 表、1,250 应用、125 任务的备份恢复/跨页控制验收；没有对这份现有业务库执行全量停机备份，不能把测试包当成业务备份。

运行数据保存在 MySQL 8.4（全部业务表，包括应用、各市场历史、任务、用户权限、日志和数据源加密配置），文件保存在配置的 `data_dir`。部署代码或 `docker image save` **不会**自动包含数据库和绑定目录。

新命令 `appmon backup` 逐表流式导出 JSONL 到 ZIP64 压缩包，用 MySQL 一致性快照读取，逐表记录精确行数、字段清单与 SHA256。DECIMAL 保留原精度。文件包括 `responses/`、`icons/`、`exports/`、`raw/`、`keyword_reports/`、`public-sources/`、`secrets/` 及配置文件；不包含 `data/mysql/` 物理数据库、截图、测试目录、旧部署包、上游研究副本及宿主机 `.env`。未识别的数据库表会拒绝备份，不静默遗漏。

备份含用户密码哈希和数据源解密密钥，应作为私有数据保管；`secrets/data-sources.key` 必须与数据库一起迁移，否则已保存的数据源账号无法解密。备份 ZIP 本身未加密。新环境自行配置数据库密码、域名、代理和可选 Apple Ads 环境变量，不复制旧机器地址。

恢复前完整校验包内哈希，目标库必须**没有任何表**，目标文件目录也必须为空。全部表按原字段和 ID 分批插入并核对行数。中断或失败留下迁移标记，服务不会读取半成品；保留失败目标排查，另建空库/空目录重试，不提供覆盖业务库选项。恢复完成默认打开整体采集暂停开关；数据源手动操作和种子包同步不受此开关控制，所以验收前只启动 API 和前端。

仅支持 MySQL 8.4、相同程序版本/表结构的迁移；“任意新环境”指具备 Docker Engine + Compose v2 及对应架构的 Linux 容器环境（含 Docker Desktop）。镜像包记录 CPU 架构；amd64 包不能保证在 arm64 原生运行，应在目标架构重新打包。磁盘预留原数据、压缩包和恢复数据库的空间。全量大库耗时取决于磁盘和数据库吞吐，不承诺秒级恢复。

参考：[MySQL 备份与一致性快照限制](https://dev.mysql.com/doc/refman/8.4/en/mysqldump.html)、[Docker 镜像保存](https://docs.docker.com/reference/cli/docker/image/save/)、[Compose profiles](https://docs.docker.com/compose/how-tos/profiles/)。也可使用原生 `mysqldump --single-transaction --no-tablespaces --set-gtid-purged=OFF` 备份数据库，但仍需单独保存上述文件；禁止直接复制运行中的 `data/mysql/`。

## 在线源码部署

在仓库根目录复制 `.env.example` 为 `.env`，为 `MYSQL_PASSWORD` 和 `MYSQL_ROOT_PASSWORD` 设置不同的随机密码。服务器访问设置 `APPMON_BIND_ADDRESS=0.0.0.0`、`APPMON_ALLOWED_HOSTS=实际域名或IP`；HTTPS 反向代理部署时设置 `APPMON_COOKIE_SECURE=true`。前端入口默认 5173，API 8765、MySQL 3307 仅绑定回环地址。

```sh
docker compose -f compose.yaml --env-file .env --profile console config --quiet
docker compose -f compose.yaml --env-file .env --profile console up -d --build mysql web frontend
```

先在采集中心设置间隔，再启动重点任务 worker：

```sh
docker compose -f compose.yaml --env-file .env --profile console up -d console
# 需要全池详情、种子同步、发现流程时再启动 monitor
docker compose -f compose.yaml --env-file .env up -d monitor
docker compose -f compose.yaml --env-file .env --profile console logs --tail 100 web console
```

`console` 已包含关键词采集，通常无需再启动 `keywords` 独立 worker。不指定服务直接 `up` 会启动默认的全池 `monitor`，首次迁移验收请使用上述显式服务名单。

## 保存离线数据

先停止原环境所有写入者，包括容器中的 API/worker 和宿主机直接运行的 `appmon`、导入脚本；保持 MySQL 运行。`--writers-stopped` 是操作者对维护窗口的确认，工具不能检测另一台机器上的写入进程。它保证数据库快照与文件复制处于同一静止状态；备份完成后仅启动维护前实际运行的服务。

```sh
docker compose -f compose.yaml --env-file .env --profile console --profile keywords stop frontend web console monitor keywords
docker compose -f compose.yaml --env-file .env run --rm --no-deps --pull never monitor appmon backup /app/data/backups/guanxing.zip --writers-stopped
# 例如此前只运行网页服务
docker compose -f compose.yaml --env-file .env --profile console up -d web frontend
```

输出为 `data/backups/guanxing.zip` 和 `.zip.sha256`。`backups/` 不纳入备份，重复备份使用新文件名。现有 JSONL 业务导出只能导出部分表，不能替代全平台备份。备份会保留已暂停任务和历史结果。

## 打包程序镜像

联网构建机需要 Python 3.11+ 和 Docker：

```sh
python scripts/package_deployment.py --output dist/guanxing-offline.zip
```

程序包包含已构建后端/前端/MySQL镜像 `images.tar`、Compose、配置样例、镜像标签 `release.env`、架构/源码记录 `release.json`，以及本说明。外部 SHA256 文件可校验传输完整性。工作区有修改时记录 `working_tree_changes=true`，不宣称等同于纯提交构建。数据包单独复制，程序包不含真实凭据。

## 新环境离线导入

解压程序包，校验 SHA256；复制 `.env.example` 为 `.env` 并配置密码和访问地址。把数据包放到 `data/backups/guanxing.zip`。目标目录为全新部署目录，保持 `data/mysql/` 为空，不先启动应用初始化表。Linux 可用 `sha256sum -c 文件.zip.sha256`；Windows 可用 `Get-FileHash 文件.zip -Algorithm SHA256`。

```sh
docker image load -i images.tar
docker compose -f compose.yaml --env-file .env --env-file release.env --profile console config --quiet
docker compose -f compose.yaml --env-file .env --env-file release.env up -d --no-build --pull never mysql --wait
docker compose -f compose.yaml --env-file .env --env-file release.env run --rm --no-deps --pull never monitor appmon restore /app/data/backups/guanxing.zip
docker compose -f compose.yaml --env-file .env --env-file release.env --profile console up -d --no-build --pull never web frontend
docker compose -f compose.yaml --env-file .env --env-file release.env run --rm --no-deps --pull never monitor appmon status
```

访问 `http://目标地址:5173/`（本机使用 `127.0.0.1`），以原账号登录，核对应用池数量、历史、任务和数据源配置。`/health` 为 API 健康检查。备份中的 `config.toml` 供比对，恢复不覆盖新环境配置。无旧数据时跳过 restore，首次访问注册管理员即可。

验收后在采集中心确认整体限速并取消“暂停所有商店采集请求”，再启动所需 worker：

```sh
docker compose -f compose.yaml --env-file .env --env-file release.env --profile console up -d --no-build --pull never console
```

## 批量控制和控频语义

- 表头复选框选择当前页；“全选筛选结果（跨页）”使用服务端当前筛选条件；“全选全部任务（所有商店）”忽略筛选和分页。跨页仅支持暂停/恢复，并在执行前显示操作范围；不批量触发立即请求。
- 后端在事务内固定匹配任务 ID，随后更新全部匹配任务；状态变化不会导致翻页漏项。暂停使旧租约失效，恢复保留历史和到期时间。已发出的 HTTP 请求无法撤回。
- 整体最小请求间隔默认 5 秒，Google 2.5 秒、Apple 3.5 秒，各请求同时满足总间隔与商店间隔。多个进程/容器共享同一数据库，增加 worker 不增加总额度。配置每次请求前读取，重启后仍保留。
- 403、429、5xx 默认触发至少 900 秒的整体冷却，并遵守更长的 Retry-After；等待中的请求会重新检查窗口。长等待交还队列，避免占用过期租约。频率降低不能保证来源不封禁，需按来源允许的频率调整。
- 请求额度覆盖商店 Client 的详情、搜索、榜单、评论、推荐、图标及发现请求；种子包下载、公开指标脚本、第三方账号授权/手动调用不使用这个商店额度。“基础采集流程”中的种子、全池、发现开关仍独立管理。
