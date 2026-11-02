import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const target = env.API_PROXY_TARGET || "http://127.0.0.1:8765";
  const proxy = {
    "/api": { target, changeOrigin: false },
    "/health": { target, changeOrigin: false },
  };
  return {
    server: { host: "127.0.0.1", port: 5173, strictPort: true, proxy },
    preview: { host: "127.0.0.1", port: 4173, strictPort: true, proxy },
    build: { outDir: "dist", target: "es2022" },
  };
});
