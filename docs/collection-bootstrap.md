# 新部署与存量任务接管

## 本轮已实现

- 图标按关注应用与当前榜单 Top50 的去重集合查找候选，范围查询不持有行锁；最终按主键短事务领取，并重新检查暂停、到期时间、来源地址和租约。截图采用相同的两阶段领取方式。
- 图标与截图通过已有 URL 哈希和数据库命名锁共享文件。后续任务可复用自身刷新周期内的文件，批次取最严格周期；手动强制刷新仍要求本次开始后的获取结果。原 `fetched_at`、任务 `checked_at` 和下一次到期时间保留真实采集时间，不用缓存命中时间冒充新观测。
- 同一批次的全池进度只写一次；限速等待每 30 秒采样一次，每个任务仍保留开始、结果、错误及执行编号。每次真实 HTTP 的血缘计数不变。
- `reconcile_collection.py` 覆盖详情、关键词、榜单/评论、权限、图标、截图与 ASO 一次性任务。每批最多 500 个主键回收过期租约，重新校验租约后才更新；只补缺失的 watchlist/jobs 关联，不重置深度、周期、暂停、失败或成功历史。

## 有历史数据的新环境

1. 恢复同一备份时间点的 MySQL 数据和相应文件；保留结构化结果、任务主键、来源/国家/语言、实际观测时间、分页/覆盖范围、采集历史与任务回执。图片仅恢复关注应用及当前 Top50 所需文件，视频保持来源链接。其他应用不额外归档原始响应。
2. 执行正常数据库初始化/迁移，再审查整理预览。不要把“历史成功”更新成部署时刻，也不要通过重新执行启动脚本把所有任务 `due=0` 或统一启用。
3. 接管前保持消费者停止，使用 `--quiescent` 验证任务数量、启用状态、深度与周期汇总稳定。在线整理可省略该参数，报告会明确标记同期任务新增；已有行的业务字段不会被整理操作修改。
4. 整理完成后启动消费者。保留原 due、成功水位与失败退避；新增任务仍走现有唯一键、合并深度与周期以及动态分配逻辑。本轮素材复用遵守上述有效期与强制刷新规则；其他类型仍保留现有提交时效语义，新的提交可能要求提交时刻之后的观测，尚未统一改成命中历史结果即免请求。

已有构建镜像与 `.env` 的 Linux 部署示例：

```bash
docker compose -f compose.yaml --profile console --profile keywords up -d mysql
docker compose -f compose.yaml --profile console --profile keywords run --rm --no-deps -T web appmon init
docker compose -f compose.yaml --profile console --profile keywords run --rm --no-deps -T web python - --quiescent --output data/exports/reconcile-preview.json < scripts/reconcile_collection.py
docker compose -f compose.yaml --profile console --profile keywords run --rm --no-deps -T web python - --quiescent --apply --output data/exports/reconcile-applied.json < scripts/reconcile_collection.py
docker compose -f compose.yaml --profile console --profile keywords up -d web console keywords frontend
```

PowerShell 可将脚本输入改为 `Get-Content scripts/reconcile_collection.py -Raw | docker compose ... run --rm --no-deps -T web python - ...`。整理报告的输出目录会自动创建。`--pool-service-stopped` 是明确停用全池流程的选项，正常接管不要附加。

完全空库需要先导入可用的历史基线。当前种子导入主要包含 ID、名称、分类，不能视作已有完整应用详情。全应用全球多市场首轮必须真实采集缺口；只有具备准确身份、来源、字段与时效证明的历史结果才可以复用。

## 容量与后续边界

2026-11-07 21:25（上海）核查：主采集流共享 5 秒请求间隔；当前已建详情任务按 Apple 50 / Google 20 满批重新采集一轮，理想下界约 203,525 次请求、11.8 天，未计评论、榜单、图片、错误补验及后续全球扩展。50/20 是当前验证过的实现上限，不是承诺任意上游都允许该配额。增加消费者不会突破共享请求额度。

本轮没有改变任务刷新周期和采集深度。未来全局出站请求归并、结构化结果覆盖索引、持久化分页恢复需要独立实现：进程内 RPC 合并不能宣称全局去重；当前分页对象仍在内存，不能把已抓前几页当作整个任务完成。评论增量不能只遇到一个旧 ID 就停止，否则可能漏掉改评或重排。API 304 可减少传输字节，但仍是一笔外部请求。

下一步的核心对象应分离“提交回执、规范化采集目标、一次实际请求、可复用结果”。复用键至少包括数据源/协议、商店、国家、语言、目标、排序/过滤、字段覆盖、深度与新鲜度要求；不同市场不能因 App ID 相同而互相冒充。商业数据授权与额度继续独立核验。
