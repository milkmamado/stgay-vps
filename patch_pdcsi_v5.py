#!/usr/bin/env python3
"""
PDCSI 안전 격리 패치 v5
- index.html은 단 2줄만 추가 (include 1줄 + mount div 1줄)
- 모든 PDCSI 로직은 templates/pdcsi_widget.html에 격리
- 멱등 (여러 번 실행해도 동일 결과)
- 자동 백업 + 실패 시 안내

사용법:
  scp tmp/patch_pdcsi_v5.py tmp/pdcsi_widget.html root@108.160.132.74:/opt/stock-crawler/
  ssh root@108.160.132.74 'cd /opt/stock-crawler && python3 patch_pdcsi_v5.py && sudo systemctl restart stock-crawler'
"""
import os, re, shutil, sys
from datetime import datetime

TEMPLATE = "templates/index.html"
WIDGET   = "templates/pdcsi_widget.html"
WIDGET_SRC = "pdcsi_widget.html"  # SCP로 같은 폴더에 올린 파일
BACKUP = f"templates/index.html.bak_pdcsi_v5_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

INCLUDE_LINE = "{% include 'pdcsi_widget.html' %}\n"
MOUNT_LINE = "                html += '<div class=\"pdcsi-mount\" data-code=\"' + (item.code || '') + '\"></div>';\n"

def main():
    if not os.path.exists(TEMPLATE):
        print(f"❌ {TEMPLATE} 없음. /opt/stock-crawler에서 실행하세요."); sys.exit(1)
    if not os.path.exists(WIDGET_SRC):
        print(f"❌ {WIDGET_SRC} 없음. SCP로 먼저 올리세요."); sys.exit(1)

    # 1) 위젯 파일 배포
    os.makedirs("templates", exist_ok=True)
    shutil.copy2(WIDGET_SRC, WIDGET)
    print(f"✅ 위젯 배포: {WIDGET}")

    # 2) index.html 백업
    with open(TEMPLATE, "r", encoding="utf-8") as f:
        html = f.read()
    shutil.copy2(TEMPLATE, BACKUP)
    print(f"✅ 백업: {BACKUP}")

    # 3) 기존 PDCSI 흔적이 있으면 작업 거부 (안전)
    if "pdcsi-" in html or "PDCSI_API_URL" in html or "PDCSI_PATCH_START" in html:
        print("❌ index.html에 이전 PDCSI 흔적이 남아있음. 먼저 깨끗한 백업으로 복구하세요.")
        sys.exit(1)

    changed = False

    # 4) include 1줄 추가 (</body> 직전, 멱등)
    if "{% include 'pdcsi_widget.html' %}" not in html:
        idx = html.lower().rfind("</body>")
        if idx == -1:
            print("❌ </body> 못 찾음"); sys.exit(1)
        html = html[:idx] + INCLUDE_LINE + html[idx:]
        print("✅ include 1줄 삽입 (</body> 직전)")
        changed = True
    else:
        print("ℹ️ include 이미 존재")

    # 5) mount div 1줄 추가 (calc-section html += 직전, 멱등)
    if 'class=\\"pdcsi-mount\\"' not in html and 'class="pdcsi-mount"' not in html:
        # 819줄 근처: html += '<div class="calc-section ...
        pat = re.compile(r"(\s*)html\s*\+=\s*['\"`]<div class=\"calc-section", re.MULTILINE)
        m = pat.search(html)
        if not m:
            print("⚠️ calc-section 패턴 못 찾음 — mount 삽입 스킵 (PDCSI는 안 보임, index는 안전)")
        else:
            insert = m.start()
            # 줄 시작으로 정렬
            line_start = html.rfind("\n", 0, insert) + 1
            html = html[:line_start] + MOUNT_LINE + html[line_start:]
            print("✅ mount div 1줄 삽입 (calc-section 직전)")
            changed = True
    else:
        print("ℹ️ mount div 이미 존재")

    if changed:
        with open(TEMPLATE, "w", encoding="utf-8") as f:
            f.write(html)

    print("\n🎉 v5 패치 완료!")
    print(f"롤백:\n  cp {BACKUP} {TEMPLATE} && rm -f {WIDGET} && sudo systemctl restart stock-crawler")

if __name__ == "__main__":
    main()
