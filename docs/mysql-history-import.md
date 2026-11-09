# 一次性 MySQL 历史导入

应用运行时仍只连接 OceanBase。`scripts/import_mysql.py` 用于读取保留的旧 MySQL 数据，在独立的 OceanBase 数据库中转换并验证；不会覆盖当前正在使用的数据库，也不会修改源表。

## 前提

- 停止旧 MySQL 的所有业务写入，将源实例设置为 `super_read_only=ON`。导入程序还将每个源连接设为只读。
- `.env` 保留旧源的 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_DATABASE`、`MYSQL_ROOT_PASSWORD`；这些配置只供此工具读取，不是运行时回退。
- 提供当前 OceanBase、SeaweedFS 的 `OCEANBASE_*` 和 `S3_*` 配置。
- `--target` 必须是不同于当前运行库的独立数据库；已有目标必须具有本次导入的来源标识。
- 保留旧 `data` 文件目录。数据库和文件均在验证完成前保留，不自动删除。

## 执行与恢复

```powershell
python scripts/import_mysql.py --target appstore_applications_monitoring_history_20261109 --state data/mysql-import-20261109
```

每批数据与断点在同一目标事务提交。再次运行相同命令会从最后一次提交继续；不要同时运行多个导入进程，也不要在恢复时更改 batch-size。复合主键使用展开的字典序范围分页，避免 MySQL 从索引开头重复扫描。

每张表会核对行数及按主键排序、涵盖全部字段的分批 SHA256 链。迁移中的数据库保持不可由应用连接，直到所有表和正文引用完成验证。源数据库标识、结构指纹和行数变化会拒绝继续。

旧 zlib 正文解压后核对原文 SHA256，再写入对象存储；图片、报告保持原文件字节，gzip 响应转为保存原文的无损对象。所有新对象回读验证，源文件映射记录于 `files.jsonl`。旧文件路径转换为对象引用。未完成任务的租约清零，运行中的计划/导出重新排队，失败计数和历史观测时间保留。

旧浏览器会话、限流桶、请求等待者是瞬时状态，不恢复到新系统。旧审计历史仍保留。

## 保留当前登录并切换

源数据复制验证完成后，停止当前 API 和全部 worker，再执行：

```powershell
python scripts/import_mysql.py --target appstore_applications_monitoring_history_20261109 --state data/mysql-import-20261109 --activate --writers-stopped
```

该步骤按用户名关联当前账号，保留当前密码/恢复码、当前有效会话及新增审计记录。当前运行库仍不修改。如果当前库已经有新的应用、分组等业务数据，工具拒绝自动覆盖，必须另行审查合并策略。

检查 `verified.json` 与 `activation.json` 后，将 `.env` 的 `OCEANBASE_DATABASE` 改为验证完成的目标库，并重新创建 API/worker 容器。保留原运行库和只读 MySQL 作为回退来源。导入保留原有全局暂停状态，不自动扩大采集范围。

## 磁盘

对象数据目录可以通过 `SEAWEEDFS_DATA_DIR` 指定宿主机目录；不设置则使用命名卷。移动对象目录时先停 SeaweedFS，完整复制旧数据，再切换挂载并校验，不能删除仍被数据库引用的对象。

OceanBase 数据及日志仍使用 Linux 数据卷。导入大量数据时应监控磁盘、MemStore 与转储进度；必要时降低转储触发比例，让转储先于写入节流发生，保留节流和事务保护。

## 本次执行结果（2026-11-09）

已完成旧库 80 张表的处理：导入 30,723,371 行，逐表行数和覆盖全部字段的 SHA256 链校验通过。旧会话、限流桶和请求等待者共 3 行瞬时状态未恢复；当前账号密码、恢复码与当前会话保留，新库新增的审计/运行日志另外合并。

- 应用：5,494,572。
- 详情任务：5,758,105。
- 关键词追踪目标：384,526；排名明细：388,520。
- 分组：4；分组成员/市场目标：2,138。
- 历史快照：218,806；当前地区详情：217,488。
- 完整正文对象索引：218,847。
- 公开基线记录：6,574,809。

已切换到验证后的独立数据库，保留原 MySQL 与切换前的 OceanBase 库。旧 MySQL 已停止，未删除原数据。原库因磁盘余量不足而暂停的采集状态继续保留；对象数据移到 C 盘，数据库卷仍在原 Docker 存储中。

详细逐表记录见本机 `data/mysql-import-20261109/summary.json`、`verified.json`、`activation.json`、`live-verification.json`。页面已实屏确认恢复 4 个分组，当前登录继续有效。
