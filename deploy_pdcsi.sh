#!/bin/bash
# ─────────────────────────────────────────────────────────────────────
# PDCSI 안전 배포 래퍼 (사전 검증 → 패치 → 헬스체크 → 실패 시 자동 롤백)
# ─────────────────────────────────────────────────────────────────────
# 사용법:
#   cd /opt/stock-crawler
#   bash deploy_pdcsi.sh
#
# 단계:
#   1. 사전 점검: 서비스 상태, 디스크, templates/index.html 존재
#   2. 풀 백업: templates/ 디렉토리 통째로 tar.gz
#   3. patch_pdcsi.py 실행
#   4. systemctl restart stock-crawler
#   5. 헬스체크 (3회 재시도)
#   6. 실패하면 자동 롤백 + tar 복원
# ─────────────────────────────────────────────────────────────────────

set -e

cd "$(dirname "$0")"

SERVICE="stock-crawler"
TEMPLATE="templates/index.html"
HEALTH_URL="http://127.0.0.1:5003/stgay/"
TS=$(date +%Y%m%d_%H%M%S)
FULL_BACKUP="/root/stgay_backups/templates_${TS}.tar.gz"

mkdir -p /root/stgay_backups

echo "════════════════════════════════════════════════════════════"
echo "  PDCSI 안전 배포 시작"
echo "════════════════════════════════════════════════════════════"

# 1) 사전 점검
echo "▶ [1/6] 사전 점검..."
[ -f "$TEMPLATE" ] || { echo "❌ $TEMPLATE 없음"; exit 1; }
[ -f "patch_pdcsi.py" ] || { echo "❌ patch_pdcsi.py 없음 (먼저 SCP로 업로드)"; exit 1; }
systemctl is-active --quiet "$SERVICE" || { echo "❌ $SERVICE 비활성 상태"; exit 1; }
DISK_FREE=$(df -m . | tail -1 | awk '{print $4}')
[ "$DISK_FREE" -gt 100 ] || { echo "❌ 디스크 여유공간 부족 (${DISK_FREE}MB)"; exit 1; }
echo "  ✅ 모든 사전 조건 통과"

# 2) 풀 백업
echo "▶ [2/6] templates/ 디렉토리 풀 백업..."
tar -czf "$FULL_BACKUP" templates/
echo "  ✅ $FULL_BACKUP ($(du -h "$FULL_BACKUP" | cut -f1))"

# 3) 패치 실행
echo "▶ [3/6] PDCSI 패치 실행..."
python3 patch_pdcsi.py || { echo "❌ 패치 실패 — 파일 변경 없음"; exit 1; }

# 4) 서비스 재시작
echo "▶ [4/6] $SERVICE 재시작..."
sudo systemctl restart "$SERVICE"
sleep 3

# 5) 헬스체크 (3회 재시도)
echo "▶ [5/6] 헬스체크..."
OK=0
for i in 1 2 3; do
  HTTP=$(curl -s -o /dev/null -w "%{http_code}" "$HEALTH_URL" || echo "000")
  echo "  시도 $i: HTTP $HTTP"
  if [ "$HTTP" = "200" ] || [ "$HTTP" = "302" ] || [ "$HTTP" = "401" ]; then
    OK=1; break
  fi
  sleep 2
done

if [ "$OK" = "1" ]; then
  echo "  ✅ 서비스 정상"
  echo "▶ [6/6] 완료!"
  echo
  echo "════════════════════════════════════════════════════════════"
  echo "  🎉 배포 성공"
  echo "════════════════════════════════════════════════════════════"
  echo "  풀백업:  $FULL_BACKUP"
  echo "  롤백:    bash rollback_pdcsi.sh"
  echo "  풀복원:  tar -xzf $FULL_BACKUP -C / && systemctl restart $SERVICE"
else
  echo "  ❌ 헬스체크 실패 — 자동 롤백 실행"
  echo "▶ [6/6] 자동 롤백..."
  LATEST_BAK=$(ls -t templates/index.html.bak_pdcsi_* 2>/dev/null | head -n1)
  if [ -n "$LATEST_BAK" ]; then
    cp -p "$LATEST_BAK" "$TEMPLATE"
    sudo systemctl restart "$SERVICE"
    sleep 2
    HTTP=$(curl -s -o /dev/null -w "%{http_code}" "$HEALTH_URL" || echo "000")
    echo "  롤백 후 HTTP: $HTTP"
  else
    echo "  ⚠️  파일 백업 없음 — tar에서 복원:"
    echo "     tar -xzf $FULL_BACKUP -C /opt/stock-crawler/"
  fi
  exit 1
fi
