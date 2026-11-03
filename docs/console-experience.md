# 市场筛选、日志与图标

顶部应用商店可选择 Google Play、App Store 或全部商店。应用库按商店交错分页，榜单和上下架历史分别展示来源；概览和任务管理统计所选商店已观察的全部市场。应用、关键词排名、事件和评论研究仍保留国家、语言及观察时间。采集计划、库外商店搜索和 ASO 编辑需要明确单个商店，避免将相同名称或不同应用 ID 混用。

请求语言默认自动匹配国家和商店，仍可手动选择语言。推荐来自 Unicode CLDR 49 的地区语言信息及商店本地化能力；例如哈萨克斯坦 Google Play 使用 `kk`、App Store 使用 `ru`，泰国使用 `th`、越南使用 `vi`，菲律宾 Google 使用 `fil`、Apple 使用 `en`。它是请求偏好，实际返回语言可能由商店回退决定。`kz`、`us` 等国家代码不作为语言。旧浏览器偏好迁移到自动模式，已有数据库任务和历史语言不改写。

目录位于 `src/appmon/market_locales.json`，通过 `/api/catalog` 随目录返回。先运行 `scripts/refresh_store_catalog.py` 准备固定版本 CLDR 原始包，再运行 `scripts/build_market_locales.py` 重建推荐。原始包版本与许可沿用 [商店目录说明](store-catalog.md)。商店语言清单参考 [Google Play 官方说明](https://support.google.com/googleplay/android-developer/answer/9844778) 与 [App Store Connect 本地化说明](https://developer.apple.com/help/app-store-connect/reference/app-information/app-store-localizations/)。

采集中心任务表只显示状态；点击“日志”打开每两秒增量更新的日志面板。新错误支持展开异常类型、请求阶段、域名、HTTP 状态、超时、重试等待和调用位置。不会记录请求头、凭据、原始响应或本地变量。历史错误没有详细诊断时会明确说明。部署初始化自动为 `collection_logs` 增加可空 `details` 列，保留旧记录。

应用图标由统一组件显示。浏览器只为当前可见应用调用 `/api/icons/resolve`，每批最多50个；后台先复用已知图标地址，没有地址时采集该市场的公开详情。图标保存于 `data/icons/`，由同源认证接口提供；单文件限2MB，只接受两家商店图片域名及 PNG、JPEG、WebP、GIF。不会批量抓取整个应用池的图标。

图标采集由 console 服务内独立线程执行，使用独立数据库连接和持久化租约。图标任务统一出现在采集中心，可查看日志、立即采集、暂停、恢复和调整周期（默认168小时）。更新失败保留原缓存。应用图标上的刷新按钮也可请求补采。原始应用信息采集只登记图标地址，尚未请求的图片不会进入下载队列。

顶部控件高度34px；桌面页头约76px，窄屏自动换行。主题面板提供命名预设、大色块选择器、二至三色渐变、角度滑块与方向快捷按钮；预览、取消还原和本地保存行为保持一致。侧栏选中项铺满行，滚动条沿用主题矩形样式。

验证覆盖真实 MySQL 的多商店分页、自动语言、日志诊断、图标缓存与过期租约保护；浏览器验收覆盖单店/全部商店、14个页面、图标显示、主题编辑及390px窄屏。
