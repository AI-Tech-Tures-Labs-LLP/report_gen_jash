# SPA image: build with Vite, serve the static output with nginx.
#
# Build context is the repo ROOT (same as backend/Dockerfile), so paths below are
# prefixed with frontend-react/. Keeping one context means one .dockerignore and
# one Dokploy build setting for both services.
#
# nginx also reverse-proxies the API prefixes to the backend container, so the
# browser only ever talks to one origin — no CORS, and VITE_API_BASE_URL stays
# empty (relative paths, see src/config.js).

# ── Stage 1: build ──────────────────────────────────────────────────────────
FROM node:20-slim AS build

WORKDIR /build

# Deps first so this layer caches until the lockfile changes.
COPY frontend-react/package.json frontend-react/package-lock.json ./
RUN npm ci

COPY frontend-react/ ./
RUN npm run build


# ── Stage 2: serve ──────────────────────────────────────────────────────────
FROM nginx:1.27-alpine

# Only the build output ships — no node_modules, no source.
COPY --from=build /build/dist /usr/share/nginx/html

# Files in /etc/nginx/templates are run through envsubst at container start, so
# BACKEND_ORIGIN/PORT are resolved at RUNTIME (unlike VITE_* vars, which are
# baked in at build time). Only the listed vars are substituted — nginx's own
# $uri, $host, etc. are left alone.
COPY frontend-react/nginx.conf.template /etc/nginx/templates/default.conf.template

ENV NGINX_ENVSUBST_TEMPLATE_SUFFIX=.template
ENV NGINX_ENVSUBST_FILTER=BACKEND_ORIGIN|PORT
ENV BACKEND_ORIGIN=http://backend:7860
ENV PORT=80

EXPOSE 80
