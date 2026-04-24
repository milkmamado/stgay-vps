#!/usr/bin/env python3
"""patch_surge_v2.py — dedup + 5캡 + 분봉 스캘핑 + UI"""
import os, sys, shutil, datetime

ROOT = '/opt/stock-crawler'
SURGE_PY = os.path.join(ROOT, 'modules/surge_scanner.py')
INDEX_HTML = os.path.join(ROOT, 'templates/index.html')
ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')

# ===== 1) surge_scanner.py =====
print("[1/2] surge_scanner.py 패치...")
shutil.copy(SURGE_PY, f'{SURGE_PY}.backup_{ts}')
src = open(SURGE_PY, encoding='utf-8').read()

SCALPING_FN = '''
def _calc_scalping_levels(stock_mod, code, day_high, day_low, day_close):
    """분봉(5분봉) 기반 스캘핑 진입/목표/손절. 실패 시 일봉 fallback."""
    try:
        from datetime import datetime
        today = datetime.now().strftime("%Y%m%d")
        df = stock_mod.get_market_ohlcv_by_date(today, today, code, "m")
        if df is not None and len(df) > 5:
            tp = (df['고가'] + df['저가'] + df['종가']) / 3
            vwap = (tp * df['거래량']).cumsum() / df['거래량'].cumsum()
            vwap_now = float(vwap.iloc[-1])
            recent_low = float(df['저가'].tail(6).min())
            day_hi = float(df['고가'].max())
            entry_low = round(max(recent_low, vwap_now * 0.995))
            entry_high = round(vwap_now * 1.005)
            if entry_low >= entry_high:
                entry_high = round(entry_low * 1.005)
            target1 = round(day_hi)
            target2 = round(day_hi * 1.03)
            stop = round(recent_low * 0.98)
            mid = (entry_low + entry_high) / 2
            rr = round((target1 - mid) / max(mid - stop, 1), 2)
            return {'entry_low':entry_low,'entry_high':entry_high,'target1':target1,
                    'target2':target2,'stop':stop,'rr_ratio':rr,
                    'basis':'5분봉 VWAP+지지','vwap':round(vwap_now)}
    except Exception as e:
        logger.warning(f"  분봉 산출 실패 {code}: {e}")
    entry_low = round(day_low)
    entry_high = round((day_low + day_close) / 2)
    if entry_low >= entry_high:
        entry_high = round(entry_low * 1.005)
    target1 = round(day_high)
    target2 = round(day_high * 1.03)
    stop = round(day_low * 0.98)
    mid = (entry_low + entry_high) / 2
    rr = round((target1 - mid) / max(mid - stop, 1), 2)
    return {'entry_low':entry_low,'entry_high':entry_high,'target1':target1,
            'target2':target2,'stop':stop,'rr_ratio':rr,'basis':'일봉 fallback'}


'''

if '_calc_scalping_levels' not in src:
    src = src.replace('def run_surge_scan(', SCALPING_FN + 'def run_surge_scan(')
    print("  ✅ _calc_scalping_levels 추가")
else:
    print("  ⏭️ 스캘핑 함수 이미 있음")

OLD = """    # 점수 높은 순 정렬
    leaders.sort(key=lambda x: x['leader']['score'], reverse=True)

    _log(f"✅ 급등 스캐너 완료 — 테마 {len(leaders)}개 대장주 검출")"""

NEW = """    # 점수 높은 순 정렬
    leaders.sort(key=lambda x: x['leader']['score'], reverse=True)

    # === 종목 dedup ===
    seen_codes = {}
    for ld in leaders:
        code = ld['leader']['code']
        if code not in seen_codes:
            ld['extra_themes'] = []
            seen_codes[code] = ld
        else:
            seen_codes[code]['extra_themes'].append(ld['theme_name'])
    deduped = sorted(seen_codes.values(), key=lambda x: x['leader']['score'], reverse=True)

    # === 하드캡 5개 ===
    leaders = deduped[:5]

    # === 분봉 스캘핑 산출 ===
    _log("🔍 [4/4] 스캘핑 진입/목표/손절가 산출 (5분봉)")
    for ld in leaders:
        l = ld['leader']
        try:
            ld['scalping'] = _calc_scalping_levels(
                stock_mod, l['code'],
                l.get('day_high', l.get('price', 0)),
                l.get('day_low', l.get('price', 0)),
                l.get('price', 0))
        except Exception as e:
            _log(f"  ⚠️ {l['name']} 스캘핑 실패: {e}")
            ld['scalping'] = None

    _log(f"✅ 급등 스캐너 완료 — dedup 후 {len(leaders)}개 대장주 (최대 5개)")"""

