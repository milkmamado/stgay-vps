"""
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
    if crawler is None: return {}
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
                    stocks = detail.get('stockItems') or detail.get('stocks') or []
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






def _fetch_naver_5min_candles(code, count=390):
    """네이버 fchart XML 1분봉 → 당일 봉 list. 실패시 None.
    EUC-KR XML 응답: <item data="YYYYMMDDHHMM|open|high|low|close|volume" />
    함수명은 호환을 위해 유지하지만 실제로는 1분봉 반환.
    """
    try:
        import requests, re
        from datetime import datetime
        url = f"https://fchart.stock.naver.com/sise.nhn?symbol={code}&timeframe=minute&count={count}&requestType=0"
        r = requests.get(url, timeout=8, headers={'User-Agent': 'Mozilla/5.0'})
        if r.status_code != 200:
            return None
        # EUC-KR 디코딩
        try:
            text = r.content.decode('euc-kr')
        except Exception:
            text = r.text
        # <item data="..." /> 추출
        items = re.findall(r'<item data="([^"]+)"', text)
        if not items:
            return None
        candles = []
        for raw in items:
            parts = raw.split('|')
            if len(parts) < 6: continue
            t, o, h, l, c, v = parts[0], parts[1], parts[2], parts[3], parts[4], parts[5]
            # null 값 스킵 (장 시작 전 호가만 있는 봉)
            if 'null' in (o, h, l) or c == 'null': continue
            try:
                candles.append({
                    'time': t,
                    'open': float(o), 'high': float(h),
                    'low': float(l), 'close': float(c),
                    'volume': float(v),
                })
            except ValueError:
                continue
        if not candles:
            return None
        # 오늘 날짜만 필터
        today = datetime.now().strftime("%Y%m%d")
        today_candles = [c for c in candles if c['time'].startswith(today)]
        return today_candles if today_candles else candles[-30:]
    except Exception as e:
        print(f"[naver_fchart] {code} 실패: {e}")
        return None



def _fetch_naver_realtime_price(code):
    """네이버 모바일 시세 API로 실시간 현재가 조회. 실패 시 None."""
    try:
        import requests
        url = f"https://m.stock.naver.com/api/stock/{code}/basic"
        r = requests.get(url, timeout=3, headers={'User-Agent': 'Mozilla/5.0'})
        if r.status_code == 200:
            data = r.json()
            price = data.get('closePrice') or data.get('tradePrice')
            if price:
                # "9,100" 형태 문자열 처리
                return float(str(price).replace(',', ''))
    except Exception as e:
        logger.warning(f"  네이버 실시간 시세 실패 {code}: {e}")
    return None


def _calc_scalping_levels(stock_mod, code, day_high, day_low, day_close):
    """
    스캘핑 진입/목표/손절 산출.
    우선순위: 네이버 5분봉 VWAP+지지 > 네이버 실시간 시세 > 일봉 fallback
    """
    candles = _fetch_naver_5min_candles(code, count=78)
    
    if candles and len(candles) >= 6:
        # ✅ 진짜 5분봉 모드
        typical_x_vol = sum(((c['high']+c['low']+c['close'])/3) * c['volume'] for c in candles)
        total_vol = sum(c['volume'] for c in candles) or 1
        vwap = typical_x_vol / total_vol
        
        recent6 = candles[-30:]  # 최근 30분 (1분봉 30개)
        low_30m = min(c['low'] for c in recent6)
        current_price = candles[-1]['close']
        
        entry_low = round(max(vwap, low_30m))
        entry_high = round(current_price)
        if entry_high <= entry_low:
            entry_high = round(entry_low * 1.003)
        
        mid = (entry_low + entry_high) / 2
        stop = round(entry_low * 0.985)
        target1 = round(max(day_high, current_price * 1.015))
        target2 = round(mid * 1.03)
        rr = round((target1 - mid) / max(mid - stop, 1), 2)
        
        return {
            'entry_low': entry_low, 'entry_high': entry_high,
            'target1': target1, 'target2': target2, 'stop': stop,
            'rr_ratio': rr,
            'basis': '네이버 1분봉 VWAP+지지',
            'vwap': round(vwap),
            'current_price': round(current_price),
            'candle_count': len(candles),
        }
    
    # 🔻 실시간 시세 fallback
    rt_price = _fetch_naver_realtime_price(code)
    if rt_price:
        current_price = rt_price
        basis = '네이버 실시간 현재가'
    else:
        current_price = day_close
        basis = '일봉 종가 fallback'
    
    mid = current_price
    entry_low = round(mid * 0.995)
    entry_high = round(mid * 1.002)
    stop = round(entry_low * 0.985)
    target1 = round(max(day_high, mid * 1.02))
    target2 = round(mid * 1.03)
    rr = round((target1 - mid) / max(mid - stop, 1), 2)
    
    return {
        'entry_low': entry_low, 'entry_high': entry_high,
        'target1': target1, 'target2': target2, 'stop': stop,
        'rr_ratio': rr, 'basis': basis,
        'current_price': round(current_price),
    }




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

    _log(f"✅ 급등 스캐너 완료 — dedup 후 {len(leaders)}개 대장주 (최대 5개)")

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
