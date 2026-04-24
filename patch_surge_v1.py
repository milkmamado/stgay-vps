"""
patch_surge_v1.py — 급등 테마 대장주 스캐너 통합 패치
=====================================================
손부장님 VPS에서 한 방에 적용:
  cd /opt/stock-crawler
  python patch_surge_v1.py
  → 자동 백업 + 모듈 설치 + job.py 패치 + index.html UI 추가
  → 실패 시 자동 롤백

대상:
  1) modules/surge_scanner.py   ← 신규 추가
  2) modules/job.py             ← run_crawl_job 에 급등 스캐너 호출 끼워넣기 (85→95%)
  3) templates/index.html       ← 숨은보석 아래 "급등 테마 대장주" 섹션 + JS

원칙 (stgay-maintenance-strategy 준수):
  - 기존 TOP3 / 숨은보석 섹션 절대 손대지 않음
  - HTML/JS 구조 보존, 새 블록만 삽입
  - 실패 시 .bak 파일에서 복원
"""

import os
import shutil
import sys
import re
from datetime import datetime

ROOT = '/opt/stock-crawler'
TS = datetime.now().strftime('%Y%m%d-%H%M%S')

JOB_PY = os.path.join(ROOT, 'modules/job.py')
INDEX_HTML = os.path.join(ROOT, 'templates/index.html')
SURGE_PY = os.path.join(ROOT, 'modules/surge_scanner.py')


