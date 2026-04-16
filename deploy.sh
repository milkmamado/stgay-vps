#!/bin/bash
set -e
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
APP="/opt/stock-crawler/app.py"
BACKUP_DIR="/opt/stock-crawler/backups"

mkdir -p "$BACKUP_DIR"

# 백업
cp "$APP" "$BACKUP_DIR/app.py.bak.$TIMESTAMP"
echo "✅ 백업 완료: backups/app.py.bak.$TIMESTAMP"

# git 커밋
cd /opt/stock-crawler
git add -A
git commit -m "배포 전 백업: $TIMESTAMP" --allow-empty

# 서비스 재시작
systemctl restart stock-crawler
echo "✅ 서비스 재시작 완료"
echo "🕊️ 문제 발생 시: bash rollback.sh"