if OLD in src:
    src = src.replace(OLD, NEW)
    print("  ✅ dedup + 5캡 + 스캘핑 적용")
elif "deduped" in src:
    print("  ⏭️ dedup 이미 적용됨")
else:
    print("  ❌ leaders 패턴 못 찾음")
    sys.exit(1)

DAY_OLD = "'price': int(latest['종가']),"
DAY_NEW = """'price': int(latest['종가']),
                        'day_high': int(latest['고가']),
                        'day_low': int(latest['저가']),"""
if DAY_OLD in src and 'day_high' not in src:
    src = src.replace(DAY_OLD, DAY_NEW, 1)
    print("  ✅ day_high/day_low 필드 추가")

open(SURGE_PY, 'w', encoding='utf-8').write(src)
print(f"  ✔️ 저장 (backup: ...backup_{ts})")

# ===== 2) templates/index.html =====
print("\n[2/2] index.html 패치...")
shutil.copy(INDEX_HTML, f'{INDEX_HTML}.backup_{ts}')
html = open(INDEX_HTML, encoding='utf-8').read()

HTML_OLD = """                    if (g.theme_members_names && g.theme_members_names.length > 1) {
                        sh += '<div style="font-size:11px;color:#94a3b8;margin-bottom:6px">동반: ' + g.theme_members_names.slice(1).map(esc).join(', ') + '</div>';
                    }"""

HTML_NEW = """                    if (g.theme_members_names && g.theme_members_names.length > 1) {
                        sh += '<div style="font-size:11px;color:#94a3b8;margin-bottom:6px">동반: ' + g.theme_members_names.slice(1).map(esc).join(', ') + '</div>';
                    }
                    if (g.extra_themes && g.extra_themes.length > 0) {
                        sh += '<div style="font-size:11px;color:#fbbf24;margin-bottom:6px">📌 추가 테마: ' + g.extra_themes.map(esc).join(' · ') + '</div>';
                    }
                    if (g.scalping) {
                        const sc = g.scalping;
                        const rrColor = sc.rr_ratio >= 1.5 ? '#22c55e' : (sc.rr_ratio >= 1 ? '#eab308' : '#ef4444');
                        sh += '<div style="margin:8px 0;padding:10px;background:#0f172a;border:1px solid #334155;border-radius:6px;font-size:12px">';
                        sh += '<div style="font-weight:600;color:#60a5fa;margin-bottom:6px">🎯 스캘핑 (' + esc(sc.basis) + ')</div>';
                        sh += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:4px">';
                        sh += '<div>진입: <b style="color:#22c55e">' + sc.entry_low.toLocaleString() + '~' + sc.entry_high.toLocaleString() + '</b></div>';
                        sh += '<div>손절: <b style="color:#ef4444">' + sc.stop.toLocaleString() + '</b></div>';
                        sh += '<div>1차 목표: <b style="color:#fbbf24">' + sc.target1.toLocaleString() + '</b></div>';
                        sh += '<div>2차 목표: <b style="color:#fbbf24">' + sc.target2.toLocaleString() + '</b></div>';
                        sh += '<div style="grid-column:1/3">손익비: <b style="color:' + rrColor + '">' + sc.rr_ratio + ' : 1</b>' + (sc.rr_ratio < 1.5 ? ' ⚠️ 신중' : ' ✅') + '</div>';
                        sh += '</div></div>';
                    }"""

if HTML_OLD in html:
    html = html.replace(HTML_OLD, HTML_NEW)
    print("  ✅ 스캘핑 박스 + 추가 테마 UI 추가")
elif "🎯 스캘핑" in html:
    print("  ⏭️ 이미 있음")
else:
    print("  ❌ 동반 패턴 못 찾음")
    sys.exit(1)

open(INDEX_HTML, 'w', encoding='utf-8').write(html)
print(f"  ✔️ 저장 (backup: ...backup_{ts})")

print("\n🎉 완료! 재시작:")
print("  systemctl restart stock-crawler && systemctl is-active stock-crawler")
