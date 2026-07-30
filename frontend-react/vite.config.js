import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// API path prefixes that belong to the backend. Everything else is a client-side
// React route and must NOT be proxied.
//
// The /report entries are split deliberately: "^/report$" and "^/report/" match the
// report ENDPOINTS, while /report-view is a CLIENT-SIDE route (main.jsx renders
// ReportPage for it). A looser "^/report" would swallow /report-view and the backend
// would serve the SPA shell for it instead of React handling the route.
const API_PREFIXES = [
  "^/ask",
  "^/report$",
  "^/report/",
  "^/history",
  "^/schema",
  "^/relationships",
  "^/auth",
  "^/conversations",
  "^/reports/",
];

export default defineConfig(({ mode }) => {
  // vite.config.js runs in Node before the app bundle exists, so `import.meta.env`
  // is not available here — load the .env files explicitly. Third arg "" means
  // "load every key", not just VITE_-prefixed ones.
  const env = loadEnv(mode, process.cwd(), "");

  // Where the dev server forwards API calls. DEV ONLY: this whole `server` block is
  // build tooling and is discarded by `vite build`, so it never reaches production.
  // In production FastAPI serves the SPA and the API from one origin, so the app's
  // relative paths already resolve correctly (see src/config.js).
  const target = env.VITE_DEV_API_TARGET || "http://localhost:8000";

  return {
    plugins: [react()],
    server: {
      port: Number(env.VITE_DEV_PORT) || 5173,
      proxy: Object.fromEntries(API_PREFIXES.map((p) => [p, target])),
    },
  };
});
