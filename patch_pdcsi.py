#!/usr/bin/env python3
"""
PDCSI (개돼지 지수) 섹션을 STGAY templates/index.html에 자동 삽입하는 패치 스크립트 v4.

v4 변경점:
  - CSS를 `</head>` 직전에 별도 `<style>` 태그로 삽입하여 JS 문자열 오염 방지
  - JS를 `</body>` 직전에 별도 `<script>` 태그로 삽입하여 기존 스크립트 문자열과 분리
  - 기존/깨진 PDCSI 흔적(CSS, JS, HTML 블록)을 먼저 정리한 뒤 다시 삽입
  - 여러 번 실행해도 동일 결과가 나오도록 멱등 처리

사용법:
  scp tmp/patch_pdcsi.py root@108.160.132.74:/opt/stock-crawler/
  cd /opt/stock-crawler && python3 patch_pdcsi.py && sudo systemctl restart stock-crawler
"""

import os
import re
import shutil
import sys
from datetime import datetime

TEMPLATE_PATH = "templates/index.html"
BACKUP_PATH = f"templates/index.html.bak_pdcsi_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

PDCSI_URL = "https://lbpkxtvxmwgxlklprhuv.supabase.co/functions/v1/pdcsi-analyze"
SUPABASE_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImxicGt4dHZ4bXdneGxrbHByaHV2Iiwicm9sZSI6ImFub24iLCJp"
    "YXQiOjE3NzYyNTMzMTAsImV4cCI6MjA5MTgyOTMxMH0.XHjyPztUnrSOil482WPkfVz7IKXtgS3KmaX3a27KtgM"
)

CSS_BLOCK = """
<style>
/* PDCSI_PATCH_START */
/* ─── PDCSI (개돼지 지수) 섹션 ─── */
.pdcsi-section { margin-top: 12px; border: 1px solid rgba(168, 85, 247, 0.3); border-radius: 10px; background: linear-gradient(135deg, rgba(168, 85, 247, 0.05), rgba(236, 72, 153, 0.05)); overflow: hidden; }
.pdcsi-toggle { width: 100%; padding: 10px 14px; background: transparent; border: none; color: #c084fc; font-weight: 700; font-size: 13px; text-align: left; cursor: pointer; display: flex; justify-content: space-between; align-items: center; }
.pdcsi-toggle:hover { background: rgba(168, 85, 247, 0.08); }
.pdcsi-toggle .pdcsi-arrow { transition: transform 0.2s; }
.pdcsi-toggle.open .pdcsi-arrow { transform: rotate(90deg); }
.pdcsi-body { display: none; padding: 14px; border-top: 1px solid rgba(168, 85, 247, 0.2); background: rgba(15, 15, 25, 0.4); }
.pdcsi-body.open { display: block; }
.pdcsi-loading { text-align: center; padding: 16px; color: #94a3b8; font-size: 12px; }
.pdcsi-error { color: #f87171; font-size: 12px; padding: 8px; }
.pdcsi-score-row { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; flex-wrap: wrap; }
.pdcsi-score-big { font-size: 32px; font-weight: 900; color: #fff; line-height: 1; }
.pdcsi-score-adj { font-size: 11px; color: #94a3b8; }
.pdcsi-verdict { padding: 4px 10px; border-radius: 999px; font-size: 11px; font-weight: 700; }
.pdcsi-verdict.buy { background: rgba(34, 197, 94, 0.2); color: #4ade80; }
.pdcsi-verdict.warn { background: rgba(251, 191, 36, 0.2); color: #fbbf24; }
.pdcsi-verdict.exit { background: rgba(239, 68, 68, 0.2); color: #f87171; }
.pdcsi-verdict.wait { background: rgba(148, 163, 184, 0.2); color: #cbd5e1; }
.pdcsi-bars { display: grid; grid-template-columns: repeat(2, 1fr); gap: 6px; margin-bottom: 12px; }
.pdcsi-bar-row { display: flex; align-items: center; gap: 6px; font-size: 11px; }
.pdcsi-bar-label { width: 44px; color: #94a3b8; flex-shrink: 0; }
.pdcsi-bar-track { flex: 1; height: 6px; background: rgba(255,255,255,0.05); border-radius: 3px; overflow: hidden; }
.pdcsi-bar-fill { height: 100%; border-radius: 3px; transition: width 0.4s; }
.pdcsi-bar-fill.euphoria { background: linear-gradient(90deg, #fbbf24, #f59e0b); }
.pdcsi-bar-fill.panic { background: linear-gradient(90deg, #60a5fa, #3b82f6); }
.pdcsi-bar-fill.distrust { background: linear-gradient(90deg, #f87171, #ef4444); }
.pdcsi-bar-fill.shilling { background: linear-gradient(90deg, #c084fc, #a855f7); }
.pdcsi-bar-val { width: 28px; text-align: right; color: #cbd5e1; font-weight: 600; flex-shrink: 0; }
.pdcsi-academic { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; margin-bottom: 10px; }
.pdcsi-acad-card { padding: 6px 8px; background: rgba(255,255,255,0.03); border-radius: 6px; border: 1px solid rgba(255,255,255,0.05); }
.pdcsi-acad-label { font-size: 9px; color: #64748b; margin-bottom: 2px; text-transform: uppercase; letter-spacing: 0.5px; }
.pdcsi-acad-val { font-size: 13px; font-weight: 700; color: #e2e8f0; }
.pdcsi-acad-tag { display: inline-block; margin-top: 2px; padding: 1px 6px; border-radius: 4px; font-size: 9px; background: rgba(255,255,255,0.05); color: #94a3b8; }
.pdcsi-meta { font-size: 10px; color: #64748b; line-height: 1.5; padding-top: 8px; border-top: 1px solid rgba(255,255,255,0.05); }
.pdcsi-meta strong { color: #c084fc; }
.pdcsi-refresh { background: transparent; border: 1px solid rgba(168, 85, 247, 0.3); color: #c084fc; padding: 3px 8px; border-radius: 6px; font-size: 10px; cursor: pointer; margin-left: 6px; }
.pdcsi-refresh:hover { background: rgba(168, 85, 247, 0.1); }
/* PDCSI_PATCH_END */
</style>
"""