# ============================================================
# 1) surge_scanner.py 본문 (별도 파일에서 SCP 로 올리지 않고 임베드)
# ============================================================
SURGE_SCANNER_CODE = r'''"""
급등 테마 대장주 스캐너 v1.0
=================================
1~2일 누적 +15% 급등주 → 인포스탁 테마 그룹핑 → 같은 테마 3종목+ 모이면
대장주 1위만 추천 출력. 추격매수 위험 가드 필수.

파이프라인:
  1) pykrx로 코스피/코스닥 전종목 일봉 (2일치) → 누적 +15% 필터
  2) 거래대금 5일평균 ×2배+ / 시총 300억~5조 / 3일 연속 급등 제외
  3) 인포스탁 종목→테마 역매핑 (StockNewsCrawler.get_sector_stocks_api 재활용)
  4) 같은 테마 3종목+ 모인 그룹만 채택, 그룹 내 점수 1위 = 대장주

반환 형식 (job.py가 crawl_state['result']['surge_leaders']에 저장):
  {
    'scanned_date': '2026-04-23',
    'total_surged': 12,            # +15% 종목 총 개수
    'leaders': [
      {
        'theme_name': '2차전지',
        'theme_code': '177',
        'leader': {
          'name': '에코프로', 'code': '086520',
          'cum_return_pct': 18.5, 'volume_ratio': 3.2,
          'market_cap_eok': 12000, 'market_cap_grade': 'large',
          'score': 87.3, 'price': 125000,
        },
        'theme_members_count': 4,   # 이 테마에서 같이 급등한 종목 수
        'warning': '추격매수 위험 — 대장주 확인용. 매수는 눌림목 후 판단',
      },
      ...
    ],
  }
"""

import time
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

# 설정값 (기획안 기준)
SURGE_THRESHOLD_PCT = 15.0          # 1~2일 누적 등락률
VOLUME_MULTIPLIER = 2.0             # 5일평균 거래대금 ×2배+
MIN_MARKET_CAP_EOK = 300            # 최소 시총 300억
MAX_MARKET_CAP_EOK = 50000          # 최대 시총 5조
MIN_THEME_MEMBERS = 3               # 같은 테마 3종목+ 모여야 진짜 테마
MAX_CONSECUTIVE_SURGE_DAYS = 3      # 3일 연속 급등 = 끝물 제외


def _get_pykrx_data():
    """pykrx 동적 import (모듈 로딩 실패 시 graceful)."""
    try:
        from pykrx import stock
        return stock
    except ImportError:
        logger.warning("pykrx 미설치 — 급등 스캐너 비활성")
        return None


def _build_stock_to_theme_map(crawler):
    """
    인포스탁 API로 종목코드→테마(들) 역매핑 사전 구축.
    StockNewsCrawler.get_sector_stocks_api()는 이미 1시간 캐시 있음.
    """
    theme_data = crawler.get_sector_stocks_api()
    if not theme_data or 'data' not in theme_data:
        return {}

    items = theme_data.get('data', {}).get('items', [])
    stock_to_themes = {}  # {code: [(theme_name, theme_code), ...]}

    # 각 테마 detail 호출은 비용 큼 → 일단 items에 stocks 들어있는지 확인
    for theme in items:
        theme_name = theme.get('name', '')
        theme_code = str(theme.get('code', '')).lstrip('0') or '0'
        # items에 stocks 직접 들어있는 경우
        stocks = theme.get('stocks') or theme.get('items') or []
        for s in stocks:
            code = str(s.get('code') or s.get('shcode') or '').strip().zfill(6)
            if code and code != '000000':
                stock_to_themes.setdefault(code, []).append((theme_name, theme_code))

    # stocks가 비어있는 경우 → theme/detail 호출 (한 번만, rate limit 주의)
    if not stock_to_themes:
        logger.info("테마 detail 호출 시작 (rate limit 주의)")
        for theme in items[:300]:  # 안전 상한
            theme_name = theme.get('name', '')
            theme_code = str(theme.get('code', '')).lstrip('0') or '0'
            try:
                detail = crawler.get_theme_detail(theme_code)
                if detail:
                    stocks = detail.get('stocks') or detail.get('items') or []
                    for s in stocks:
                        code = str(s.get('code') or s.get('shcode') or '').strip().zfill(6)
                        if code and code != '000000':
                            stock_to_themes.setdefault(code, []).append((theme_name, theme_code))
                time.sleep(0.05)  # 50ms 간격
            except Exception as e:
                logger.debug(f"theme detail {theme_code} 실패: {e}")
                continue

    logger.info(f"종목→테마 역매핑 완료: {len(stock_to_themes)}개 종목")
    return stock_to_themes


def _scan_surged_stocks(stock_mod, log_fn=None):
    """
    pykrx로 코스피/코스닥 전종목 2일 누적 등락률 계산 → +15% 이상만 반환.
    """
    today = datetime.now()
    # 영업일 보정: 6일 전부터 받아서 최근 거래일 6개 확보
    start = (today - timedelta(days=10)).strftime('%Y%m%d')
    end = today.strftime('%Y%m%d')

    surged = []

    for market in ['KOSPI', 'KOSDAQ']:
        try:
            tickers = stock_mod.get_market_ticker_list(end, market=market)
        except Exception as e:
            if log_fn:
                log_fn(f"  ⚠️ {market} 티커 조회 실패: {e}")
            continue

        if log_fn:
            log_fn(f"  📊 {market} {len(tickers)}종목 스캔 중...")

        # 일괄 OHLCV (시장 전체 한방에)
        try:
            df_today = stock_mod.get_market_ohlcv(end, market=market)
            df_cap = stock_mod.get_market_cap(end, market=market)
        except Exception as e:
            if log_fn:
                log_fn(f"  ⚠️ {market} OHLCV 조회 실패: {e}")
            continue

        # 종목별 6일치 일봉 받아서 누적등락률 계산은 너무 느림
        # → 시장 전체 일봉 6일치를 한방에 받는 게 빠름
        try:
            # 최근 6 거래일 종가만 추출
            from pykrx import stock as _s
            # date_to_close 사전 만들기
            date_list = []
            cur = today
            for _ in range(15):
                cur -= timedelta(days=1)
                d = cur.strftime('%Y%m%d')
                try:
                    df = _s.get_market_ohlcv(d, market=market)
                    if df is not None and not df.empty:
                        date_list.append((d, df))
                        if len(date_list) >= 6:
                            break
                except Exception:
                    continue
        except Exception as e:
            if log_fn:
                log_fn(f"  ⚠️ {market} 과거 일봉 수집 실패: {e}")
            continue

        if len(date_list) < 4:
            if log_fn:
                log_fn(f"  ⚠️ {market} 거래일 데이터 부족 ({len(date_list)}일)")
            continue

        # date_list[0] = 가장 최근, [1] = 1일전, [2] = 2일전, ...
        df_d0 = date_list[0][1]
        df_d1 = date_list[1][1] if len(date_list) > 1 else None
        df_d2 = date_list[2][1] if len(date_list) > 2 else None
        df_d3 = date_list[3][1] if len(date_list) > 3 else None
        df_d4 = date_list[4][1] if len(date_list) > 4 else None
        df_d5 = date_list[5][1] if len(date_list) > 5 else None

        for ticker in tickers:
            try:
                if ticker not in df_d0.index or ticker not in df_d2.index:
                    continue

                close_today = float(df_d0.loc[ticker, '종가'])
                # 2일전 종가 기준 (1~2일 누적)
                close_2d_ago = float(df_d2.loc[ticker, '종가'])
                if close_2d_ago <= 0:
                    continue

                cum_return = (close_today - close_2d_ago) / close_2d_ago * 100

                if cum_return < SURGE_THRESHOLD_PCT:
                    continue

                # 3일 연속 급등 체크 (끝물 제외)
                consecutive = 0
                for df_check in [df_d0, df_d1, df_d2]:
                    if df_check is None or ticker not in df_check.index:
                        break
                    try:
                        chg = float(df_check.loc[ticker, '등락률'])
                        if chg >= 5.0:
                            consecutive += 1
                        else:
                            break
                    except Exception:
                        break
                if consecutive >= MAX_CONSECUTIVE_SURGE_DAYS:
                    continue  # 끝물

                # 거래대금 비교: 오늘 vs 5일 평균
                today_value = float(df_d0.loc[ticker, '거래대금'])
                past_values = []
                for df_p in [df_d1, df_d2, df_d3, df_d4, df_d5]:
                    if df_p is not None and ticker in df_p.index:
                        try:
                            past_values.append(float(df_p.loc[ticker, '거래대금']))
                        except Exception:
                            pass
                if not past_values:
                    continue
                avg_value = sum(past_values) / len(past_values)
                if avg_value <= 0:
                    continue
                vol_ratio = today_value / avg_value
                if vol_ratio < VOLUME_MULTIPLIER:
                    continue

                # 시총 필터 (억 단위)
                if ticker not in df_cap.index:
                    continue
                cap_won = float(df_cap.loc[ticker, '시가총액'])
                cap_eok = cap_won / 1e8
                if cap_eok < MIN_MARKET_CAP_EOK or cap_eok > MAX_MARKET_CAP_EOK:
                    continue

                # 종목명
                try:
                    name = stock_mod.get_market_ticker_name(ticker)
                except Exception:
                    name = ticker

                surged.append({
                    'code': ticker,
                    'name': name,
                    'cum_return_pct': round(cum_return, 2),
                    'volume_ratio': round(vol_ratio, 2),
                    'market_cap_eok': int(cap_eok),
                    'price': int(close_today),
                    'today_value': int(today_value),
                    'consecutive_surge': consecutive,
                })
            except Exception:
                continue

        if log_fn:
            log_fn(f"  ✅ {market} 스캔 완료")

    return surged


def _grade_market_cap_simple(cap_eok):
    """간이 등급 (swing_engine.grade_market_cap 참고)."""
    if cap_eok < 1000:
        return 'small'
    elif cap_eok < 5000:
        return 'sweet_spot'   # 1천~5천억
    elif cap_eok < 10000:
        return 'mid'
    else:
        return 'large'


def _calculate_score(stock):
    """
    점수산정:
      거래대금 비율(40%) + 누적등락률(20%) + sweet_spot 보너스(20%) + 시총 적정도(20%)
    """
    vol_score = min(stock['volume_ratio'] * 10, 40)        # 최대 40
    return_score = min(stock['cum_return_pct'], 20)        # 최대 20
    grade = _grade_market_cap_simple(stock['market_cap_eok'])
    sweet_bonus = 20 if grade == 'sweet_spot' else (10 if grade == 'mid' else 5)
    cap_score = 20 if 500 <= stock['market_cap_eok'] <= 20000 else 10
    return round(vol_score + return_score + sweet_bonus + cap_score / 2, 2)


def run_surge_scan(crawler, log_fn=None):
    """
    메인 진입점. job.py가 호출.
    crawler: StockNewsCrawler 인스턴스 (인포스탁 캐시 공유 X — 호출자가 새 인스턴스 권장)
    log_fn: progress 로깅 함수 (job.py의 log)
    """
    def _log(msg):
        if log_fn:
            log_fn(msg)

    stock_mod = _get_pykrx_data()
    if stock_mod is None:
        _log("⚠️ pykrx 미설치 — 급등 스캐너 건너뜀")
        return None

    _log("📈 급등 스캐너 시작...")

    # Step 1: 급등주 수집
    _log("🔍 [1/3] 급등주 스캔 (1~2일 +15%, 거래대금 ×2배+)")
    surged = _scan_surged_stocks(stock_mod, _log)
    _log(f"  → 조건 통과 {len(surged)}종목")

    if not surged:
        return {
            'scanned_date': datetime.now().strftime('%Y-%m-%d'),
            'total_surged': 0,
            'leaders': [],
            'message': '오늘 +15% 급등 종목 없음'
        }

    # Step 2: 인포스탁 테마 매핑
    _log("🔍 [2/3] 인포스탁 테마 역매핑")
    stock_to_themes = _build_stock_to_theme_map(crawler)

    if not stock_to_themes:
        _log("  ⚠️ 인포스탁 매핑 실패 — 테마 그룹핑 불가")
        return {
            'scanned_date': datetime.now().strftime('%Y-%m-%d'),
            'total_surged': len(surged),
            'leaders': [],
            'message': '인포스탁 API 응답 실패'
        }

    # Step 3: 테마별 그룹핑
    _log("🔍 [3/3] 테마별 그룹핑 + 대장주 선정")
    theme_groups = {}  # {theme_name: {'code': str, 'members': [stock,...]}}
    for s in surged:
        themes = stock_to_themes.get(s['code'], [])
        for theme_name, theme_code in themes:
            if theme_name not in theme_groups:
                theme_groups[theme_name] = {'code': theme_code, 'members': []}
            theme_groups[theme_name]['members'].append(s)

    # 같은 테마 MIN_THEME_MEMBERS (3종목)+ 모인 것만 채택
    leaders = []
    for theme_name, group in theme_groups.items():
        members = group['members']
        if len(members) < MIN_THEME_MEMBERS:
            continue

        # 점수 계산 후 1위 = 대장주
        for m in members:
            m['score'] = _calculate_score(m)
        members_sorted = sorted(members, key=lambda x: x['score'], reverse=True)
        leader = members_sorted[0]
        leader['market_cap_grade'] = _grade_market_cap_simple(leader['market_cap_eok'])

        leaders.append({
            'theme_name': theme_name,
            'theme_code': group['code'],
            'leader': leader,
            'theme_members_count': len(members),
            'theme_members_names': [m['name'] for m in members_sorted[:5]],
            'warning': '⚠️ 추격매수 위험 — 대장주 확인용. 매수는 눌림목 형성 후 판단하세요.',
        })

    # 점수 높은 순 정렬
    leaders.sort(key=lambda x: x['leader']['score'], reverse=True)

    _log(f"✅ 급등 스캐너 완료 — 테마 {len(leaders)}개 대장주 검출")

    return {
        'scanned_date': datetime.now().strftime('%Y-%m-%d'),
        'total_surged': len(surged),
        'leaders': leaders,
        'config': {
            'surge_threshold': SURGE_THRESHOLD_PCT,
            'volume_multiplier': VOLUME_MULTIPLIER,
            'min_theme_members': MIN_THEME_MEMBERS,
            'cap_range_eok': [MIN_MARKET_CAP_EOK, MAX_MARKET_CAP_EOK],
        }
    }


if __name__ == '__main__':
    # 단독 테스트용
    import sys
    sys.path.insert(0, '/opt/stock-crawler')
    from modules.crawler import StockNewsCrawler
    result = run_surge_scan(StockNewsCrawler(), print)
    import json
    print(json.dumps(result, ensure_ascii=False, indent=2))
'''


