# 关键词追踪验收

验收日期：2026-11-03，Asia/Shanghai。运行环境：Windows 本地 Python 虚拟环境、MySQL 8.4.6，以及 Docker Linux Python 3.12 镜像。详细命令见 [使用说明](keywords.md)。

## 真实搜索与历史

导入 `examples/keywords-sea.json` 后执行第一轮实时搜索，8 个任务全部成功，315 条排名落入业务 MySQL。每个任务默认 24 小时刷新，请求深度上限 50。

| 商店 | 国家 / 请求语言 | 关键词 | 首轮实际保存数 |
|---|---|---|---:|
| Google | TH / th | เงินกู้ | 30 |
| Apple | TH / th | เงินกู้ | 50 |
| Google | VN / vi | vay tiền | 30 |
| Apple | VN / vi | vay tiền | 50 |
| Google | PH / en | loan | 26 |
| Apple | PH / en | loan | 50 |
| Google | PH / fil | utang | 29 |
| Apple | PH / fil | utang | 50 |

第一轮的时间、前三名和反查样例见 [keyword-live-results.json](keyword-live-results.json)。这些是当时观测值，不是永久实时排名。

随后在运行中的 Docker `keywords` 容器内对菲律宾 `loan` 的 Google、Apple 两个任务各立即刷新一次，两次均成功。累计 **10 份有效快照、391 条历史排名、0 次失败尝试**。通过 `scripts/verify_keyword_data.py` 逐份读取原始 gzip、校验内容 SHA256、重放解析，并核对数据库顺序、连续位置和精确行数，全部相符。详见 [keyword-verification.json](keyword-verification.json)。

实际反查 `com.mocamoca`（PH）返回两个关键词：英语 `loan` 第 3，Filipino `utang` 第 1。第二次 `loan` 仍为第 3，`previous_position=3`、`change=0`；历史保留两次独立观察。排名变化、消失、重新出现及失败间隔的其他分支使用确定性集成用例验证，不伪造真实排名变动。

## 本地自动验证

`python -m unittest discover -s tests -v`：**35 项通过**，使用独立随机 MySQL 测试库，未清理业务数据。涵盖：

- 双商店有序解析、精确匹配卡、重复结果、Google dataset 迁移、前置空分组和异常页面。
- 多国家/语言/商店隔离，词库重复导入、整批输入验证、暂停/恢复。
- 成功空结果、排名升降、未命中、再次出现、失败保留上次成功排名、Retry-After。
- 并发跳过被锁任务、过期租约拒收、原子事务回滚、调度到期和单目标立即刷新。
- Windows GBK 控制台重定向时仍输出合法 UTF-8 多语言 JSON。
- 原有应用监控、导入、限速、旧 SQLite → MySQL 迁移回归。

另外回放了 MrAdex77 上游四个搜索 HTML 样例，成功解析 20、23、20、30 个结果。`compileall`、`git diff --check`、Compose `config --quiet` 均通过。

## 持续运行

已用 `docker compose -f compose.yaml --env-file .env --profile keywords up -d --build keywords` 构建并启动独立服务，容器命令为 `appmon keywords daemon`，自动重启策略继承 `unless-stopped`。MySQL 健康，关键词服务持续运行并检查到期时间；实际网络采集已在该容器中验证。原全池 `monitor` 服务未启动。

运行期间数据库、8 个任务及原始响应保存在原项目 `data/` 下。后续是否已刷新，以实时 `keywords list`、`keywords ranks` 和服务日志为准。

## 远程验证边界

工作流在 Linux/Windows × Python 3.11/3.12/3.13 六项矩阵内执行真实 MySQL 测试，以及关键词 CLI 导入、列表和反查。远程 CI 结果以交付消息中的最终提交 SHA 与 [GitHub Actions](https://github.com/hengshu-credit/appstore_applications_monitoring/actions/workflows/ci.yml) 对应运行为准；本记录的本地验证不能代替远程检查。实时商店网络采集不纳入远程 CI，避免把外部搜索内容波动作为断言。
