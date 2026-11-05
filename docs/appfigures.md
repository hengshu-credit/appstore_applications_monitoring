# Appfigures 试用期采集

在「数据源」中配置 Appfigures。账密与 Personal Access Token 使用现有 `data/secrets/data-sources.key` 加密后保存在 `source_accounts`，不返回前端、不记录到日志。可以只配置 Token；只保存账密时显示「API 采集需填写 Personal Access Token」，不会尝试已停用的 Basic Auth。

## 权限与真实验收边界

- 账户页面：<https://app.appfigures.com/account/units>。
- 在 <https://appfigures.com/developers/keys> 创建 API Client 和 Personal Access Token，只读集成需要 `public:read`、`products:read`。
- [官方认证文档](https://docs.appfigures.com/guides/authentication/)与 [Basic Auth 停用说明](https://docs.appfigures.com/api/authentication/basic-auth)。
- [官方 API 访问说明](https://help.appfigures.com/en/article/appfigures-api-cli-and-mcp-access-limits-and-public-data-1seiibo/)区分网页套餐、公共数据 API 和额度。14 天网页试用不保证可通过 API 读取所有第三方应用。系统不自动开通或购买服务。
- 本次使用真实 MySQL 进行自动化测试，API 采用模拟响应验证分页和失败恢复。用户只有已登录网页，浏览器工具无法可靠确认当前网址并停止操作，尚未取得 Token，**未完成真实账户 API 试采，也未核实试用截止时间和额度**。

## 创建与执行

1. 保存 Token 并检查 API 授权（读取 `/usage`）。
2. 选择历史日期范围、国家、分类和应用，填写实际试用截止时间。界面默认回溯一年，可修改。本机时区的截止时间转换为 UTC 存储，不推测试用起点。
3. 国家/分类留空时从官方目录展开全部国家及 Apple/Google 分类；应用 ID 为 **Appfigures ID**，不是商店 ID。账户应用及榜单发现的应用可自动加入历史补采；有效商店 ID 同时导入现有应用池。
4. `appmon console daemon` 自动执行，关闭页面后继续。页面提供心跳、任务计数、失败原因、暂停/继续/停止和重试。

```powershell
.\.venv\Scripts\python.exe -m appmon appfigures status
.\.venv\Scripts\python.exe -m appmon appfigures plan --file examples/appfigures-plan.json
.\.venv\Scripts\python.exe -m appmon appfigures run --limit 5
.\.venv\Scripts\python.exe -m appmon console daemon
.\.venv\Scripts\python.exe -m appmon appfigures import-csv --file path\to\appfigures-export.csv
```

示例文件必须填写真实截止时间后才能运行。部署时更新前后端并重启 web/console，由现有初始化流程创建新表；保留原 MySQL、data 挂载和凭据密钥。Appfigures 遵守全局采集暂停，备份恢复后默认暂停有效。

## 范围与归档

| 类型 | 采集范围 |
|---|---|
| 目录 | 国家、分类、商店、账户应用完整 JSON |
| 榜单 | 来源支持的免费、付费、畅销榜，每页最多 1000，后续页固定同一快照小时 |
| 元数据 | 计划内按应用去重；有效商店 ID 加入应用池 |
| 下载/收入估算 | 每 31 天一个断点，保留逐日、分国家维度和缺失值 |
| 历史排名 | 同一日期分段合并国家请求，明确 UTC 时区 |
| 评论 | 每页 500；超过 500 页自动细分日期；单日仍超限时明确报错 |
| CSV | 网页导出的 UTF-8 CSV，单文件最多 10 MB；校验列数、数据行数并完整归档，不猜测列含义 |

先读取目录，再轮换处理榜单、元数据、估算、排名和评论，避免全部额度被榜单占用。每日保留未访问的榜单目标，已完成目标追加刷新。`_day` 是入队日期，实际观察时间以来源 `timestamp` 和归档采集时间为准，不代表所有国家每天均有成功快照。榜单 API 的全榜历史快照仅支持最近 24 小时。

`appfigures_runs/jobs/products/blocks/budget/artifacts` 保存计划、分页断点、应用去重、权限熔断、账户预算和归档索引。原始文件在 `data/public-sources/appfigures/*.json.gz` 或 `*.csv.gz`，SHA256 对解压后的 UTF-8 原文计算，随现有备份携带。每页的任务完成状态、扩展与索引在同一事务提交，下载仅管理员可读。

页面展示最近 50 份归档，历史文件不会删除；全部索引在 `appfigures_artifacts`。记录数为单份响应的应用、评论、非空排名观察或国家/日期估算条数，并非跨文件去重数。日期细分保留父请求和子请求原文，分析时需按来源主键去重。CSV 原样保留，不自动推断币种或混入官方实测量。

## 限额与恢复

按 `X-Request-Limit` / `X-Request-Usage` 和配置上限的较低值控制，默认 1000 请求/UTC 日、间隔至少 2 秒。`420` 等待下一 UTC 日，`429` 遵守 `Retry-After`，网络错误最多自动尝试 5 次。

`401` 暂停计划并提示更新 Token；`402/403` 熔断当前数据类型及后续新增任务，其余类型继续。权限恢复后点击「重试失败 / 权限任务」。到期不再发起新请求，保留全部文件和未完成队列。

「已归档」「待采集」「失败」「权限不足」分别计数。「计划已完成」仅代表选定范围全部任务成功，不能代表全商店或全部历史。

参考：[目录](https://docs.appfigures.com/reference/data/)、[榜单/排名](https://docs.appfigures.com/reference/ranks/)、[估算](https://docs.appfigures.com/reference/estimates/)、[评论](https://docs.appfigures.com/reference/reviews/)、[限额](https://docs.appfigures.com/rate-limits/)。
