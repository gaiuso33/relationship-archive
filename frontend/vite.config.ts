import react from "@vitejs/plugin-react";
import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";

// The development proxy sends `/api/...` to the local backend, so the browser sees one origin and
// the backend needs no CORS configuration for local work. Production origin and CORS are decided
// with hosting (issue #33).
//
// The target is read from `DEV_API_PROXY_TARGET`. It has no `VITE_` prefix on purpose: Vite only
// embeds `VITE_` variables in the browser bundle, so backend addresses never reach the client.
const DEFAULT_PROXY_TARGET = "http://127.0.0.1:8000";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "DEV_API_");
  const target = env.DEV_API_PROXY_TARGET || DEFAULT_PROXY_TARGET;
  if (!/^https?:\/\//.test(target)) {
    throw new Error("DEV_API_PROXY_TARGET must be an http(s) URL, for example http://127.0.0.1:8000.");
  }
  return {
    plugins: [react()],
    server: { proxy: { "/api": { target, changeOrigin: false } } },
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test/setup.ts"],
    },
  };
});
