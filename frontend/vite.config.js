import { defineConfig, loadEnv } from "vite";
import { fileURLToPath } from "node:url";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const target = env.API_PROXY_TARGET || "http://127.0.0.1:8765";
  const proxy = {
    "/api": {
      target,
      changeOrigin: false,
      bypass(req) {
        if (["/api/docs", "/api/docs/"].includes(req.url?.split("?")[0]))
          return "/api-docs.html";
      },
    },
    "/health": { target, changeOrigin: false },
  };
  return {
    server: {
      host: "127.0.0.1",
      port: 5173,
      strictPort: true,
      proxy,
      watch: { usePolling: env.DEV_POLLING === "true", interval: 400 },
    },
    preview: { host: "127.0.0.1", port: 4173, strictPort: true, proxy },
    build: {
      outDir: "dist",
      target: "es2022",
      rolldownOptions: {
        input: {
          main: fileURLToPath(new URL("./index.html", import.meta.url)),
          docs: fileURLToPath(new URL("./api-docs.html", import.meta.url)),
        },
      },
    },
  };
});
