# 市场数据采集与归档

通过数据接入面板保存加密账户或 Personal Access Token，建立按国家、分类、应用和历史日期划分的采集计划。原始响应保留缺失值和估算属性，支持分页、断点续采、限额退避、截止时间和 CSV 导入。

## 私有部署配置

连接地址与允许的认证域名由服务器 `data/secrets/source-profiles.json` 提供，不随代码分发、不返回界面。示例结构如下（示例地址不能用于真实采集）：

```json
{
  "community": {"origin": "https://metadata.example", "hosts": ["metadata.example"]},
  "directory": {"origin": "https://directory.example", "hosts": ["directory.example"], "account_path": "/account"},
  "marketdata": {"origin": "https://market.example/v2", "hosts": ["market.example"]},
  "web_feed": {"origin": "https://feed.example/rss", "hosts": ["feed.example"]}
}
```

凭据仍使用 `data/secrets/data-sources.key` 加密存储。迁移时须同时携带密钥、私有连接配置与原始归档目录；普通源码发布不包含这些文件。

## 采集与恢复

采集范围取决于授权和额度，账户授权有效不代表所有接口均可访问。请求次数、数据积分和权限是独立限制。遇到 401 暂停计划，402/403 熔断相应类型，420/429 等待额度或冷却恢复；不会自动购买额度。

目录、榜单、应用元数据、下载/收入估算、历史排名、评论分别归档。历史数据按 31 天分段；评论超过分页上限时细分日期。新发现应用按计划去重，榜单翻页固定同一快照时间，未访问目标不会因每日刷新被丢弃。

`marketdata_runs/jobs/products/blocks/budget/artifacts` 保存计划及进度。原始文件位于 `data/public-sources/marketdata/`，SHA256 对解压后原始内容计算。记录数为单份响应的记录数，不冒充跨文件去重应用数。数据来源属性保持为外部估算或导入观察，不改称官方实测。

```powershell
.\.venv\Scripts\python.exe -m appmon marketdata status
.\.venv\Scripts\python.exe -m appmon marketdata plan --file examples/market-data-plan.json
.\.venv\Scripts\python.exe -m appmon marketdata run --limit 5
.\.venv\Scripts\python.exe -m appmon marketdata import-csv --file path\to\report.csv
```

后台由 `console daemon` 执行，并遵守共享采集暂停开关。截止时间必须按实际授权期限填写。CSV 完整保留原始列和行数，不猜测币种或日期口径。

## 已有环境的标识迁移

已有环境更新到中性标识版本前，停止本地 API 与采集写入进程，保留原始数据目录。运行 `scripts/migrate_source_profiles.py`，传入服务器私有迁移清单：`tables` 为旧表名到当前表名的映射，`providers` 为账户标识映射，`asset_folders` 为数据目录内的归档目录映射，`field_updates` 指定需调整的展示元数据。清单不随源码分发。

工具保留迁移前接入记录，拒绝覆盖已存在的目标表、账户或目录；迁移后逐表核对记录数并验证所有市场数据归档的 SHA256。密码和 Token 的加密内容不变。关键词来源标识同步重算，保持再次导入的幂等性。完成核验后再启动新版服务。
