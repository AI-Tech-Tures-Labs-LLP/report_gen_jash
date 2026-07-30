# ── Stage 1: build the React frontend ───────────────────────────────────────
# Vite emits static assets, so Node is only needed at build time — the runtime
# image below stays Python-only.
FROM node:20-slim AS frontend-build

WORKDIR /build

# Install deps first so this layer caches until the lockfile actually changes.
COPY frontend-react/package.json frontend-react/package-lock.json ./
RUN npm ci

COPY frontend-react/ ./
RUN npm run build


# ── Stage 2: runtime ────────────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (if needed, extend this)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code. Build context = repo ROOT.
#   backend/ -> /app/backend  (the python app, run with this as the import root)
COPY backend/ ./backend/

# The built SPA from stage 1. FRONTEND_DIR below points api/frontend.py here,
# so only the build output ships — no node_modules, no source.
COPY --from=frontend-build /build/dist ./frontend-react/dist

ENV PYTHONUNBUFFERED=1
ENV FRONTEND_DIR=/app/frontend-react/dist

# Hugging Face Spaces pass the port via the PORT env var
ENV PORT=7860
EXPOSE 7860

# Run uvicorn with backend/ as the app-dir so `import config` / `from ai.x` resolve (Option A).
CMD ["sh", "-c", "uvicorn app:app --app-dir backend --host 0.0.0.0 --port ${PORT}"]