JS_BLOCK_TEMPLATE = r"""
<script>
// PDCSI_PATCH_START
// ─── PDCSI (개돼지 지수) 함수 ───
const PDCSI_API_URL = "__PDCSI_URL__";
const PDCSI_ANON_KEY = "__PDCSI_KEY__";

function togglePdcsi(code) {
  const btn = document.getElementById('pdcsi-toggle-' + code);
  const body = document.getElementById('pdcsi-body-' + code);
  if (!btn || !body) return;
  const isOpen = body.classList.toggle('open');
  btn.classList.toggle('open');
  if (isOpen && !body.dataset.loaded) loadPdcsi(code);
}

async function loadPdcsi(code, forceRefresh) {
  const body = document.getElementById('pdcsi-body-' + code);
  if (!body) return;
  body.innerHTML = '<div class="pdcsi-loading">📡 종토방 분석 중... (10~30초)</div>';
  try {
    const resp = await fetch(PDCSI_API_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + PDCSI_ANON_KEY, 'apikey': PDCSI_ANON_KEY },
      body: JSON.stringify({ stock_code: code, force_refresh: !!forceRefresh })
    });
    if (!resp.ok) {
      const errText = await resp.text();
      body.innerHTML = '<div class="pdcsi-error">❌ 분석 실패 (' + resp.status + '): ' + errText.slice(0, 200) + '</div>';
      return;
    }
    const data = await resp.json();
    body.dataset.loaded = '1';
    renderPdcsi(body, data, code);
  } catch (e) {
    body.innerHTML = '<div class="pdcsi-error">❌ 네트워크 오류: ' + (e.message || e) + '</div>';
  }
}

function renderPdcsi(container, d, code) {
  const verdict = (d.verdict || 'WAIT').toUpperCase();
  let verdictClass = 'wait';
  if (verdict.indexOf('BUY') >= 0) verdictClass = 'buy';
  else if (verdict.indexOf('EXIT') >= 0 || verdict.indexOf('AVOID') >= 0) verdictClass = 'exit';
  else if (verdict.indexOf('WATCH') >= 0 || verdict.indexOf('PARTIAL') >= 0) verdictClass = 'warn';
  const acad = d.academic || {};
  const mom = acad.sentiment_momentum || {};
  const adjustedShown = (typeof d.pdcsi_adjusted === 'number' && d.pdcsi_adjusted !== d.pdcsi);
  const cachedTag = d._cached ? '· 캐시' : '';
  const euphoria = d.euphoria || 0;
  const panic = d.panic || 0;
  const distrust = d.distrust || 0;
  const shilling = d.shilling || 0;
  const dis = (acad.disagreement_index != null) ? acad.disagreement_index : '-';
  const disLabel = acad.disagreement_label || '-';
  const volZ = (acad.abnormal_volume_z != null) ? acad.abnormal_volume_z : '-';
  const volLabel = acad.abnormal_volume_label || '-';
  const delta = mom.pdcsi_delta;
  const deltaStr = (delta != null) ? ((delta > 0 ? '+' : '') + delta) : '-';
  const momLabel = mom.momentum_label || '데이터부족';

  let h = '';
  h += '<div class="pdcsi-score-row">';
  h += '<div class="pdcsi-score-big">' + (d.pdcsi != null ? d.pdcsi : '-') + '</div>';
  if (adjustedShown) h += '<div class="pdcsi-score-adj">학술보정: <strong>' + d.pdcsi_adjusted + '</strong></div>';
  h += '<span class="pdcsi-verdict ' + verdictClass + '">' + verdict + '</span>';
  h += '<button class="pdcsi-refresh" onclick="loadPdcsi(\'' + code + '\', true)">↻ 새로</button>';
  h += '</div>';
  h += '<div class="pdcsi-bars">';
  h += '<div class="pdcsi-bar-row"><span class="pdcsi-bar-label">환희</span><div class="pdcsi-bar-track"><div class="pdcsi-bar-fill euphoria" style="width:' + euphoria + '%"></div></div><span class="pdcsi-bar-val">' + euphoria + '</span></div>';
  h += '<div class="pdcsi-bar-row"><span class="pdcsi-bar-label">공포</span><div class="pdcsi-bar-track"><div class="pdcsi-bar-fill panic" style="width:' + panic + '%"></div></div><span class="pdcsi-bar-val">' + panic + '</span></div>';
  h += '<div class="pdcsi-bar-row"><span class="pdcsi-bar-label">불신</span><div class="pdcsi-bar-track"><div class="pdcsi-bar-fill distrust" style="width:' + distrust + '%"></div></div><span class="pdcsi-bar-val">' + distrust + '</span></div>';
  h += '<div class="pdcsi-bar-row"><span class="pdcsi-bar-label">선동</span><div class="pdcsi-bar-track"><div class="pdcsi-bar-fill shilling" style="width:' + shilling + '%"></div></div><span class="pdcsi-bar-val">' + shilling + '</span></div>';
  h += '</div>';
  h += '<div class="pdcsi-academic">';
  h += '<div class="pdcsi-acad-card"><div class="pdcsi-acad-label">불일치</div><div class="pdcsi-acad-val">' + dis + '</div><span class="pdcsi-acad-tag">' + disLabel + '</span></div>';
  h += '<div class="pdcsi-acad-card"><div class="pdcsi-acad-label">이상거래</div><div class="pdcsi-acad-val">' + volZ + 'σ</div><span class="pdcsi-acad-tag">' + volLabel + '</span></div>';
  h += '<div class="pdcsi-acad-card"><div class="pdcsi-acad-label">모멘텀</div><div class="pdcsi-acad-val">' + deltaStr + '</div><span class="pdcsi-acad-tag">' + momLabel + '</span></div>';
  h += '</div>';
  h += '<div class="pdcsi-meta">활동성 <strong>' + (d.velocity_label || '-') + '</strong> (' + (d.velocity_score || 0) + '점) · 게시글 ' + (d.post_count || 0) + '건 · 상태 ' + (d.status || '-') + ' ' + cachedTag;
  if (d.enhancement_note) h += '<br>💡 ' + d.enhancement_note;
  h += '</div>';
  container.innerHTML = h;
}
// PDCSI_PATCH_END
</script>
"""

