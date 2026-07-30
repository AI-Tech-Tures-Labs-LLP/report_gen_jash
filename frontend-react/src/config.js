// Frontend build-time configuration.
//
// IMPORTANT: Vite inlines `import.meta.env.VITE_*` into the bundle when
// `npm run build` runs — these are NOT read at runtime. Setting VITE_API_BASE_URL
// in a server's environment after the bundle is built has no effect; it has to be
// present during the build (see .env.example).

/**
 * Origin the API lives on, with no trailing slash.
 *
 * Defaults to "" — every request stays a relative path ("/ask"), which resolves
 * against whatever origin served the page. That is correct for the normal deploy,
 * where FastAPI serves both this SPA and the API from one origin.
 *
 * Set it only when the frontend is served from a DIFFERENT origin than the
 * backend (separate API domain, CDN-hosted frontend, etc.). The backend must then
 * also allow that origin via CORS.
 */
export const API_BASE_URL = String(import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/+$/, "");

/**
 * Resolve an API path against API_BASE_URL.
 * Pass paths with a leading slash: apiUrl("/ask").
 */
export function apiUrl(path) {
  return `${API_BASE_URL}${path}`;
}
