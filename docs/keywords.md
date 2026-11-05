# 多国家、多语言关键词排名追踪

基于本地点点报告的首版词库已建立，包含总关键词库和独立ASO自然搜索词库，数量、入口、导出文件与重建方式见 [初始关键词库 v1](initial-keyword-libraries.md)。

每个 `(商店, 国家, 请求语言, 关键词)` 是独立任务。采集返回的有序应用列表写入 MySQL，反向索引支持查询某个应用在哪些关键词下出现、最近排名、相对上次成功采集的升降，以及历史。新发现的应用同步加入现有 `apps` 池。

## 快速使用

在仓库目录运行，Python 环境与 MySQL 沿用 README。已有数据库首次运行任意命令会自动新增三张表，不重建应用池。

```powershell
# 导入可编辑示例：泰语、越南语、菲律宾英语和 Filipino；双商店，共 8 个任务。
.\.venv\Scripts\python.exe -m appmon keywords import examples/keywords-sea.json

# 自定义任务：每 6 小时观察一次，请求最多 50 个结果。
.\.venv\Scripts\python.exe -m appmon keywords add google "เงินกู้" --country th --language th --depth 50 --refresh-hours 6
.\.venv\Scripts\python.exe -m appmon keywords list

# 执行到期任务；有采集失败时退出码为 2。
.\.venv\Scripts\python.exe -m appmon keywords run --limit 200

# 某个任务立即查询一次商店；仅此命令会显式跳过该任务的到期/退避时间。
.\.venv\Scripts\python.exe -m appmon keywords run --target-id 1 --refresh

# 查询关键词最近有效快照，及某个 App 的所有已发现关键词。
.\.venv\Scripts\python.exe -m appmon keywords ranks 1
.\.venv\Scripts\python.exe -m appmon keywords app google com.example.app
.\.venv\Scripts\python.exe -m appmon keywords app apple 123456789 --country ph --language en
.\.venv\Scripts\python.exe -m appmon keywords history 1 --app-id com.example.app --limit 100

# 暂停保留历史；重复导入不会恢复已暂停任务。
.\.venv\Scripts\python.exe -m appmon keywords pause 1
.\.venv\Scripts\python.exe -m appmon keywords resume 1
```

示例 ID 需替换为 `keywords list` 或真实商店返回的 ID。导入文件为 UTF-8 JSON 数组，每项必填 `store`、`country`、`language`、`keyword`；可选 `depth`（1–200，默认 50）、`refresh_hours`（默认 24）。整批先验证，再原子写入。Unicode NFKC、首尾/连续空白、国家和语言格式被规范化；保留关键词大小写。重复导入更新采集设置，保留历史、暂停状态和已有到期时间。调整周期后可用单目标 `--refresh` 立即应用新周期。

## 定期运行

现有 `appmon daemon` 已包含关键词任务。也可以单独运行关键词 worker，避免全池种子同步和应用详情任务拖延关键词刷新：

```powershell
# 前台持续运行，Ctrl+C 停止。
.\.venv\Scripts\python.exe -m appmon keywords daemon

# Docker 后台持续运行，自动重启、共享原 MySQL 和 data/responses。
docker compose -f compose.yaml --env-file .env --profile keywords up -d --build keywords
docker compose -f compose.yaml --env-file .env --profile keywords logs --tail 100 keywords
docker compose -f compose.yaml --env-file .env --profile keywords stop keywords
```

独立服务不会自动启动原 `monitor` 服务。空闲每 10 秒检查到期任务，默认每个成功任务 24 小时后重新到期；可逐词配置周期。任务耗时、限流、网络故障可能推迟刷新，因此“实时”指采集时向商店发起即时搜索，查询存储结果时必须同时看 `observed_at` 和 `stale`。两个 worker 同时运行时由 MySQL 租约及共享商店限速协调；过期 worker 的结果不会覆盖新结果。失败采用指数退避并尊重服务端 Retry-After，不影响上次成功快照。

## 如何读反向查询

`keywords app` 返回该应用**曾经命中过**的全部已跟踪关键词，包含国家、请求语言、任务周期、当前/上次位置、变化及最近尝试。

