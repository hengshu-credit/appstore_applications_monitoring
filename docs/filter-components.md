# 共享筛选与分页

页面通过 `filterBar` 声明筛选项，使用 `bindFilterBar` 绑定查询。控制器在入口初始化一次，支持页面重新渲染后继续工作。

```js
import { filterBar, bindFilterBar } from "./filter-bar.js";

container.innerHTML = filterBar({
  id: "example-filter",
  values: filters,
  resetValues: { days: 30 },
  fields: [
    { name: "store", label: "应用商店", type: "store" },
    { name: "country", label: "国家 / 地区", type: "country" },
    { name: "language", label: "语言", type: "language" },
    { name: "state", label: "状态", type: "panel", options: [["", "全部"], ["paused", "暂停"]] },
    { name: "q", label: "应用名称", options: apps.map(app => [app.app_id, app.name]) },
  ],
});
bindFilterBar(container.querySelector("form"), async values => {
  filters = values;
  page = 1;
  await render();
}, toast);
```

`store`、`country`、`language` 使用与页头一致的图标、国旗和选择按钮。国家及语言支持搜索，状态使用展开选项面板，官方分类继续引用现有分类面板。文本和数值字段支持候选项，直接 Enter 提交当前输入，方向键选择后 Enter 提交该项。中文输入法确认阶段保留输入行为。应用库支持按开发者和应用名称查询已观察数据的远程建议，250ms防抖，过期响应会被丢弃。手输开发者使用包含匹配，点选开发者使用精确匹配；应用名点选绑定应用 ID。评分继续使用其页面标注的阈值或星级含义。

重置清空输入，选择器恢复默认项，`resetValues` 可配置业务默认值，随后查询首页。标准表单使用 `data-filter-reset` 保存默认配置，复合国家选择可监听 `filter-reset`。建议项缺失或请求失败时仍可输入查询。分页通过 `pagination` 和 `bindPagination` 复用总数、条数、箭头、页码和跳页控件。接口固定页长的列表显示其实际页长，支持可变页长的应用库、分组和采集任务提供切换选项。应用库保留原游标API，同时增加按页查询，通过偏移读取目标页和边界校正处理两家商店长度不等的情况。

采集中心首屏使用轻量流程配置，百万级全池统计单独加载并缓存60秒，缓存时间随统计返回。切页立即更新标题和加载态，迟到的结果按页面版本丢弃。应用档案弹窗固定外框尺寸，内容区滚动。分组配置使用紧凑复选项和统一文件选择控件。

详情采集保留每次响应可解析的公开字段到 `store_fields`，同时提供常用字段的规范化映射。已有分组记录通过 `scripts/enrich_archived_details.py --apply` 从原始归档补全，备份位于 `data/verification/detail-repair/before.jsonl`。本次核验340条当前记录，均通过应用身份校验，补全340条，未发现身份错配。采集回归测试覆盖105个Apple应用分3次请求以及Google批量身份校验、失败重试和暂停租约。
