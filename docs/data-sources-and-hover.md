# 免费数据源配置与跨榜联动

2026-11-04。管理员进入“数据源配置”（`#datasources`），可分别填写 AppGoblin、AppBrain 的邮箱和密码。保存后尝试授权，检查/续期按钮复核会话；获取数据时也会校验会话，失效后尝试重新登录。密码错误和限流会退避，验证码、邮箱验证、二次验证、第三方单点登录需要人工处理。

## 账户与权限

账密及来源会话用 Fernet 加密后存入 MySQL `source_accounts`；API不回显邮箱、密码或Cookie。密钥位于持久卷 `data/secrets/data-sources.key`，首次保存时生成；备份需同时保留数据库与该密钥。丢失密钥不会重新生成并覆盖已有密文。此设置仅管理员可读写，仍受现有登录、CSRF和同源检查保护。认证只发送到固定的来源域名，禁止携带凭据跟随未知域名重定向。

AppGoblin适配按其[开源登录实现](https://github.com/appgoblin-dev/appgoblin/blob/main/frontend/src/routes/auth/login/%2Bpage.server.ts)提交邮箱密码，再通过 `/auth/me` 验证已登录且邮箱已验证。没有把网页会话当作付费API密钥，也不会自动创建API密钥。AppBrain按[官方登录页](https://www.appbrain.com/login)实际返回的POST表单识别邮箱、密码与隐藏字段；仅允许 `www.appbrain.com` 和 `developers.appbrain.com`，授权成功需要账户页返回退出登录入口。

真实账户账密尚未提供给此服务，因此真实账户授权、免费目录下载权限尚待用户在配置页保存后验证。当前已有契约、加密、域名限制、权限与退避测试，不把模拟授权测试称为真实登录成功。

## 数据来源实测与兜底

| 来源 | 已确认可用范围 | 实测与限制 |
|---|---|---|
| [AppGoblin公开页](https://appgoblin.info/apps/com.openai.chatgpt) | 全球安装指标、月活/月收入估算 | 已匿名取得ChatGPT Android/iOS快照，保留K/M/B及`$10M+`原显示值；iOS安装为来源估算 |
| [AppGoblin免费数据](https://appgoblin.info/free-app-datasets) | 免费账户可下载元数据、描述、生态报告 | 页面展示的应用指标文件列不含国家下载/收入；配置支持授权后读取目录，不能据此宣称已获取国家拆分 |
| [AppBrain](https://www.appbrain.com/app/chatgpt/com.openai.chatgpt) | Android安装量与页面可见的近月下载 | 官网网页可查；本机匿名HTTP返回403。已加邮箱密码配置与实际表单适配，登录挑战会明确显示，未绕过 |
| Google Play/Apple公开观察 | 各国家榜单、搜索结果、应用资料；Google全球累计安装 | 已有采集器。自动兜底采用最近成功Google安装快照，并保留原观察时间；缺失收入不补值 |
| [Appfigures](https://appfigures.com/top-apps) | 公开各国榜单；官方说明提供上月全球下载/收入估算 | 已读取公开榜单及应用页；本次未取得应用估算数值接口，列为候选，未宣称自动兜底已接通 |
| [Google Trends RSS](https://trends.google.com/trending/rss?geo=US) | 国家网页搜索热点 | 美国、泰国各10条已保存；日本曾取得RSS但后续连接不稳定。不是Google Play/App Store官方热搜 |
| [app-store-operator](https://github.com/meyusufdemirci/app-store-operator) | 开源客户端 | 下载收入工具依赖SensorTower登录，不满足匿名条件；未执行其登录或购买流程 |

自动应用快照顺序为：AppGoblin已配置账户 → AppGoblin公开页 → 已配置AppBrain（Android）→ 已采集Google Play累计安装快照。也可以指定来源单独查询。每次只返回该来源实际存在的字段，保留来源链接、获取时间与指标范围。当前不保证完整分国家下载、收入或完整商店官方热搜；免费账户与公开资料均需按实际字段核实。

匿名采集脚本：`python scripts/collect_public_sources.py --app google:com.openai.chatgpt --app apple:6448311069 --country US --country TH`。可按网络需要提供 `--proxy`。输出在 `data/public-sources/`，JSON带原始响应SHA256；匿名原始响应单独保存，登录后的页面只保存解析指标与摘要哈希，不落盘完整账户页面。TLS证书校验保持开启。

## 全球排行榜 hover

悬停或键盘聚焦应用，按“商店 + 应用ID”高亮当前页面全部同一应用；同名不同ID或不同商店不会误合并。浮层查询全部所选国家，不只当前6国：逐榜显示Top N覆盖国家、有效观察国家、Top1/3/10、最佳/中位名次、部分国家名次，以及缺失/过期/不支持/历史不足/返回不足数量。

最新查询仅统计启用、未失败且未过期的有效快照；历史查询以所选时点的快照为准。缺失和返回不足不视为未上榜。派生榜明确标注。请求防抖、同页应用缓存、过期响应保护与Escape关闭已覆盖测试；真实本地250国家三榜汇总样例耗时约0.33秒（仅代表本机现有数据规模）。

本轮本地验证：46项前端测试、11项相关后端测试通过，前后端镜像构建和服务健康检查通过。最终浏览器实屏复核未完成：浏览器连接多次超时，未把DOM测试当作实屏检查。两家真实账户尚未在观星配置，授权测试使用受控响应验证流程与保密性。
