#!/usr/bin/env bash
# 同步代码到服务器并按需执行动作
# 用法: ./deploy/sync.sh [--web|--migrate|--restart|--prod]
set -euo pipefail

SERVER="${LEARNLOOP_SERVER:?Set LEARNLOOP_SERVER to your SSH destination}"
REMOTE_DIR=/opt/learnloop
cd "$(dirname "$0")/.."

rsync -az --delete \
  --exclude .git \
  --exclude node_modules \
  --exclude frontend/dist \
  --exclude .env \
  --exclude backups \
  --exclude instructions.md \
  --exclude __pycache__ \
  --exclude .pytest_cache \
  --exclude .ruff_cache \
  --exclude .DS_Store \
  ./ "$SERVER:$REMOTE_DIR/"

DEV="docker compose -f docker-compose.yml -f docker-compose.dev.yml"

case "${1:-}" in
  --web)
    ssh "$SERVER" "cd $REMOTE_DIR && $DEV run --rm web"
    ;;
  --migrate)
    ssh "$SERVER" "cd $REMOTE_DIR && $DEV exec app alembic upgrade head"
    ;;
  --restart)
    ssh "$SERVER" "cd $REMOTE_DIR && $DEV restart app"
    ;;
  --prod)
    ssh "$SERVER" "cd $REMOTE_DIR && docker compose build && docker compose up -d"
    ;;
  "") ;;
  *)
    echo "未知参数: $1" >&2
    exit 1
    ;;
esac
echo "同步完成 → $SERVER:$REMOTE_DIR ${1:-}"
