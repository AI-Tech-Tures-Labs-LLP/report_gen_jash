# API image — Python only.
#
# The SPA is NOT built or served here: frontend-react/Dockerfile produces an
# nginx image that serves the Vite build and reverse-proxies the API paths to
# this container (see docker-compose.yml).
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

ENV PYTHONUNBUFFERED=1

# Overridable so the port is set in one place (docker-compose.yml).
ENV PORT=7860
EXPOSE 7860

# Run uvicorn with backend/ as the app-dir so `import config` / `from ai.x` resolve (Option A).
CMD ["sh", "-c", "uvicorn app:app --app-dir backend --host 0.0.0.0 --port ${PORT}"]
