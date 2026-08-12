## sqlbot — AI SQL Analyst

A FastAPI app that lets you ask natural-language questions about your PostgreSQL database and get:

- Generated SQL
- Executed query results
- Explanations and insights

### Deployment (Dokploy / Docker Compose)

Two containers, defined in `docker-compose.yml`:

- **`frontend`** — nginx serving the Vite build (`frontend-react/Dockerfile`). It also
  reverse-proxies the API paths to `backend`, so the browser talks to a single
  origin. **Attach the domain to this service, container port 80.**
- **`backend`** — FastAPI/uvicorn on port 7860 (`backend/Dockerfile`), API only.
  Internal to the compose network; it gets no domain of its own.

Postgres is external (RDS/Neon/etc). Copy `.env.example` into the Dokploy
environment and fill in `ANTHROPIC_API_KEY`, `DATABASE_URL`, `APP_DATABASE_URL`,
and `JWT_SECRET`.

### Local development

```bash
# API on :8000
pip install -r backend/requirements.txt
python backend/app.py

# SPA on :5173, proxying API calls to VITE_DEV_API_TARGET (default :8000)
cd frontend-react && npm install && npm run dev
```
