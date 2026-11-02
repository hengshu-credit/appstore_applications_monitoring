> 本文为首次 SQLite 基线落地的历史验收记录。当前存储已升级为 MySQL，最新结果见 `mysql-verification.md` 和 `mysql-migration.json`；本文的旧库路径、存储实现和测试数量不代表当前状态。

# 本次落地与验收

验收日期：2026-11-02（Asia/Shanghai）。以下区分已执行、未执行和持续运行边界。

## 实际完成的数据处理

- 三份 XZ 文件均已完整遍历，合计 11,645,149 个源记录；不是抽样统计。
- 主 ID 文件：5,440,809 行全部有效，拒绝 0 行，导入状态 complete。
- 名称/类别补充文件：3,102,170 行全部有效，拒绝 0 行，导入状态 complete。
- 指标文件：3,102,170 行已核对并完整保存在 `data/raw/`，没有混入实时快照。
- 合并历史包、补充包及本次实时发现后，去重应用池 **5,440,876** 个：Google **3,044,767**，Apple **2,396,109**。
- US/en 队列 **5,440,876** 个，等于池总数；有名称的应用 **3,102,235** 个。
- SQLite `PRAGMA quick_check` 返回 `ok`，checkpoint 后主库大小 **1,243,664,384 字节**。
- 每份文件 SHA256、Git blob SHA1、精确行数、各商店行数和采集时间范围都在 `data-manifest.json`；可运行 `python scripts/verify_data.py` 再校验。

注意：两份输入导入时，实时发现和示例 ID 也在入库。因此 `imports.added` 是“该次操作真正新增多少 ID”，不等于输入行数。两份输入共享大量应用，不能把它们相加当作应用池大小。补充后多出的 67 个 ID 也不应一律叫“新上架”。

## 真实商店验证

详情成功：Apple `310633997`（WhatsApp）、`544007664`（YouTube）；Google `com.whatsapp`、`com.google.android.youtube`。四条有效详情、四条首次验证事件、四个历史快照，原始响应已 gzip 保存。

两次最初的 Google TLS 失败仍保留在请求审计中。定位为当前代理路径的 TLS 兼容问题，启用 TLS 1.2 上限且保持证书验证后重试成功；当前这两个任务的失败计数已清零。没有删掉错误日志来制造成功。

| 发现源 | 实际返回 |
|---|---:|
| Apple US 免费榜 | 100 |
| Apple US 付费榜 | 100 |
| Google US FINANCE 分类页 | 33 |
| Apple US finance 搜索 | 186 |
| Google US finance 搜索 | 29 |
| Google 开发者 WhatsApp LLC | 2 |
| Google 数字开发者 ID 5700313618786177705 | 11 |
| Apple 开发者 281956209 | 75 |
| Apple 开发者 310634000 | 2 |

发现数字是各入口返回的条数，集合存在交集，不可相加视为新增数。Google 文本开发者 ID 初次出现双重 URL 编码导致入口不可用，修正解码后实测成功；原失败记录保留。

**实时详情只验证了 4 个示例应用；其余 ID 已入队，尚未完成全池详情轮询。没有证明所有应用当前上架，也没有完成每日全球全量扫描。** 当前没有启动长期 daemon、Windows 计划任务或 Docker 容器；运行方式见 README。

## 本地代码验证

在 Windows / Python 3.11.5 上执行：

- 13 项离线测试：导入校验/重复去重、事务断点恢复、错误表头、租约回收和旧 worker 拒绝、变更/缺失/恢复状态机、地区隔离、跨连接限速、历史导入不覆盖实时名称、失败任务退避、Apple schema 错误/正常空结果、Google 解析失败、开发者 ID 编码。
- `compileall` 编译检查。
- editable 安装和 wheel 构建。
- `docker compose -f compose.yaml config --quiet` 配置检查。
- 真实来源的 API/网页烟雾验证，结果见上表和 `validation-results.json`。

未执行 Docker 镜像构建/容器启动、Linux 实机验证、长期稳定性/负载测试、百万级详情轮询，也未接入外部告警平台。

## Git / 远程 CI

本地仓库已初始化为 `main`。当前没有 remote，没有创建提交，没有 push。最终提交 SHA：**不存在**；远程 CI 链接：**不存在**；远程 CI 状态：**尚未触发，不能宣称通过**。

已提供 GitHub Actions 六项矩阵（Linux/Windows × Python 3.11/3.12/3.13）。后续指定远程仓库并提交后，必须等待最终 SHA 的全部已触发工作流、矩阵任务和必需检查通过，才能完成代码提交交付。
