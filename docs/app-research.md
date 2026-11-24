# 市场切换、榜单来源与应用一键采集

核查日期：2026-11-03（Asia/Shanghai）。

## 市场切换

应用详情的商店由应用身份决定。进入 Apple 应用详情时，顶部自动显示 App Store；在详情中切换国家或语言，仍查询同一 App 在新市场的记录。切换商店则返回该商店的应用库，不把 Apple 数字 ID 当成 Google 包名，也不继续请求原商店。

Apple 市场名单从旧抓取器的115个数字ID映射中分离，按 [Apple 官方国家/地区资料](https://developer.apple.com/help/app-store-connect/reference/pricing-and-availability/app-store-pricing-and-availability-start-times-by-country-or-region) 支持175个市场，包含哈萨克斯坦。名单和日期存于 `src/appmon/apple_countries.json`。原表 Nigeria 行代码误写 NER，按国家名识别为 NG；保留官方市场 Kosovo 的 XK 标识。

旧映射覆盖的市场仍使用原 native search；其他官方市场使用 Apple 网页结构化搜索，并检查响应中的 `intent.storefront` 与请求一致。不存在的市场不回退美国。175项是依据官方名单的能力配置，未声称逐个完成175个市场的网络实测。

## 七类榜单

| 榜单 | Google Play | App Store |
|---|---|---|
| 免费榜 | 公开榜单 RPC `topselling_free` | RSS `topfreeapplications` |
| 付费榜 | RPC `topselling_paid` | RSS `toppaidapplications` |
| 畅销榜 | RPC `topgrossing` | RSS `topgrossingapplications` |
| 人气飙升榜 | RPC `movers_shakers` | 本站派生：相邻两次免费榜的正向排名升幅 |
| 最新免费榜 | RPC `topselling_new_free` | RSS `newfreeapplications`，仅总榜 |
| 最新畅销榜 | 本站派生 | 本站派生 |
| 最新付费榜 | RPC `topselling_new_paid` | RSS `newpaidapplications`，仅总榜 |

以上直接来源已做真实请求探测。Google 的三个扩展榜单在总榜/FINANCE 分类返回不同或相应的结果。Apple 最新免费/付费返回数据，但忽略 `genre` 分类过滤，因此系统拒绝将它们标成分类榜。

Apple `newgrossingapplications` 返回 HTTP 400；Google `topselling_new_grossing` 无可解析榜单，另一个候选 `topgrossing_new` 的头部应用与普通畅销榜相同，无法证明是独立的新应用榜。因此“最新畅销榜”不冒充官方榜单：从当前畅销榜已返回的应用中，筛选相对于该次观察近30天发布的应用，保持基榜顺序。发布日期缺失会单独报告，不按零处理。

Apple 人气飙升榜只比较同一国家、语言、分类、来源与解析版本的相邻成功快照；新入榜缺少旧位置，不编造升幅。派生榜同时展示自身序号和基榜位置。查询接口：`GET /api/charts/view`，参数为 `store/country/language/collection/category`。

## 应用详情一键采集

点击“立即采集全部公开信息”，后台创建一个可追踪的配置计划，复用采集中心现有 worker 与日志：

- 当前市场的应用详情、版本、描述、评分、价格、图标/截图 URL 等公开资料。
- 最多100条近期评论；不承诺全历史。
- 该商店可直接获取的总榜和已知应用分类榜；不支持分类的新应用榜只安排总榜。
- 当前市场最多100个已跟踪关键词及应用名搜索；没有名称时使用应用 ID。超过的词显示剩余数量，可在采集中心分批安排。
- Apple Today 推荐观察。

操作会创建或启用对应任务，已存在目标的周期与深度保留。重复点击进行中的同一应用/市场会返回现有计划，避免重复排队。只有本轮请求之后观察到的实际尝试才计入完成进度；失败单独展示，不用旧快照冒充本轮成功。配置进度和网络采集进度分开，支持打开计划及每个子任务日志。

详情页汇总当前市场的各种总榜、分类榜位置与关键词排名，保留来源、时间、实际返回范围、未采集/未命中及派生口径。不同商店、国家和语言的数据不合并。

这项操作不代表取得开发者私有后台、无限历史评论或全球完整关键词库。图片仍保存公开来源 URL；原始响应按既有机制压缩留存。

## 广告追踪

打开「应用详情 → 广告追踪」，可在当前应用下按渠道筛选投放记录，查看文案、素材、展示媒体、来源日期和证据链接。「采集广告数据」将可用来源加入后台队列。具备广告写入权限时，「立即采集全部公开信息」也会安排这些任务。采集来源栏显示进度、最近成功时间和失败原因。

Google、Meta、TikTok、Reddit、Instagram、Yandex 和 App Store 均有独立渠道入口。AppGoblin 的网络观测覆盖 Google、Yandex 等移动广告网络。已核实的自动采集记录会按内容保存文案、素材 URL 和目标链接的版本，在卡片中可展开查看各版本首次和最近采集时间。

| 来源 | 可查看的内容 | 覆盖范围 |
|---|---|---|
| Google 广告透明度中心 | 开发者官网域名对应的公开广告、广告主、观察日期、原页和可解析的素材 | 每轮查看至多 8 条跨地区域名样本。素材中能核对商店应用链接的记录计入投放，其余可在域名线索中核查。公开查询和预览解析参考[开源实现](https://github.com/block-town/google-ads-transparency-mcp)。 |
| AppGoblin 应用推广素材 | 广告网络、图片或视频预览图、展示广告的应用、最近观测日期 | 按商店应用 ID 匹配。公开样本缺少投放国家时显示「地区未披露」。[推广素材示例](https://appgoblin.info/apps/com.einnovation.temu/ad-creatives) |
| Apple 广告资料库 | 广告位、文案、图片、视频、投放观察期和广告详情链接 | 按应用 ID 和资料库支持的欧洲市场查询近 90 天，日期以来源返回的窗口为准。分页入库，失败可续采。[Apple 接口文档](https://developer.apple.com/support/downloads/Ad-Repository.pdf) |
| Meta / Instagram | 两个独立的渠道筛选、广告原页、文案、版位和应用链接 | 配置 Meta Ad Library API 授权后查询欧盟及英国的商业广告。两个平台都有投放的广告可分别查看。[Meta 接口文档](https://developers.facebook.com/docs/graph-api/reference/ads_archive/) |
| TikTok | 广告原页、图片或视频、文案、下载链接和落地链接 | 配置 Commercial Content API 授权后检索所选市场近 30 天发布的广告，按详情中的应用地址确认归属。[TikTok 接口文档](https://developers.tiktok.com/docs/en/commercial-content-api-get-ad-details) |
| Reddit | 账户广告状态、推广帖、标题、媒体及目标链接 | 需配置广告账户 ID 和只读令牌。读取该账户中能通过商店链接核实归属的广告。[Reddit 广告接口](https://ads-api.reddit.com/docs/v3/api/list-ads) |
| AppGoblin 月度广告主榜 | 公开样本内按素材数和覆盖媒体应用数计算的两个排名 | 同值并列，每个指标仅使用该指标有值的样本。页面列出样本量和观察时间。[公开榜单](https://appgoblin.info/top-mobile-advertisers) |

商店总榜和分类榜使用当前国家、语言的已采集快照。自有账户日报只显示当前 Apple 应用和地区的最近 30 条记录。「广告观察」导入的证据也在该应用下可见，可附带 `channel`、`title`、`body`、`ad_url`、`landing_url`、`creative_type` 和 `creative_url`。素材链接保留来源地址。来源仅公开缩略图时，页面标注为预览图。

## 容器网络与界面

本地 Docker 采集器可通过 `.env` 的 `APPMON_HTTP_PROXY` / `APPMON_HTTPS_PROXY` 配置可达代理，例如 Docker Desktop 的 `http://host.docker.internal:7897`；不需要代理时留空。TLS 证书校验保持开启。网页和 MySQL 本地通信被 `NO_PROXY` 排除。

表单按钮、输入框、选择器及分类触发器统一为42px；文本行操作、复选框和菜单选项保留各自语义尺寸。前端静态入口要求缓存重新验证，避免更新后继续使用旧入口文件。