# ============================================================
# 2) job.py 패치 — 두 군데
#    a) import 라인 추가 (상단)
#    b) run_crawl_job 안 final 블록 직전에 급등 스캐너 호출 + 결과 저장
# ============================================================
JOB_IMPORT_OLD = "from swing_engine import analyze_stock_swing"
JOB_IMPORT_NEW = """from swing_engine import analyze_stock_swing
from modules.surge_scanner import run_surge_scan"""

# 결과 저장 직전 (crawl_state['result'] = { ... } 블록 직전)에 급등 스캔 호출 삽입
JOB_HOOK_OLD = "        with state_lock:\n            crawl_state['phase'] = '완료'\n            crawl_state['percent'] = 100"
JOB_HOOK_NEW = """        # ===== 급등 테마 대장주 스캔 (85→95%) =====
        surge_result = None
        try:
            with state_lock:
                crawl_state['phase'] = '급등 테마 스캔 중'
                crawl_state['percent'] = 90
            log(f"\\n📈 급등 테마 대장주 스캔 시작...")
            # 인포스탁 캐시 공유 위해 동일 crawler 인스턴스 재사용
            surge_result = run_surge_scan(crawler, log)
            with state_lock:
                crawl_state['percent'] = 95
            if surge_result and surge_result.get('leaders'):
                log(f"✅ 급등 대장주 {len(surge_result['leaders'])}개 검출")
            else:
                log(f"  → 급등 대장주 없음 (오늘 시장 잠잠)")
        except Exception as _surge_e:
            log(f"⚠️ 급등 스캐너 오류 (기존 결과는 정상 출력): {_surge_e}")
            surge_result = None

        with state_lock:
            crawl_state['phase'] = '완료'
            crawl_state['percent'] = 100"""

