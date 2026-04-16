#!/bin/bash
BACKUP_DIR="/opt/stock-crawler/backups"
APP="/opt/stock-crawler/app.py"

echo "📦 사용 가능한 백업 목록:"
ls -1t "$BACKUP_DIR"/app.py.bak.* 2>/dev/null | head -10 | nl

if [ $# -eq 1 ]; then
  SEL=$1
else
  read -p "복원할 번호 선택 (최신=1): " SEL
fi

FILE=$(ls -1t "$BACKUP_DIR"/app.py.bak.* | sed -n "${SEL}p")

if [ -z "$FILE" ]; then
  echo "❌ 잘못된 선택"
  exit 1
fi

cp "$FILE" "$APP"
systemctl restart stock-crawler
echo "✅ 롤백 완료: $(basename $FILE)"
echo "✅ 서비스 재시작 완료"