| 字段 | 含义 |
|---|---|
| `position` | 最近成功搜索中的位置，从 1 开始；未出现在实际返回结果中为 null |
| `previous_position` | 上一次成功搜索的位置；采集失败不会推进它 |
| `change` | 上次位置减去本次位置，正数上升、负数下降；两次都有位置且采集口径一致才计算 |
| `status=not_observed` | 曾经出现，但未出现在本次返回结果中；不代表下架、零排名或所有后续页都没有 |
| `latest_success.returned_count` | 本次真正保存的不同应用数，不能用请求的 depth 代替 |
| `latest_success.observed_at` | 采集完成的 UTC epoch 秒 |
| `latest_attempt` | 最新尝试，可能失败；失败原因和时间独立于有效排名 |
| `stale` | 没有成功记录、最后尝试失败，或有效排名已超过设定刷新周期 |

任务还没有成功结果时，`keywords ranks` 的 `latest_success` 为 null；成功空结果则有快照、`returned_count=0`。从未在词库结果中出现的应用，反查返回空列表，不代表它没有其他关键词覆盖。该库覆盖范围取决于维护的词库，不能自动还原所有全球关键词。

## 采集口径及研究依据

- [AppGoblin 关键词 API](https://github.com/appgoblin-dev/appgoblin/blob/main/backend/api_app/controllers/public/v1/keywords.py) 的公开返回结构当前固定 `country="US"`；不把它改写成泰国等地区数据，也不使用其美国难度指标冒充本地指标。
- Google 根据 [MrAdex77/google-play-scraper](https://github.com/MrAdex77/google-play-scraper) 的结构化搜索结果映射提取有序结果与顶部精确匹配卡，使用 `gl`/`hl` 请求国家和语言。不把页面全部链接或推荐栏按应用 ID 排序后当作排名。
- Apple 对已有数字ID映射的市场使用 [app-store-scraper](https://github.com/facundoolano/app-store-scraper) 的 native search 协议；对官方支持但旧映射遗漏的市场（例如哈萨克斯坦），使用对应国家的 Apple 网页搜索结构化结果，并验证响应中的市场代码。支持表现为175个市场，未知地区拒绝配置，不静默回退 US；两种来源分别记录 source/parser_version，不跨协议计算排名升降。
- [AsoBeast](https://github.com/AsoBeast/asobeast) 提供关键词周期检查、市场分离和历史快照的参考；此项目在现有 Python/MySQL 内独立实现，没有引入其 AGPL 应用源码或额外 Node 服务。

**本版双商店仅采集首个搜索响应，`coverage=first_response`。** `depth=200` 只是最多保留 200 个结果，不保证获得 200 条；Google 常只返回约 20–30 个。分页尚未实现，不把“本次 30 条中未找到”写成“排名大于 200”。语言字段是请求语言，商店可能按自身规则回退，本系统没有声称验证了实际返回语言。国家参数、服务器 IP、商店实验等会影响结果；这是公开搜索来源的观察排名，不保证等同于每个当地用户、设备或广告位排名。每份快照记录 source、parser_version、coverage 与原始响应，便于复核。

## 数据与导出

| 表 | 内容 |
|---|---|
| `keyword_targets` | 多语言词库、地区、周期、到期、租约、失败信息、最近两次有效快照指针 |
| `keyword_runs` | 每次成功/失败尝试、观察时间、来源、解析版本、请求/实际深度、原始响应路径 |
| `keyword_ranks` | 每份成功快照的应用、位置、名称；按应用 ID 建立反向查询索引 |

原始响应 gzip 位于 `data/responses/`，每次成功观察均存快照，即使结果未变也保留时间点。快照、排名行、应用池新增与任务进度同一事务提交。

```powershell
.\.venv\Scripts\python.exe -m appmon export keyword_targets data/exports/keyword-targets.jsonl
.\.venv\Scripts\python.exe -m appmon export keyword_runs data/exports/keyword-runs.jsonl
.\.venv\Scripts\python.exe -m appmon export keyword_ranks data/exports/keyword-ranks.jsonl
```

当前保留 CLI 和 MySQL 入口，并新增独立前端与 HTTP API，见 [应用情报控制台](console.md)。关键词采集本身的部署、测试和真实采集结果见 [关键词验收记录](keyword-verification.md)。
