# License artifacts and the exact allowlisted source used by this build.
FROM python:3.12-slim AS legal
WORKDIR /source
COPY . ./
RUN python tools/package_source.py /source /legal

# ---- 前端构建 ----
# Standard upstream images; configure a regional mirror locally if required.
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json frontend/.npmrc ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- 后端运行 ----
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai
WORKDIR /srv/backend
COPY backend/requirements.txt backend/requirements-dev.txt backend/constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-dev.txt
COPY backend/ ./
COPY --from=web /web/dist /srv/static
COPY --from=legal /legal /srv/legal
ENV STATIC_DIR=/srv/static LEGAL_DIR=/srv/legal
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
