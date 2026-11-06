# 公开金融借贷与 ASO 扩词

`scripts/expand_public_keywords.py` 按项目市场目录逐国采集公开候选词，结果可直接导入现有 **关键词中心 → 候选词库**。默认使用每个国家的 Google 请求语言；Apple 请求使用自己的市场默认语言。金融检索种子覆盖当前全部默认语言，只负责发起检索，未被来源返回的种子不会入库。

## 来源和口径

| 来源 | 提取内容 | 证据及限制 |
|---|---|---|
| Google 搜索联想 | 金融种子的完整联想词 | 保留 `gl`、`hl` 和原始查询；这些参数不保证服务端严格定位，不能称为当地真实搜索量 |
| App Store 金融榜 | 最多 100 个金融应用的完整标题、金融标题片段、介绍中实际出现的金融词 | 核对返回 storefront 和金融分类 6015；只是榜单文案，不是关键词搜索排名 |
| Google Play 公开搜索 | 含金融词的应用标题及片段 | 复用项目搜索解析器；网络/布局失败单独记录，不以空列表冒充成功 |
| 金融 APP 官网 | 页面可见文字中的金融词、已审阅的短语 | 忽略脚本和样式；每个短语必须实际出现于返回的正文 |
| 广告平台公开案例 | 例如 Kredivo 印尼、Banco Azteca 墨西哥案例中的金融短语 | 是案例文案，不是广告主实际竞价词、投放词或广告效果数据 |

金融榜包含银行、支付、理财等应用，所以输出包含金融 ASO 品牌词和借贷词。候选词仍需按具体业务筛选；不推断每个应用均提供贷款。请求语言是采集配置，不等于自动检测出的文本语种。官网与广告案例的国家/语言绑定在 `examples/public-keyword-pages.json` 中逐项配置，不复制其他国家的搜索排名。

所有来源保留链接、UTC 观察时间、原始响应 SHA256、原始文件、原文短片段、提取类型和应用 ID（如有）。关键词采用 NFKC、空白规范化、移除不可见格式字符和 casefold 去重；原始大小写仍保存在响应与证据中。不会生成搜索量或自动创建追踪任务，也不会把公开候选词混入仅存历史自然搜索报告的 ASO 报告库。

## 运行

```powershell
.\.venv\Scripts\python.exe scripts/expand_public_keywords.py --countries all --pages examples/public-keyword-pages.json --output data/keyword_libraries/public-finance-next --import-db
```

可使用 `--countries th,id,vn,ph,ke,ng,mx,br,in,kz` 缩小市场范围，使用 `--sources suggest,apple` 选择通道。`--sources pages --pages examples/public-keyword-pages.json` 仅采集已配置的官网/广告案例。`--seeds` 控制每市场检索种子数（1–10）；默认 3。默认 6 个工作线程、每主机每秒启动不超过一次请求，响应最大 8 MB。HTTP 403/429 会暂停该主机后续请求；不会绕过验证或关闭 TLS 校验。

相同计划和输出目录会复用逐请求检查点；追加 `--retry-errors` 仅重试失败项。变更市场、来源、种子或页面配置后必须使用新目录。网络代理可显式使用 `--proxy`，默认遵循 requests 的环境配置。

```powershell
# 使用原计划续跑，参数必须与原计划相同
.\.venv\Scripts\python.exe scripts/expand_public_keywords.py --countries all --pages examples/public-keyword-pages.json --output data/keyword_libraries/public-finance-next --retry-errors
# 解析器修复后，从原始响应重建，不重新联网
.\.venv\Scripts\python.exe scripts/expand_public_keywords.py --output data/keyword_libraries/public-finance-next --reparse
# 校验导出文件后导入；重复导入不增加相同来源记录
.\.venv\Scripts\python.exe scripts/expand_public_keywords.py --output data/keyword_libraries/public-finance-next --import-only
```

