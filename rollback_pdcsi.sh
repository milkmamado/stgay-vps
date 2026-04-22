#!/bin/bash
# ─────────────────────────────────────────────────────────────────────
# PDCSI 패치 원클릭 롤백 스크립트
# ─────────────────────────────────────────────────────────────────────
# 사용법:
#   cd /opt/stock-crawler
#   bash rollback_pdcsi.sh              # 가장 최근 백업으로 롤백
#   bash rollback_pdcsi.sh <백업파일>    # 특정 백업으로 롤백
#
# 동작:
#   1. templates/index.html.bak_pdcsi_* 중 가장 최신 백업 자동 탐색
#   2. 현재 파일을 .pre_rollback_* 로 한 번 더 보존 (롤백의 롤백 가능)
#   3. 백업 → templates/index.html 복원
#   4. systemctl restart stock-crawler
#   5. curl로 200 확인
# ─────────────────────────────────────────────────────────────────────

set -e

cd "$(dirname "$0")"

TEMPLATE="templates/index.html"
SERVICE="stock-crawler"
HEALTH_URL="http://127.0.0.1:5003/stgay/"   # STGAY Flask (302 로그인 리다이렉트가 정상)

# 1) 백업 파일 결정
if [ -n "$1" ]; then
  BACKUP="$1"
else
  BACKUP=$(ls -t templates/index.html.bak_pdcsi_* 2>/dev/null | head -n1 || true)
fi

if [ -z "$BACKUP" ] || [ ! -f "$BACKUP" ]; then
  echo "❌ 백업 파일을 찾을 수 없습니다."
  echo "   templates/index.html.bak_pdcsi_* 패턴이 존재해야 합니다."
  exit 1
fi

echo "🔍 롤백 대상 백업: $BACKUP"
echo "   크기: $(wc -c < "$BACKUP") bytes"
echo "   수정: $(stat -c '%y' "$BACKUP")"
read -p "이 백업으로 롤백할까요? (y/N): " ans
[ "$ans" = "y" ] || { echo "취소."; exit 0; }

# 2) 현재 상태 보존 (롤백 후 다시 패치 상태로 돌아갈 수 있게)
SAFE="${TEMPLATE}.pre_rollback_$(date +%Y%m%d_%H%M%S)"
cp -p "$TEMPLATE" "$SAFE"
echo "✅ 현재 파일 보존: $SAFE"

# 3) 복원
cp -p "$BACKUP" "$TEMPLATE"
echo "✅ 복원 완료: $BACKUP → $TEMPLATE"

# 4) 서비스 재시작
echo "🔄 $SERVICE 재시작 중..."
sudo systemctl restart "$SERVICE"
sleep 2

# 5) 헬스체크
echo "🩺 헬스체크: $HEALTH_URL"
HTTP=$(curl -s -o /dev/null -w "%{http_code}" "$HEALTH_URL" || echo "000")
if [ "$HTTP" = "200" ] || [ "$HTTP" = "302" ] || [ "$HTTP" = "401" ]; then
  echo "✅ 서비스 정상 응답 ($HTTP)"
else
  echo "⚠️  비정상 응답 ($HTTP) — journalctl -u $SERVICE -n 50 확인 필요"
fi

echo
echo "🎉 롤백 완료!"
echo "   되돌리기(다시 패치 상태로): cp $SAFE $TEMPLATE && sudo systemctl restart $SERVICE"
