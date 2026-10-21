#!/usr/bin/env bash
# 安装 cron：(crontab -l 2>/dev/null; echo '30 3 * * * /opt/learnloop/deploy/backup.sh') | awk '!seen[$0]++' | crontab -
set -Eeuo pipefail

BACKUP_DIR=/opt/learnloop/backups
BACKUP_FILE="$BACKUP_DIR/learnloop_$(date +%F).sql.gz"
TMP_FILE="$BACKUP_FILE.tmp"
LOG_FILE="$BACKUP_DIR/backup.log"

mkdir -p "$BACKUP_DIR"
touch "$LOG_FILE"
exec >>"$LOG_FILE" 2>&1
trap 'status=$?; rm -f "$TMP_FILE"; echo "[$(date --iso-8601=seconds)] 备份失败，退出码 $status"; exit "$status"' ERR

echo "[$(date --iso-8601=seconds)] 开始备份"
cd /opt/learnloop
docker compose exec -T pg pg_dump -U learnloop learnloop | gzip >"$TMP_FILE"
mv "$TMP_FILE" "$BACKUP_FILE"
find /opt/learnloop/backups -name '*.sql.gz' -mtime +14 -delete
echo "[$(date --iso-8601=seconds)] 备份完成：$BACKUP_FILE"
