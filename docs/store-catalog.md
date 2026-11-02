# 国家、商店分类与离线字体

核查日期：2026-11-03。截图只作为交互设计参考，不复制其中的数量或第三方自定义分类。

## 国家和地区

顶部国家字段改为选择面板：中文/英文/ISO代码搜索、热门快捷地区、各洲分组，以及东亚、东南亚、南亚、中亚、西亚筛选。地区名和分区来自 Unicode CLDR 49，采用249个ISO国家/地区条目；热门地区只是快捷入口，不是市场热度排名。国旗使用本地 flag-icons 7.6.0 SVG，无外部图片CDN依赖。

商店请求仍发送原来的小写地区代码，例如 `cn/us/hk/tw/th`。本项目当前 Apple native search 映射支持115个地区，其他地区在 Apple 选择面板中禁用，不静默回退美国。Google地区参数仍只是公开请求上下文，不等于实际用户设备可安装性。

## 官方分类

- Apple 来源：[官方 genre JSON](https://itunes.apple.com/WebObjects/MZStoreServices.woa/ws/genres?id=36) 和 [App Store 分类说明](https://developer.apple.com/app-store/categories/)。采用数字genre ID，例如财务 `6015`、摄影与录像 `6008`、开发者工具 `6026`、图形与设计 `6027`。应用/游戏分开展示，游戏细分保留官方ID。`7003` 当前官方名称是 Casual（休闲），旧 Arcade 数据只作为历史别名匹配。
- Google 来源：[Play Console 官方类别说明](https://support.google.com/googleplay/android-developer/answer/9859673?hl=zh-Hans) 和 [官方表盘分类](https://play.google.com/store/apps/category/WATCH_FACE)。使用 `FINANCE/PHOTOGRAPHY/GAME_ACTION` 等官方ID；Android Wear、Family集合不当作普通行业分类。
- Apple Catalogs `6022` 在历史官方目录仍存在，应用池可查询并明确标为历史分类，新增榜单任务不提供它。Stickers明确标注iMessage。不会把第三方“AI”等专题变成官方应用分类。
- 选择面板支持应用/游戏切换、搜索、总榜/全部；应用池增加可搜索的分类侧栏和真实数量。切换商店时分类重置，避免把Google字符串ID传给Apple接口。

`src/appmon/store_catalog.json` 是后端唯一目录源，前端通过 `/api/catalog` 获取。`scripts/refresh_store_catalog.py` 可以从固定版本CLDR、flag-icons和Apple官方genre响应重建；原始下载缓存在忽略的 `work/catalog-sources/`。更新Apple目录前需移除该目录内的 `apple-genres.json` 缓存并重新核查分类翻译；新增未审核的Apple一级分类会使生成器报错，避免静默省略。目录记录原始包地址和SHA256。

## 历史数据兼容和数量

数据库原始 `apps.category` 历史值保留。显式别名兼容 `finance/Finance/6015`、`photo_and_video/Photo & Video`、`game_puzzle`、Google小写/大写分类等。别名按商店隔离；Apple的普通Music与game_music、Google的Sports与GAME_SPORTS分别处理，避免同名应用/游戏误归类。

新Apple详情同时保存 `primaryGenreId`、`primaryGenreName`、`genreIds` 和 `genres`。应用池的主分类在游戏场景优先使用第一个明确游戏子类；原始完整genre数组仍在快照中，单一主分类不宣称覆盖所有次要分类。

`GET /api/categories/counts?store=apple|google` 对已知池真实行数聚合，返回分类数量、总数、未分类数量和观察时间；缓存5分钟。父游戏数量包含其子类，每个应用只计一次。未知数据保留在“未分类/未映射”，不强行分配。数量按整个商店应用池统计，**不限定顶部国家，不代表当前在架数量**。

数据库初始化幂等添加 `apps_store_category(store,category(64))` 索引，兼容已有库且不重采全部数据。本机真实库验证：Apple财务、Google财经、Apple游戏每次取10条，约0.06–0.16秒；首次分类统计分别约3.36/4.03秒，之后读取缓存。这是本机测量，不是性能保证。

## 离线字体

从天枢 `rule-engine-web/public/fonts/` 原样复制 WOFF2、TTF、许可和使用说明到 `frontend/public/fonts/`。不依赖系统安装字体或远程字体站。CSS指定：

```css
font-family: "AlimamaFangYuanTiVF", "PingFang SC", "Microsoft YaHei", sans-serif;
font-variation-settings: "wght" 600, "BEVL" 80;
```

这两个轴对应字体中的 SemiBold 命名实例。WOFF2预加载并优先使用，TTF备用；正文、标题、表格、表单、弹窗和代码/ID文字统一继承。字体版权与许可见 `frontend/public/fonts/AlimamaFangYuanTiVF-LICENSE.txt`。

文件SHA256：

- WOFF2：`5908e1bfa4ab09364c0c426e3ab5b5dc2ae0319327a7cf4f2230c0d227956643`
- TTF：`32697de724dd9aa631b2312ef89e7885f5fa9ff6ecf04e1e021a0bd4ab988e32`

沿用天枢已有的兼容版本；源项目已记录对官方字体无效可选STAT元数据的修复，本次不再转换或修改字体字形/可变轴。

## 验证

- 前端13项测试：搜索/选择、亚洲分区、Apple不可用地区、商店分类隔离、分类计数、字体二进制的SemiBold轴实例，以及原有交互与主题测试。
- 后端53项测试：别名兼容、同名应用/游戏隔离、数字ID、真实MySQL分类筛选与统计、未知分类、官方榜单输入校验及既有采集回归。
- 真实浏览器字体状态为 `loaded`，`document.fonts.check` 返回true；计算样式的轴为 `"BEVL" 80, "wght" 600`。
- 实测Apple财务筛选结果均显示财务；Google游戏面板展示官方17个游戏子类；商店切换后旧分类清空。
- 390px深色国家面板无整页水平溢出，弹窗圆角为0；已修复新增分类布局在窄屏下的grid最小宽度问题。

截图：[国家面板](screenshots/country-picker.jpg)、[Apple分类](screenshots/apple-category-picker.jpg)、[Google游戏分类](screenshots/google-game-categories.jpg)、[分类数量侧栏](screenshots/category-sidebar.jpg)、[窄屏深色](screenshots/country-picker-mobile-dark.jpg)。远程CI以最终提交SHA的实际结果为准。