JS_BLOCK = JS_BLOCK_TEMPLATE.replace("__PDCSI_URL__", PDCSI_URL).replace("__PDCSI_KEY__", SUPABASE_ANON_KEY)

HTML_BLOCK = (
    '          html += `<div class="pdcsi-section" data-pdcsi-patch="1">'
    '<button class="pdcsi-toggle" id="pdcsi-toggle-${item.code}" onclick="togglePdcsi(\'${item.code}\')">'
    '<span>🐷🐶 PDCSI 개돼지 지수 (종토방 군중심리)</span>'
    '<span class="pdcsi-arrow">▶</span>'
    '</button>'
    '<div class="pdcsi-body" id="pdcsi-body-${item.code}"></div>'
    '</div>`;\n'
)


def remove_marker_blocks(html: str) -> tuple[str, int]:
    patterns = [
        (r"/\* PDCSI_PATCH_START \*/.*?/\* PDCSI_PATCH_END \*/\s*", re.DOTALL),
        (r"// PDCSI_PATCH_START.*?// PDCSI_PATCH_END\s*", re.DOTALL),
    ]
    total = 0
    for pattern, flags in patterns:
        html, count = re.subn(pattern, "", html, flags=flags)
        total += count
    return html, total


def remove_legacy_css(html: str) -> tuple[str, int]:
    lines = html.splitlines(True)
    kept = []
    removed = 0
    for line in lines:
        stripped = line.strip()
        if "PDCSI (개돼지 지수) 섹션" in stripped or stripped.startswith(".pdcsi-"):
            removed += 1
            continue
        kept.append(line)
    return "".join(kept), removed


