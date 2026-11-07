# 应用明细与排行榜核查（2026-11-07）

## 界面与数据口径

- 明细表固定分成「通用榜单」「分类榜单」。分类列读取该行 APP 当前类别对应的排名，不把新闻、表盘等其他类别铺成列；后端也只查询通用和该 APP 的类别，减少无关查询。未知值显示 `-`，有效采样中未入榜显示空白，真实零值继续显示0。
- 排行榜选择器仍有7种：免费、付费、畅销、人气飙升、新应用免费、新应用畅销、新应用付费。新应用榜不是普通榜的“最新刷新版本”，因此改名消除歧义。选择器悬停可查看来源口径；新应用畅销为近30天发布应用的本地派生视图，Apple人气飙升也是派生视图，需有效历史快照。
- 国家标题使用国旗和名称，右侧显示语言名称与代码；布局仅显示“横向排列”“纵向排列”。采集进度入口移到筛选栏按钮区。
- 纵向国家卡片宽度随所选榜单数变化，桌面一行完整展示一个国家所选榜单，不在国家卡片内截断或横向滚动；更宽屏可并列国家，手机窄屏在卡片内折行，确保内容可读。横向应用榜仍可左右滚动。
- Esc、移出和滚动仅关闭应用详情提示，保留当前应用及跨榜高亮；选择另一应用或更换布局后才切换/清空选择。

## ID 核查

分组内394个Apple应用（402个市场目标）的应用ID均为数字，1,695个Google应用的应用ID均不是纯数字。没有据此批量改写任何标识。

现场调用 Apple lookup，WhatsApp 返回 `trackId=310633997`、`artistId=310634000`，与系统字符串记录一致。[官方查询](https://itunes.apple.com/lookup?id=310633997&country=us)。Google 也同时存在数值开发者ID和名称型开发者ID；截图中的 `6942553452222258407` 能对应 [Credicuotas 官方开发者页](https://play.google.com/store/apps/dev?id=6942553452222258407)。后端始终将开发者ID转为字符串输出，新增回归检查超过JavaScript安全整数范围的ID不丢位。形状核查覆盖分组记录，联网核验为上述实例，不等于逐一复核所有开发者。

## 权限覆盖

核验快照中，分组内1,736个Google应用/国家目标均有权限任务，1,563个已有成功结果，共14个返回分组：Camera、Device ID & call information、Location、Other、Phone、Photos/Media/Files、Storage、Wi-Fi connection information、Calendar、SMS、Microphone、Contacts、Identity、Device & app history。解析器遍历全部返回分组、保存所有权限条目并去重；UI在基础列之外动态补充返回的分组，未限制为最初7列。

这指Google公开接口返回的声明权限，不证明覆盖APK完整Manifest、所有Android权限或设备实际授权。[Google权限说明](https://support.google.com/googleplay/android-developer/answer/9214102)。Apple App Store公开隐私标签描述数据收集与使用，不等价于系统权限清单；当前权限接口不支持Apple，保持未知，不用“未声明”代替。[Apple隐私标签说明](https://developer.apple.com/app-store/app-privacy-details/)。

## 下载、收入与关键词

`app_metric_facts` 当前无记录，市场数据产品映射也为空，故期间下载量/收入无法显示。公开Google详情的累计安装下限已独立展示，不能当作某国家某日下载量，更不能推算收入。

已有市场数据授权配置，但现场GET应用元数据返回HTTP 403（`access_denied`），因此未继续请求估算或伪造数值。需要在数据管理接入有相应产品/估算权限的来源：自有应用可从 [App Store Connect Sales Reports](https://developer.apple.com/documentation/appstoreconnectapi/get-v1-salesreports) 或 [Google Play开发者报表](https://support.google.com/googleplay/android-developer/answer/6135870) 取数；第三方应用下载/收入属于授权估算，例如 [Appfigures estimates API](https://docs.appfigures.com/api/reference/v2/estimates)。现有系统可归档该API并映射到国家/日期指标，或通过分组“导入指标”导入CSV；真实与估算、币种不能混算。

关键词覆盖来自同商店、国家、语言下的搜索采样，失败、过期、暂停、深度变化均不计有效覆盖。已按组内应用名称补建2,120个缺失的关键词目标（深度200），四个缺少有效名称的目标未猜词。品牌词只是起点，完整ASO覆盖还需要业务词库或合法取得的ASO报告；不声称获得商店隐藏的完整索引词。

## 实际采集动作

执行 `scripts/reconcile_member_sources.py`：复用原Top50采集计划，按当前商店能力目录去重注册所有非旧版细分分类与源榜组合。新增38,250个Google分类榜目标；标准支持组合共99,600个（源是否在某请求地区可用仍以实采为准），派生榜复用基榜而不重复发请求。原有失败退避、暂停、限速与视频不下载策略保持。

另补建3个应用分组所需的分类榜目标，使请求语言与成员一致。计划10–14共2,123项配置均已完成展开，实际HTTP仍由后台消费者执行。已有组内权限目标不重复创建。

现场核验阿根廷 Apple 财务6015、教育6017，各自免费/付费/畅销共6个榜单，均成功保存50条。核验时分类目标已有Google 1,901个、Apple 16个成功采样；这是运行中的进度，不能称为全量完成。

本地证据：`data/exports/member-source-reconciliation.json`、`data/exports/category-live-check.json`，以及计划10–14和对应采集日志。可再次运行补齐脚本，已存在目标不会重复创建，用户后续暂停不会被脚本反复恢复。
