#!/bin/bash
# deploy_surge.sh — 급등 스캐너 배포 (VPS에서 실행)
# 사용: bash deploy_surge.sh

set -e
ROOT=/opt/stock-crawler
cd $ROOT

echo "========================================"
echo "🚀 급등 테마 대장주 스캐너 배포"
echo "========================================"

# 0) pykrx 확인
echo ""
echo "[0/4] pykrx 설치 확인..."
$ROOT/venv/bin/python -c "import pykrx; print(f'  ✅ pykrx {pykrx.__version__}')" || {
    echo "  ⚠️  pykrx 미설치 — 설치 진행"
    $ROOT/venv/bin/pip install pykrx
}

# 1) 패치 적용
echo ""
echo "[1/4] 패치 적용..."
$ROOT/venv/bin/python patch_surge_v1.py

# 2) 서비스 재시작 (서비스명 확인 필요)
echo ""
echo "[2/4] 서비스 재시작..."
SERVICE_NAME=$(systemctl list-units --type=service --state=running | grep -iE "stock|stgay|crawler|gunicorn" | awk '{print $1}' | head -1)
if [ -z "$SERVICE_NAME" ]; then
    echo "  ⚠️  서비스명 자동 감지 실패. 수동으로:"
    echo "       systemctl restart <your-service-name>"
else
    echo "  → $SERVICE_NAME 재시작"
    systemctl restart "$SERVICE_NAME"
    sleep 2
    systemctl is-active "$SERVICE_NAME" && echo "  ✅ 재시작 OK" || echo "  ❌ 재시작 실패 — journalctl -u $SERVICE_NAME -n 30"
fi

# 3) 헬스체크
echo ""
echo "[3/4] 로컬 헬스체크..."
sleep 1
curl -sf http://localhost:5000/stgay/login > /dev/null && echo "  ✅ Flask 응답 OK" || echo "  ⚠️  Flask 응답 없음 (포트 다를 수 있음)"

# 4) Git push
echo ""
echo "[4/4] Git push (선택)..."
read -p "  GitHub에 commit & push 할까요? [y/N] " yn
if [ "$yn" = "y" ] || [ "$yn" = "Y" ]; then
    git add modules/surge_scanner.py modules/job.py templates/index.html
    git commit -m "feat: 급등 테마 대장주 스캐너 v1 (pykrx + 인포스탁 그룹핑)"
    git push
    echo "  ✅ GitHub 동기화 완료"
fi

echo ""
echo "========================================"
echo "🎉 배포 완료! https://leapblg-6.me/stgay/ 에서 [분석하기] 클릭"
echo "========================================"
