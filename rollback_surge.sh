#!/bin/bash
# rollback_surge.sh — 급등 스캐너 패치 롤백
# 사용: bash rollback_surge.sh <TS>   예: bash rollback_surge.sh 20260423-153045
#       bash rollback_surge.sh        ← 가장 최근 백업 자동 사용

set -e
ROOT=/opt/stock-crawler
cd $ROOT

TS=$1
if [ -z "$TS" ]; then
    # 가장 최근 .bak 자동 감지
    TS=$(ls modules/job.py.bak.* 2>/dev/null | sed 's/.*\.bak\.//' | sort -r | head -1)
    if [ -z "$TS" ]; then
        echo "❌ 백업 파일 없음"
        exit 1
    fi
    echo "→ 자동 감지된 가장 최근 백업: $TS"
fi

echo "🔄 롤백 시작 (TS=$TS)"

for f in modules/job.py templates/index.html; do
    BAK="${f}.bak.${TS}"
    if [ -f "$BAK" ]; then
        cp -p "$BAK" "$f"
        echo "  ↩️  복원: $f"
    else
        echo "  ⚠️  백업 없음: $BAK"
    fi
done

# surge_scanner.py 는 신규 파일 → 삭제 (백업 없으면)
if [ -f "modules/surge_scanner.py.bak.${TS}" ]; then
    cp -p "modules/surge_scanner.py.bak.${TS}" modules/surge_scanner.py
    echo "  ↩️  복원: modules/surge_scanner.py"
else
    rm -f modules/surge_scanner.py
    echo "  🗑️  삭제: modules/surge_scanner.py (신규 파일이었음)"
fi

# 서비스 재시작
SERVICE_NAME=$(systemctl list-units --type=service --state=running | grep -iE "stock|stgay|crawler|gunicorn" | awk '{print $1}' | head -1)
if [ -n "$SERVICE_NAME" ]; then
    systemctl restart "$SERVICE_NAME"
    echo "  🔄 $SERVICE_NAME 재시작"
fi

echo "✅ 롤백 완료"
