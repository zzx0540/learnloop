# ---- 前端构建 ----
# 基础镜像走 DaoCloud 镜像源（服务器直连 Docker Hub 超时）
FROM docker.m.daocloud.io/library/node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json frontend/.npmrc ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- 后端运行 ----
FROM docker.m.daocloud.io/library/python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
WORKDIR /srv/backend
COPY backend/requirements.txt backend/requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-dev.txt
COPY backend/ ./
COPY --from=web /web/dist /srv/static
ENV STATIC_DIR=/srv/static
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