def remove_legacy_js_tail(html: str) -> tuple[str, int]:
    markers = [
        "// ─── PDCSI (개돼지 지수) 함수 ───",
        "const PDCSI_API_URL =",
        "const PDCSI_ANON_KEY =",
        "function togglePdcsi(code)",
        "async function loadPdcsi(code, forceRefresh)",
        "function renderPdcsi(container, d, code)",
    ]
    first = -1
    for marker in markers:
        idx = html.find(marker)
        if idx != -1 and (first == -1 or idx < first):
            first = idx
    if first == -1:
        return html, 0

    script_end = html.find("</script>", first)
    if script_end == -1:
        return html, 0

    line_start = html.rfind("\n", 0, first) + 1
    html = html[:line_start] + html[script_end:]
    return html, 1


def remove_legacy_html_blocks(html: str) -> tuple[str, int]:
    removed = 0
    while True:
        idx = html.find("PDCSI 개돼지 지수")
        if idx == -1:
            break
        start = html.rfind("html +=", 0, idx)
        if start == -1:
            break
        line_start = html.rfind("\n", 0, start) + 1
        end = html.find("`;", idx)
        if end == -1:
            semicolon = html.find(";", idx)
            if semicolon == -1:
                break
            end = semicolon
        else:
            end += 2
        line_end = html.find("\n", end)
        if line_end == -1:
            line_end = end
        else:
            line_end += 1
        html = html[:line_start] + html[line_end:]
        removed += 1
    return html, removed


def clean_existing_pdcsi(html: str) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}
    html, counts["marker_blocks"] = remove_marker_blocks(html)
    html, counts["legacy_css_lines"] = remove_legacy_css(html)
    html, counts["legacy_html_blocks"] = remove_legacy_html_blocks(html)
    html, counts["legacy_js_tail"] = remove_legacy_js_tail(html)
    return html, counts


def main():
    if not os.path.exists(TEMPLATE_PATH):
        print(f"❌ {TEMPLATE_PATH} 없음. /opt/stock-crawler에서 실행하세요.")
        sys.exit(1)

    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    shutil.copy2(TEMPLATE_PATH, BACKUP_PATH)
    print(f"✅ 백업 생성: {BACKUP_PATH}")

    html, cleanup = clean_existing_pdcsi(html)
    cleaned_total = sum(cleanup.values())
    if cleaned_total:
        print(
            "🧹 기존 PDCSI 정리 완료: "
            f"marker={cleanup['marker_blocks']}, css={cleanup['legacy_css_lines']}, "
            f"html={cleanup['legacy_html_blocks']}, js={cleanup['legacy_js_tail']}"
        )

    head_close = html.lower().rfind("</head>")
    if head_close == -1:
        print("❌ </head> 태그를 찾지 못했습니다.")
        sys.exit(1)
    html = html[:head_close] + CSS_BLOCK + "\n" + html[head_close:]
    print("✅ CSS 삽입 완료")

    body_close = html.lower().rfind("</body>")
    if body_close == -1:
        print("❌ </body> 태그를 찾지 못했습니다.")
        sys.exit(1)
    html = html[:body_close] + JS_BLOCK + "\n" + html[body_close:]
    print("✅ JS 함수 삽입 완료")

    calc_pattern = re.compile(r"(\s*html\s*\+=\s*[`'\"]\s*<div class=\"calc-section)")
    match = calc_pattern.search(html)
    if not match:
        print("⚠️  calc-section 패턴을 찾지 못해 HTML 블록 삽입 스킵 (수동 확인 필요).")
    else:
        insert_pos = match.start()
        html = html[:insert_pos] + "\n" + HTML_BLOCK + html[insert_pos:]
        print("✅ HTML 블록 삽입 완료 (calc-section 위)")

    with open(TEMPLATE_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    print("\n🎉 패치 완료!")
    print(f"\n롤백:\n  cp {BACKUP_PATH} {TEMPLATE_PATH}\n  sudo systemctl restart stock-crawler")


if __name__ == "__main__":
    main()
