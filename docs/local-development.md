# 本地实时开发服务

前端地址为 http://127.0.0.1:5173 ，后端为 http://127.0.0.1:8765 。前端使用 Vite，修改 JS 后自动重新加载页面，CSS 可热更新；后端使用 Uvicorn reload，修改 `src/` 下 Python 后自动重启 API。

切换时只替换前端和 API，MySQL 数据、采集进程、已有端口和账户保持原配置。

```powershell
docker compose --profile console stop frontend web
docker compose -f compose.dev.yaml --profile console up -d --no-deps web frontend
docker compose -f compose.dev.yaml --profile console logs --tail 50 web frontend
```

前端依赖在专用 Docker volume 中安装；首次启动会执行 `npm ci`。新增依赖或修改 lockfile 后，重新启动前端服务安装新依赖。Python 依赖变化仍需要重建后端镜像。

恢复生产预览：

```powershell
docker compose -f compose.dev.yaml --profile console stop frontend web
docker compose --profile console up -d --build --no-deps web frontend
```
