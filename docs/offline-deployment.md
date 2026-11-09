# 单机离线部署与备份

当前部署为 OceanBase + SeaweedFS。`scripts/package_deployment.py` 将前后端、OceanBase 和 SeaweedFS 镜像连同 Compose、配置模板和配置生成脚本打包；不包含业务数据、`.env` 或私有 S3 凭据。

解压到新目录，导入 `images.tar`，运行配置生成脚本，然后启动：

```sh
docker load -i images.tar
python3 configure_storage.py
docker compose -f compose.yaml --env-file .env --env-file release.env --profile console --profile keywords up -d
```

配置生成脚本应在发行包根目录执行。首次启动新库，重新初始化管理员。默认单机模式不代表多机高可用；OceanBase 数据和日志使用 Linux 命名卷。镜像为单机快速启动用途，生产集群应使用 OceanBase 正式集群部署流程并完成故障恢复验收。

备份前停止 web、monitor、console、keywords 等全部写入者，再从装有本版本 appmon 的环境执行 `appmon backup <path> --writers-stopped`。只备份文件系统目录无法完整备份对象存储。备份格式 4 同时收录数据库表和当前对象前缀，带 SHA256 校验。

恢复必须使用新的空数据库、空对象前缀和空数据目录。先提供新的 `OCEANBASE_DATABASE` 配置和可访问的 S3 桶，再执行 `appmon restore <backup>`。恢复先校验归档清单，再恢复表和对象，最后校验对象原文；恢复完成后采集保持暂停。拒绝旧存储格式，不合并历史 MySQL 数据。

## 添加对象存储节点

仅在受控私网开放存储端口。主机的 `STORAGE_BIND_ADDRESS` 与 `SEAWEEDFS_ADVERTISE_HOST` 设置为可从其他节点访问的私网地址；主机启动时叠加 `-f compose.storage-network.yaml` 才会发布管理/RPC 端口；默认单机配置只发布 S3。默认回环绑定不会允许跨机器通信。新节点设置 `SEAWEEDFS_MASTERS=<主机私网IP>:9333`、`VOLUME_ADVERTISE_HOST=<本节点私网IP>`、`VOLUME_BIND_ADDRESS=<本节点私网IP>`，执行 `docker compose -f compose.volume.yaml --env-file volume.env up -d`。

这只增加数据节点，不自动让现有数据产生多副本，也不自动让 Master/OceanBase 高可用。需另行配置副本/机架策略并重平衡存量卷，达到足够故障域后再配置纠删码。Master、Filer、Volume 管理/RPC 端口不能暴露到公网；跨不可信网络应先部署认证的私网或 mTLS。