# crawl_state['result'] dict 에 surge_leaders 키 추가
JOB_RESULT_OLD = "                'ai_analysis': ai_analysis,\n            }"
JOB_RESULT_NEW = """                'ai_analysis': ai_analysis,
                'surge_leaders': surge_result,
            }"""


# ============================================================
# 3) index.html 패치 — 두 군데
#    a) renderDashboard 의 el.innerHTML = html; 직전에 급등 섹션 HTML 삽입
#    b) renderHTMLForDownload 도 동일 (선택)
# ============================================================
INDEX_HOOK_OLD = "        el.innerHTML = html;\n\n        // Draw charts after DOM update"
INDEX_HOOK_NEW = """        // ===== 급등 테마 대장주 섹션 (TOP3 / 숨은보석 보존, 별도 추가) =====
        try {
            const surge = data.surge_leaders;
            if (surge && Array.isArray(surge.leaders) && surge.leaders.length > 0) {
                let sh = '<div class="section" style="border-top:1px solid #2a2a2a;margin-top:24px;padding-top:20px">';
                sh += '<div class="section-title">🔥 급등 테마 대장주 <span style="font-size:11px;color:#888;font-weight:400;margin-left:8px">(공격형 보조 · 30% 이하 권장)</span></div>';
                sh += '<div style="background:#1a1410;border:1px solid #4a2a1a;border-radius:8px;padding:10px 14px;margin-bottom:14px;font-size:12px;color:#d99">⚠️ 추격매수 위험 구간 — 대장주 확인용입니다. 매수는 눌림목 형성 후 판단하세요. (메인은 위쪽 TOP3 눌림목 매매)</div>';
                sh += '<div style="font-size:12px;color:#888;margin-bottom:12px">스캔일: ' + esc(surge.scanned_date || '') + ' · 전체 +15% 급등 ' + (surge.total_surged || 0) + '종목 · 같은 테마 3종목+ 그룹만 표시</div>';
                surge.leaders.forEach((g, i) => {
                    const ld = g.leader || {};
                    const grade = ld.market_cap_grade || '';
                    const gradeBadge = grade === 'sweet_spot' ? '<span style="background:#2a4020;color:#9c9;padding:2px 8px;border-radius:10px;font-size:10px;margin-left:6px">스위트스팟</span>' : '';
                    sh += '<div class="top3-card" style="border-left:2px solid #c93;margin-bottom:10px">';
                    sh += '<div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px">';
                    sh += '<div><div style="font-size:14px;font-weight:600;color:#eee">👑 ' + esc(ld.name || '') + ' <span style="font-size:11px;color:#888">(' + esc(ld.code || '') + ')</span>' + gradeBadge + '</div>';
                    sh += '<div style="font-size:12px;color:#aaa;margin-top:4px">테마: <b style="color:#c93">' + esc(g.theme_name || '') + '</b> · 같은 테마 급등 ' + (g.theme_members_count || 0) + '종목</div>';
                    if (g.theme_members_names && g.theme_members_names.length > 1) {
                        sh += '<div style="font-size:11px;color:#666;margin-top:3px">동반: ' + g.theme_members_names.slice(1).map(esc).join(', ') + '</div>';
                    }
                    sh += '</div>';
                    sh += '<div style="text-align:right;font-size:11px;color:#888"><div style="font-size:18px;color:#fc6;font-weight:600">+' + (ld.cum_return_pct || 0) + '%</div>';
                    sh += '<div>거래대금 ×' + (ld.volume_ratio || 0) + '</div>';
                    sh += '<div>시총 ' + Math.round((ld.market_cap_eok || 0) / 100) / 10 + '천억</div>';
                    sh += '<div style="color:#666">점수 ' + (ld.score || 0) + '</div></div>';
                    sh += '</div></div>';
                });
                sh += '</div>';
                html += sh;
            } else if (surge && surge.message) {
                html += '<div class="section" style="border-top:1px solid #2a2a2a;margin-top:24px;padding-top:20px"><div class="section-title">🔥 급등 테마 대장주</div><div style="color:#888;font-size:13px">' + esc(surge.message) + '</div></div>';
            }
        } catch (e) { console.error('surge render err', e); }

        el.innerHTML = html;

        // Draw charts after DOM update"""


