# 控制台与独立前端验证

核查日期：2026-11-03（Asia/Shanghai）。本地验证与远程 CI 分开记录。

## 本地检查

- Python 编译检查通过。
- 原有与新增真实 MySQL 集成/解析测试共 **47 项通过**。新增覆盖：榜单升降和进出范围、失败保留成功数据、租约被替换后的过期结果拒绝、评论编辑历史、重点池地区/暂停、延迟处理的关键词告警、去重、导出筛选/原子发布、广告证据与实际日报幂等导入、API鉴权/Origin/Host保护、竞品市场隔离。
- 更新测试客户端为 Starlette 推荐的 HTTPX2 后，重新运行新增 **12 项测试全部通过**，不再产生旧测试客户端弃用警告。
- 前端 **7 项交互/主题测试通过**：上游HTML转义、表单市场/周期、API失败重试、失败尝试与成功排名区分、主题校验、全部预设深浅色对比度、预览取消与保存。
- `npm run check`、`npm run build`、`npm audit --omit=optional`通过，依赖审计为0项漏洞。
- `docker compose ... config --quiet`通过；Python API和worker镜像已本地构建成功。
- 前端镜像本地构建两次受 Docker Hub Nginx 镜像认证服务连接超时阻断，发生在拉取基础镜像阶段。远程 CI 增加前端镜像构建、Nginx配置验证和Python镜像构建，最终结果以对应提交SHA的工作流为准。

## 真实来源和存储

机器可读证据：[console-live-results.json](console-live-results.json)。

| 来源 | 实际保存 |
|---|---:|
| Google Play 美国 FINANCE 免费榜 | 50条有序应用 |
| Apple 美国 6015 财务免费榜 | 50条有序应用 |
| Google Play ChatGPT最近评论 | 50条 |
| Apple ChatGPT最近评论 | 50条 |
| Apple 美国 iPhone Today | 36张推荐卡片 |
| 双商店 ChatGPT详情 | 2个成功地区详情与素材快照 |

上述是限定目标的实际采集验证，不是全国家、全应用或全历史覆盖。测试样例在重点监控中标为“验证样例”；没有写入伪造下载、收入、活跃或广告数据。

## 浏览器检查

- 实际启动前端5173与Python API8765，确认前端通过开发代理读取真实MySQL数据。
- 概览、应用库、重点监控、榜单、精品推荐、关键词、竞品、评论、市场、广告、告警与日报、导出页面逐页加载成功；无浏览器脚本错误。
- 真实Google榜单详情渲染50条；来源、覆盖范围和时间均可见。
- 深色凝液紫主题预览、保存、刷新保留已验证；顶部导航/浅色导航预览取消后恢复原配置。
- 390px宽度检查 `documentElement.scrollWidth <= clientWidth`，整页无水平溢出；宽表在自己的容器内滚动。
- 矩形卡片、按钮、输入、标签、弹窗和抽屉均为直角；普通图表跟随主题，状态色独立。

截图：[浅色工作台](screenshots/console-light.jpg)、[深色工作台](screenshots/console-dark.jpg)、[窄屏榜单](screenshots/console-mobile.jpg)。

## 远程 CI

仓库要求最终提交SHA的Linux/Windows × Python 3.11/3.12/3.13六项矩阵，以及新增frontend构建/测试/镜像检查全部通过。此文档记录本地事实，提交后的准确SHA与CI链接在最终交付信息提供；不预先填写成功结果。
