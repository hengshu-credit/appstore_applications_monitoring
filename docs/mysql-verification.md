# MySQL 迁移验收

核查日期：2026-11-02（Asia/Shanghai）。项目存储从 SQLite 切换为 MySQL 8.4；SQLite 只保留一次性只读迁移入口；旧库回退备份已于 2026-11-02 按用户要求删除。

## 部署与代码

- 独立 MySQL 8.4.6 服务，Compose 项目 `appstore-applications-monitoring`。
- 主机 `127.0.0.1:3307`，库 `appstore_applications_monitoring`，用户 `appmon`；密码写入被 Git 忽略的 `.env`，未写入源码或报告。
- MySQL 持久化目录为 `data/mysql/`；原始下载与响应仍保留在 `data/raw/` 和 `data/responses/`。
- InnoDB、utf8mb4；应用 ID 使用 ASCII binary collation，保留大小写和前导零。
- 所有运行时查询、导入、导出、任务领取、快照及历史事件均使用 PyMySQL 和原生 MySQL SQL。
- 队列领取使用 `FOR UPDATE SKIP LOCKED`；完成结果时锁定对应任务并校验租约 token；限速记录使用行锁更新。
- 测试使用独立随机 MySQL 测试库，不使用 SQLite 代替数据库测试。

## 迁移验证

完整迁移结果由 `mysql-migration.json` 记录。逐表按主键排序，对每一行的每个字段生成规范化 JSON 后累积 SHA256，并核对精确行数；不是抽样比较。源文件始终只读，开始和结束时 SHA256 必须一致。

每批数据和断点在同一个 MySQL 事务提交；断线或中断可续传。不同源文件、已有非迁移数据的目标库、无法匹配的字段结构都会拒绝导入。未完成全表验证时禁止启动运行时客户端。

## 本次迁移结果

九张表合计 **10,881,793 行**，全部字段摘要和行数一致；九张表的 `CHECK TABLE ... QUICK` 均返回 OK。

| 表 | 迁移行数 | 源/目标 SHA256 |
|---|---:|---|
| apps | 5,440,876 | 一致 |
| imports | 2 | 一致 |
| jobs | 5,440,876 | 一致 |
| listings | 4 | 一致 |
| snapshots | 4 | 一致 |
| events | 4 | 一致 |
| requests | 6 | 一致 |
| schedules | 11 | 一致 |
| discovery_runs | 10 | 一致 |

旧文件 SHA256：`f24291eb2e0636c79c2801cba77b72e578ad8cdf55c9fb3e3c8fb5635c8f55d5`。旧文件已移动到 `data/legacy/monitor.sqlite3.bak`，大小 1,243,664,384 字节，移动后再次核对源文件校验值一致。该回退副本随后已于 2026-11-02 按用户要求删除，迁移校验报告继续保留。

迁移完成后，实际抓取 Google `com.whatsapp` 和 Apple `310633997` 并写入 MySQL：2 次成功、0 次失败。新增两条真实变更事件，事件流导出共 6 行。运行后的新数据会继续变化，迁移摘要记录的是切换时的一致性基线，不代表后续数据库必须与旧备份相同。

Docker 临时监控容器执行 `appmon init` 成功，确认 Linux 容器内通过 MySQL 服务名连接新库；临时容器已退出。MySQL 服务保持健康运行，监控 daemon 尚未持续启动。

## 本地验证

- Windows / Python 3.11.5 / MySQL 8.4.6：21 项测试全部通过，最后一轮耗时 198.364 秒。
- 覆盖原有监控状态机、重试、地区隔离和解析异常，并验证真实 MySQL 的事务回滚、并发领取、并发限速、大小写/Unicode、迁移中断续传、拒绝覆盖目标和同文件并发导入保护。
- `compileall`、`pip check`、wheel 构建及 Compose 配置校验通过。
- Docker 监控镜像构建通过，Python 3.12 Debian bookworm 基础镜像；最初通用 slim 标签拉取遇到 Docker Hub 网络错误，固定为本机已有的 bookworm 标签后构建成功。
- `.env` 已验证被 Git 忽略；wheel 不包含凭据或业务数据；应用运行时模块不导入 sqlite3。

## CI 状态

MySQL 迁移验收时执行的是本地验证。远程仓库现为 [hengshu-credit/appstore_applications_monitoring](https://github.com/hengshu-credit/appstore_applications_monitoring)，保留 Linux/Windows × Python 3.11/3.12/3.13 六项矩阵，每个任务启动真实 MySQL 8.4 运行测试。[远程 CI](https://github.com/hengshu-credit/appstore_applications_monitoring/actions/workflows/ci.yml) 必须以最终提交 SHA 为准核对，不能用本地验证代替。
