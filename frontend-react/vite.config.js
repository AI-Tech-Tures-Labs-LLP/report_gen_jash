import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies API calls to the FastAPI backend (running on :8000),
// so the React app can call /ask, /report, /history, etc. with no CORS hassle.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // ONLY proxy the API endpoints to the backend. NOT /report-view — that is a
      // CLIENT-SIDE React route (main.jsx renders ReportPage for it). If it were
      // proxied, the backend would serve the OLD vanilla report.html instead.
      // Use exact-match regexes so /report-view is NOT caught by the /report rule.
      "^/ask": "http://localhost:8000",
      "^/report$": "http://localhost:8000",
      "^/report/": "http://localhost:8000",
      "^/history": "http://localhost:8000",
      "^/schema": "http://localhost:8000",
      "^/relationships": "http://localhost:8000",
      "^/auth": "http://localhost:8000",
      "^/conversations": "http://localhost:8000",
      "^/reports/": "http://localhost:8000",
    },
  },
});