`keywords.jsonl` 每行一个市场词条，`sources` 保留不同请求/应用的证据；`manifest.json` 列出每次请求的状态、词数、错误和导出哈希；`observations/` 保存检查点，`raw/` 保存原文；`import.json` 保存导入处理数量。原始数据留在已忽略的 `data/`，不提交到公开仓库。

导入数量是处理的来源记录数，不一定是净新增量。导入按 `(store,country,language,keyword,source_key)` 幂等，不删除旧词；同一日期、来源 URL 和来源类型的重复观察合并。全库总量和净新增量需在数据库中另行核对。完整广告词及 ASA/ASO 指标仍使用已有的授权报告导入功能，公开网页不能提供这些数据。

## 2026-11-07 实采及入库

已遍历目录中的 250 个国家/地区配置，涉及 60 个请求语言值。每个配置均得到候选词，但搜索联想的国家参数不能证明来源严格限定当地用户。两个批次合并为 **29,396 个市场词条**；与原库比较后，**净新增 29,372 词**，总库从 **118,548 增至 147,920**。来源记录从 128,306 增至 167,544，新增 39,238 条去重来源记录。已逐条核对全部导入身份与来源键，原有词条完整保留，追踪任务数量仍为 114,567。

| 来源 | 请求结果 | 导出证据记录数 |
|---|---|---:|
| 搜索联想 | 708 次成功 | 10,833 |
| Apple 金融榜 | 175 个 storefront 成功 | 28,289 |
| 官网 | 14 次成功、2 次失败（包含补采前的旧失败） | 140 |
| 广告平台公开页面 | 3 次请求成功，其中 2 个已核实案例提供词条，另一个页面未提取到词 | 26 |
| Google Play | 250 次 TLS 连接失败；直连及显式代理样本复查也失败 | 0 |

导出证据数合计 39,288，比数据库新增来源数多 50，原因是官网初采与补采的同日相同来源被数据库幂等合并。失败清单如实保留；Solva 官网返回 HTTP 403，未绕过。Kueski/Nubank 正常贷款页面内嵌表单验证码脚本，修复误判后已成功补采。Google 目标库新增的是搜索联想或官网/案例候选词，不能解释为本次成功抓取了 Google Play 文案。

| 市场 | 本次导出市场词条 |
|---|---:|
| 印尼 | 197 |
| 印度 | 196 |
| 菲律宾 | 187 |
| 墨西哥 / 尼日利亚 | 各 186 |
| 越南 / 肯尼亚 | 各 177 |
| 泰国 | 172 |
| 巴西 | 171 |
| 中国 | 164 |
| 美国 / 日本 | 各 161 |
| 英国 | 158 |
| 哈萨克斯坦 | 155 |

完整 250 项统计与证据位于本地：

- `data/keyword_libraries/public-finance-delivery-2026-11-07/keywords.jsonl`：合并词库，原文和原始快照路径可追溯。
- `data/keyword_libraries/public-finance-delivery-2026-11-07/country-coverage.json`：全部国家/地区、商店、语言、词数及请求状态。
- `data/keyword_libraries/public-finance-delivery-2026-11-07/delivery.json`：入库前后数量及全量核验结果。
- `data/keyword_libraries/public-finance-2026-11-07/` 与 `public-finance-pages-2026-11-07/`：原始请求计划、响应、来源观察和分批导出。重现此次历史请求应读取保存的 `plan.json`；示例页面配置已在研究过程中修正和扩充。

合并词库 SHA256：`026cd7d9209958de689977df3836801945edc2ae45708bc7f3c6061f55d3ba92`。

验证：8 项新测试与 14 项现有关键词/报告库回归通过；另完成全部原始响应哈希核验、JSONL 全量回读、全部来源身份入库核对。新测试覆盖空响应与失败区别、国家/商店隔离、原文证据、隐藏字符、土耳其语大小写、正常页面验证码脚本、断点恢复和幂等导入。不将本地检查解释为远程 CI 已通过。