def backup(path):
    """백업 생성 후 경로 반환."""
    if not os.path.exists(path):
        return None
    bak = f"{path}.bak.{TS}"
    shutil.copy2(path, bak)
    print(f"  💾 백업: {bak}")
    return bak


def patch_file(path, old, new, label):
    """파일 내 old → new 치환. 이미 패치되어 있으면 스킵."""
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    if new.strip() and (new.split('\n')[0].strip() in content or 'surge_scanner' in content and label == 'job_import'):
        print(f"  ℹ️  {label}: 이미 패치됨 — 스킵")
        return True

    if old not in content:
        print(f"  ❌ {label}: 패턴 못 찾음")
        print(f"      찾는 패턴 첫줄: {old.splitlines()[0][:80]}")
        return False

    new_content = content.replace(old, new, 1)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    print(f"  ✅ {label}: 패치 완료")
    return True


def restore(backup_paths):
    print("\n🔄 롤백 중...")
    for orig, bak in backup_paths.items():
        if bak and os.path.exists(bak):
            shutil.copy2(bak, orig)
            print(f"  ↩️  복원: {orig}")


def main():
    print(f"=" * 60)
    print(f"🚀 급등 테마 대장주 스캐너 통합 패치 v1")
    print(f"   타임스탬프: {TS}")
    print(f"=" * 60)

    if not os.path.isdir(ROOT):
        print(f"❌ {ROOT} 디렉토리 없음 — VPS에서 실행하세요")
        sys.exit(1)

    if SURGE_SCANNER_CODE.startswith('__SURGE'):
        print(f"❌ surge_scanner 코드가 임베드되지 않음 — 패치 파일 손상")
        sys.exit(1)

    backups = {}

    # ===== 1) surge_scanner.py 신규 작성 =====
    print(f"\n[1/3] modules/surge_scanner.py 작성")
    if os.path.exists(SURGE_PY):
        backups[SURGE_PY] = backup(SURGE_PY)
    else:
        backups[SURGE_PY] = None  # 신규 파일 → 롤백 시 삭제
    try:
        with open(SURGE_PY, 'w', encoding='utf-8') as f:
            f.write(SURGE_SCANNER_CODE)
        print(f"  ✅ {SURGE_PY} 작성 완료 ({len(SURGE_SCANNER_CODE)} bytes)")
    except Exception as e:
        print(f"  ❌ 실패: {e}")
        restore(backups)
        sys.exit(1)

    # ===== 2) job.py 패치 =====
    print(f"\n[2/3] modules/job.py 패치")
    backups[JOB_PY] = backup(JOB_PY)
    ok = True
    ok &= patch_file(JOB_PY, JOB_IMPORT_OLD, JOB_IMPORT_NEW, 'job_import')
    ok &= patch_file(JOB_PY, JOB_HOOK_OLD, JOB_HOOK_NEW, 'job_hook')
    ok &= patch_file(JOB_PY, JOB_RESULT_OLD, JOB_RESULT_NEW, 'job_result_key')
    if not ok:
        print(f"  ❌ job.py 패치 일부 실패")
        restore(backups)
        if backups[SURGE_PY] is None:
            os.remove(SURGE_PY)
        sys.exit(1)

    # ===== 3) index.html 패치 =====
    print(f"\n[3/3] templates/index.html 패치")
    backups[INDEX_HTML] = backup(INDEX_HTML)
    ok = patch_file(INDEX_HTML, INDEX_HOOK_OLD, INDEX_HOOK_NEW, 'index_render_hook')
    if not ok:
        restore(backups)
        if backups[SURGE_PY] is None:
            os.remove(SURGE_PY)
        sys.exit(1)

    # ===== 검증 (문법) =====
    print(f"\n[검증] Python 문법 체크")
    import py_compile
    try:
        py_compile.compile(SURGE_PY, doraise=True)
        py_compile.compile(JOB_PY, doraise=True)
        print(f"  ✅ 문법 OK")
    except py_compile.PyCompileError as e:
        print(f"  ❌ 문법 오류: {e}")
        restore(backups)
        if backups[SURGE_PY] is None:
            os.remove(SURGE_PY)
        sys.exit(1)

    print(f"\n" + "=" * 60)
    print(f"🎉 패치 완료!")
    print(f"=" * 60)
    print(f"\n다음 단계:")
    print(f"  1) systemctl restart stock-crawler   (또는 사용 중인 서비스명)")
    print(f"  2) https://leapblg-6.me/stgay/ 접속 → [분석하기] 클릭")
    print(f"  3) 진행률 90% 부근에서 '급등 테마 스캔 중' 표시 확인")
    print(f"\n롤백 필요 시:")
    print(f"  python rollback_surge.sh {TS}")
    print(f"  (또는 .bak.{TS} 파일을 수동 복원)")


if __name__ == '__main__':
    main()
