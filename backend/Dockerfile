FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (if needed, extend this)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code. Build context = repo ROOT, so we get backend/ + frontend/.
#   backend/ -> /app/backend  (the python app, run with this as the import root)
#   frontend/ -> /app/frontend (served via FRONTEND_DIR = ../frontend relative to backend/)
COPY backend/ ./backend/
COPY frontend/ ./frontend/

ENV PYTHONUNBUFFERED=1

# Hugging Face Spaces pass the port via the PORT env var
ENV PORT=7860
EXPOSE 7860

# Run uvicorn with backend/ as the app-dir so `import config` / `from ai.x` resolve (Option A).
CMD ["sh", "-c", "uvicorn app:app --app-dir backend --host 0.0.0.0 --port ${PORT}"]
