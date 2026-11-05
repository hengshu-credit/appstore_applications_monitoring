# 衡枢·观星应用情报前端

独立的 Vite / ES Modules 前端项目。Python 后端不包含 HTML、CSS 或前端构建产物；前端只通过 `/api` 访问数据。应用详情、榜单和关键词详情使用 hash 路由，可刷新或前进后退。

## 开发

Node.js 22.12+。在仓库根目录启动 Python API（默认 8765），再在本目录启动前端：

```powershell
npm ci
npm run dev
```

访问 `http://127.0.0.1:5173/`。`vite.config.js` 将 `/api`、`/health` 转发到 `http://127.0.0.1:8765`。不同后端地址可复制 `.env.example` 为 `.env.local` 并修改 `API_PROXY_TARGET`；这个变量仅由开发服务器使用，不向浏览器注入数据库凭据。

```powershell
npm run check
npm test
npm run build
npm run preview
```

`dist/` 可独立部署到静态服务器。生产 Docker 使用本目录的多阶段 Dockerfile：Node 构建，Nginx 运行；Nginx 将 API 转发到独立 Python `web` 服务。开发代理和生产代理保留 Host/Origin，使浏览器始终通过同一来源访问。

## 页面与主题

国家选择支持搜索、热门地区、各洲与亚洲分区；分类采用Google Play/App Store各自的官方目录，并提供应用/游戏选择和应用池数量侧栏。字体离线嵌入阿里妈妈方圆体VF SemiBold（wght=600、BEVL=80）。数据源、历史别名兼容及字体许可见 [目录与字体说明](../docs/store-catalog.md)。

主导航按监控、分析、管理分区，相关页面在工作区页签中访问；入口清单由 `src/navigation.js` 维护。评论记录仅提供采集、查询与历史查看，不提供评论处理或回复流程。应用详情包含地区版本矩阵、资料、评分曲线、版本/素材历史、字段变更和关键词覆盖。

竞品集合、评论处理和ASO草稿均持久化到MySQL。`src/research.js` 封装业务工作流，复用主页面组件、国家/分类选择器和主题tokens；Python对应 `aso.py`、`review_desk.py`、`insights.py`、`collection.py`。

`src/theme.js` 参考天枢 `themeConfig/themeRuntime/ThemeSettingsDrawer` 的配置方式，独立实现 12 个预设、自定义纯色、2–3 色线性/径向渐变、深浅色、导航配色/布局、内容宽度、表格滚动和色弱辅助，并扩展跟随系统模式。预览不持久化，取消还原，保存写入本项目独立的 `appmon.theme.v1`；不读取或改写天枢偏好。此版本是浏览器本地偏好，尚无多用户账户同步。

`src/theme.css` 统一背景、文本、边框、主题、状态、图表变量。所有矩形组件圆角固定为 0；不提供圆角设置。图表跟随当前主题，成功/警告/失败使用独立语义色。自定义色值严格校验，文字色做对比度修正；极端渐变叠加暗层保证前景可读。

页面结构、语义 token 和筛选工具栏规范见 [前端约定](../docs/frontend-guidelines.md)。

## 安全和数据状态

- 上游内容经过 HTML 转义；外部链接只接受 HTTP(S)，新窗口设置 noopener。
- 数据未采集、失败、过期、观察范围内未出现分别展示；不使用伪造数据填补空白。
- 使用账号密码及 HttpOnly、SameSite=Strict 的 8 小时会话登录，修改请求校验 CSRF。首次注册超管，支持外部注册、恢复码找回、普通用户和会员权限；详见 [账户与权限](../docs/accounts-and-permissions.md)。
- 单元测试使用 Happy DOM 验证表单、导航、上游 HTML 注入、错误恢复及主题预览/取消/保存。真实浏览器检查另行记录，不把模拟 DOM 测试当作截图验收。
