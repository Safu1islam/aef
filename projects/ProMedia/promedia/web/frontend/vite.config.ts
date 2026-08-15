import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// Served from FastAPI under /studio (promedia/web/app.py). base must match
// that mount point so built asset URLs resolve, in dev and in the built
// bundle alike.
export default defineConfig({
  base: "/studio/",
  plugins: [vue()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    // Dev-server convenience only: proxies API calls to the real FastAPI
    // process so `npm run dev` can run against real data without a second
    // CORS/cookie story. The operator-token cookie is scoped to the FastAPI
    // origin, so this keeps the browser's view same-origin.
    proxy: {
      "/api": "http://127.0.0.1:8765",
    },
  },
});
