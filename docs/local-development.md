# 本地开发

先安装 `.[test]`，运行 `python scripts/configure_storage.py`，再执行：

```powershell
docker compose -f compose.dev.yaml --env-file .env --profile console --profile keywords up -d --build
```

后端源文件挂载并由 Uvicorn 重载；前端 Vite 独立运行。数据库为 OceanBase，素材为 SeaweedFS。端口及密钥均从 `.env` 读取。环境变量仅接受 `OCEANBASE_*` 和 `S3_*`，不回退到旧 `MYSQL_*` 配置。

完整后端测试：`python -m unittest discover -s tests -v`。只运行不需要服务的跨平台测试：`python scripts/run_client_tests.py`。

数据不再写入旧 `data/mysql`。旧目录不会自动删除，当前服务也不会读取它。
