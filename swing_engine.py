"""
스윙 트레이딩 엔진 v4.0 — 3단계 순차 필터

필터 흐름:
  1차) 10~15일 내 장대양봉 (거래량 2배+, 양봉 5%+) 
  2차) 고가 부근 박스권 형성 여부
  3차) 외국인/기관 순매수 수급

참고 지표 (필터 아님, 카드에 표시용):
  - OBV 추세, MFI, 볼린저밴드, 정배열 여부, RSI

악재 키워드 감지:
  - 유상증자, 감자, 관리종목, 상장폐지, 횡령 등
"""

import numpy as np
import requests
from bs4 import BeautifulSoup


# ============================================================
# 1차 필터: 장대양봉 감지 (최근 15일)
# ============================================================
def detect_big_candles(prices, lookback=15):
    """
    최근 lookback일 내 장대양봉 감지
    조건: 거래량 >= 20일 평균의 2배 AND 양봉 몸통 >= 10%
    반환: 감지된 장대양봉 리스트
    """
    if len(prices) < lookback + 20:
        # 데이터 부족하면 있는 만큼만
        if len(prices) < 10:
            return []
        lookback = min(lookback, len(prices) - 5)

    results = []

    for i in range(len(prices) - lookback, len(prices)):
        p = prices[i]
        # 20일 평균 거래량 (해당 시점 기준)
        vol_window = prices[max(0, i-20):i]
        if not vol_window:
            continue
        avg_vol = np.mean([v['volume'] for v in vol_window])
        if avg_vol <= 0:
            continue

        vol_ratio = p['volume'] / avg_vol
        open_price = p.get('open', p['close'])
        if open_price <= 0:
            continue
        body_pct = (p['close'] - open_price) / open_price * 100

        # 장대양봉 조건: 거래량 2배+, 양봉 10%+
        if vol_ratio >= 2.0 and body_pct >= 5.0:
            results.append({
                'date': p['date'],
                'close': p['close'],
                'vol_ratio': round(vol_ratio, 1),
                'body_pct': round(body_pct, 1),
                'index': i,
                # 강도 분류
                'strength': 'strong' if (vol_ratio >= 3 and body_pct >= 3) else 'normal'
            })

    return results


# ============================================================
# 2차 필터: 고가 박스권 감지
# ============================================================
def detect_high_box(prices, big_candle_info=None):
    """
    장대양봉 이후 고가 부근에서 박스권(횡보) 형성 여부
    조건: 장대양봉 이후 5일 이상 횡보, 변동폭 15% 이내
    급등 전제 없이도 최근 고가 부근 횡보를 감지
    """
    if len(prices) < 10:
        return None

    # 장대양봉이 있으면 그 이후 구간에서 박스권 찾기
    if big_candle_info:
        bc = big_candle_info[-1]  # 가장 최근 장대양봉
        bc_idx = bc['index']
        days_after = len(prices) - 1 - bc_idx

        if days_after >= 3:  # 장대양봉 후 최소 3일
            segment = prices[bc_idx + 1:]
            if len(segment) >= 3:
                closes = [p['close'] for p in segment]
                high = max(closes)
                low = min(closes)
                range_pct = ((high - low) / low * 100) if low > 0 else 999

                if range_pct <= 15:
                    return {
                        'type': 'post_surge_box',
                        'box_high': high,
                        'box_low': low,
                        'box_days': len(segment),
                        'range_pct': round(range_pct, 1),
                        'trigger_date': bc['date'],
                        'trigger_close': bc['close'],
                    }

    # 장대양봉 없어도 최근 10~20일 고가 박스권 체크
    for window in [10, 15, 20]:
        if len(prices) < window + 5:
            continue
        segment = prices[-window:]
        closes = [p['close'] for p in segment]
        high = max(closes)
        low = min(closes)
        range_pct = ((high - low) / low * 100) if low > 0 else 999

        if range_pct <= 12:
            # 박스권 전에 상승이 있었는지 확인
            pre_segment = prices[-(window + 10):-window] if len(prices) >= window + 10 else []
            pre_low = min(p['close'] for p in pre_segment) if pre_segment else low
            surge_pct = ((low - pre_low) / pre_low * 100) if pre_low > 0 else 0

            if surge_pct >= 5:  # 이전에 5% 이상 상승 후 횡보
                return {
                    'type': 'high_consolidation',
                    'box_high': high,
                    'box_low': low,
                    'box_days': window,
                    'range_pct': round(range_pct, 1),
                    'pre_surge_pct': round(surge_pct, 1),
                }

    return None


def detect_box_position(prices, box_info):
    """현재 주가가 박스 내 어디에 위치하는지"""
    if not box_info:
        return None

    current = prices[-1]['close']
    box_high = box_info['box_high']
    box_low = box_info['box_low']
    box_range = box_high - box_low

    if box_range <= 0:
        return None

    position_pct = ((current - box_low) / box_range) * 100

    if current > box_high * 1.01:
        zone = 'breakout'
        label = '박스 상단 돌파'
    elif position_pct >= 70:
        zone = 'upper'
        label = '박스 상단 부근'
    elif position_pct <= 30:
        zone = 'lower'
        label = '박스 하단 (눌림 구간)'
    else:
        zone = 'middle'
        label = '박스 중간'

    return {
        'zone': zone,
        'label': label,
        'position_pct': round(position_pct, 1),
        'current': current,
        'box_high': box_high,
        'box_low': box_low,
    }


# ============================================================
# 3차 필터: 외국인/기관 수급
# ============================================================
def get_investor_data(code):
    """네이버 금융에서 외국인/기관 매매동향 크롤링 (2가지 방식 시도)"""
    try:
        # 방법 1: 외국인/기관 순매매 페이지
        url = f"https://finance.naver.com/item/frgn.naver?code={code}"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        resp = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(resp.text, 'html.parser')

        tables = soup.select('table.type2')
        data = []

        for table in tables:
            rows = table.select('tr')
            for row in rows:
                cols = row.select('td')
                if len(cols) < 6:
                    continue
                try:
                    date_text = cols[0].text.strip()
                    if not date_text or '.' not in date_text:
                        continue
                    close_text = cols[1].text.strip().replace(',', '')
                    if not close_text:
                        continue
                    close = int(close_text)

                    # 외국인 순매수: 여러 컬럼 위치 시도
                    foreign_net = 0
                    inst_net = 0
                    for ci in range(4, min(len(cols), 9)):
                        val = cols[ci].text.strip().replace(',', '').replace('+', '').replace('\n', '').replace('\t', '')
                        if val and val != '0' and val.lstrip('-').isdigit():
                            if foreign_net == 0:
                                foreign_net = int(val)
                            elif inst_net == 0:
                                inst_net = int(val)
                                break

                    data.append({
                        'date': date_text,
                        'close': close,
                        'foreign_net': foreign_net,
                        'institution_net': inst_net
                    })
                except (ValueError, IndexError):
                    continue

            if data:
                break

        return data[:20] if data else None
    except Exception as e:
        return None


def analyze_supply_demand(investor_data):
    """외국인/기관 수급 분석"""
    if not investor_data or len(investor_data) < 3:
        return {'score': 0, 'signals': [], 'data': None}

    signals = []
    score = 0
    recent_5 = investor_data[:min(5, len(investor_data))]
    recent_10 = investor_data[:min(10, len(investor_data))]

    f5 = sum(d['foreign_net'] for d in recent_5)
    i5 = sum(d['institution_net'] for d in recent_5)
    f10 = sum(d['foreign_net'] for d in recent_10)
    i10 = sum(d['institution_net'] for d in recent_10)

    # 외국인 연속 순매수
    f_consec = 0
    for d in recent_5:
        if d['foreign_net'] > 0:
            f_consec += 1
        else:
            break

    i_consec = 0
    for d in recent_5:
        if d['institution_net'] > 0:
            i_consec += 1
        else:
            break

    if f_consec >= 3:
        score += 2
        signals.append(f"외국인 {f_consec}일 연속 순매수")
    elif f5 > 0:
        score += 1
        signals.append(f"외국인 5일 순매수 {f5:+,}주")

    if i_consec >= 3:
        score += 2
        signals.append(f"기관 {i_consec}일 연속 순매수")
    elif i5 > 0:
        score += 1
        signals.append(f"기관 5일 순매수 {i5:+,}주")

    if f5 > 0 and i5 > 0:
        score += 1
        signals.append("⚡ 외국인+기관 쌍끌이 매수")

    if f5 < 0 and i5 < 0:
        score -= 1
        signals.append("⚠️ 외국인+기관 동반 매도")

    return {
        'score': score,
        'signals': signals,
        'data': {
            'foreign_5d': f5,
            'institution_5d': i5,
            'foreign_10d': f10,
            'institution_10d': i10,
            'foreign_consec': f_consec,
            'institution_consec': i_consec,
            'daily': recent_5
        }
    }


# ============================================================
# 참고 지표 (필터 아님)
# ============================================================
def calculate_reference_indicators(prices):
    """OBV, MFI, 볼린저밴드폭, 정배열 등 참고지표"""
    info = {}
    closes = [p['close'] for p in prices]

    # OBV 추세
    if len(prices) >= 10:
        obv = [0]
        for i in range(1, len(prices)):
            if prices[i]['close'] > prices[i-1]['close']:
                obv.append(obv[-1] + prices[i]['volume'])
            elif prices[i]['close'] < prices[i-1]['close']:
                obv.append(obv[-1] - prices[i]['volume'])
            else:
                obv.append(obv[-1])

        obv_10 = obv[-10:]
        x = np.arange(len(obv_10))
        slope = np.polyfit(x, obv_10, 1)[0]
        info['obv_trend'] = 'up' if slope > 0 else 'down'
        info['obv_label'] = 'OBV 상승 (매집 가능)' if slope > 0 else 'OBV 하락'

    # MFI
    if len(prices) >= 15:
        pos_flow = 0
        neg_flow = 0
        for i in range(len(prices)-14, len(prices)):
            tp = (prices[i].get('high', prices[i]['close']) +
                  prices[i].get('low', prices[i]['close']) +
                  prices[i]['close']) / 3
            prev_tp = (prices[i-1].get('high', prices[i-1]['close']) +
                       prices[i-1].get('low', prices[i-1]['close']) +
                       prices[i-1]['close']) / 3
            mf = tp * prices[i]['volume']
            if tp > prev_tp:
                pos_flow += mf
            else:
                neg_flow += mf
        if neg_flow > 0:
            mfi = 100 - (100 / (1 + pos_flow / neg_flow))
        else:
            mfi = 100
        info['mfi'] = round(mfi, 1)
        if mfi <= 20:
            info['mfi_label'] = f'MFI {mfi:.0f} — 과매도 (반등 가능)'
        elif mfi >= 80:
            info['mfi_label'] = f'MFI {mfi:.0f} — 과매수 주의'
        else:
            info['mfi_label'] = f'MFI {mfi:.0f}'

    # 볼린저밴드 폭
    if len(prices) >= 20:
        c20 = closes[-20:]
        ma20 = np.mean(c20)
        std20 = np.std(c20)
        bb_upper = ma20 + 2 * std20
        bb_lower = ma20 - 2 * std20
        bb_width = ((bb_upper - bb_lower) / ma20) * 100
        info['bb_width'] = round(bb_width, 1)
        info['bb_upper'] = round(bb_upper)
        info['bb_lower'] = round(bb_lower)
        if bb_width < 8:
            info['bb_label'] = f'밴드폭 {bb_width:.1f}% — 스퀴즈 (에너지 축적)'
        else:
            info['bb_label'] = f'밴드폭 {bb_width:.1f}%'

    # 정배열
    if len(prices) >= 60:
        ma5 = np.mean(closes[-5:])
        ma20 = np.mean(closes[-20:])
        ma60 = np.mean(closes[-60:])
        if ma5 > ma20 > ma60:
            info['alignment'] = 'perfect'
            info['alignment_label'] = '정배열 (5>20>60)'
        elif ma5 > ma20:
            info['alignment'] = 'partial'
            info['alignment_label'] = '단기 정배열 (5>20)'
        else:
            info['alignment'] = 'none'
            info['alignment_label'] = '역배열'

    return info


# ============================================================
# 악재 키워드 감지
# ============================================================
RISK_KEYWORDS = [
    '유상증자', '감자', '무상감자', '관리종목', '상장폐지', '투자주의',
    '횡령', '배임', '소송', '분식회계', '자본잠식', '부도', '워크아웃',
    '불성실공시', '거래정지', '투자경고', '보호예수해제', '대주주매도',
    '자사주처분', 'CB전환', 'BW행사', '전환사채', '신주인수권',
]


def check_risk_keywords(stock_name, news_articles=None):
    """악재 키워드 감지"""
    risks = []
    if news_articles:
        for article in news_articles:
            title = article.get('title', '')
            for kw in RISK_KEYWORDS:
                if kw in title and stock_name in title:
                    risks.append({'keyword': kw, 'title': title})
    return risks


# ============================================================
# 매매 가이드
# ============================================================
def build_trading_guide(prices, box_info=None):
    """실전 매매 가이드 (1~2주 스윙)"""
    if not prices or len(prices) < 5:
        return {}

    current = prices[-1]['close']
    if current <= 0:
        return {}

    closes = [p['close'] for p in prices[-20:]]
    highs = [p.get('high', p['close']) for p in prices[-20:]]
    lows = [p.get('low', p['close']) for p in prices[-20:]]

    support = min(lows)
    resistance = max(highs)

    # ATR (14일)
    atr_values = []
    for i in range(max(1, len(prices)-14), len(prices)):
        h = prices[i].get('high', prices[i]['close'])
        l = prices[i].get('low', prices[i]['close'])
        prev_c = prices[i-1]['close']
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        atr_values.append(tr)
    atr = np.mean(atr_values) if atr_values else current * 0.03

    # 박스권 기반 가이드
    if box_info:
        entry_low = round(box_info['box_low'])
        entry_high = round(box_info['box_low'] + (box_info['box_high'] - box_info['box_low']) * 0.3)
        stop_loss = round(box_info['box_low'] * 0.97)  # 박스 하단 -3%
        target1 = round(box_info['box_high'] * 1.02)  # 박스 상단 +2%
        target2 = round(box_info['box_high'] * 1.07)  # 박스 상단 +7%
    else:
        entry_low = round(current * 0.97)
        entry_high = round(current * 1.01)
        stop_loss = round(current - atr * 2)
        target1 = round(current + atr * 2)
        target2 = round(current + atr * 3.5)

    # 최대 손절 -8% 제한
    max_stop = round(current * 0.92)
    stop_loss = max(stop_loss, max_stop)

    stop_pct = round((stop_loss - current) / current * 100, 1)
    t1_pct = round((target1 - current) / current * 100, 1)
    t2_pct = round((target2 - current) / current * 100, 1)

    loss_amt = current - stop_loss
    profit_amt = target1 - current
    rr = round(profit_amt / loss_amt, 1) if loss_amt > 0 else 0

    # 추세
    if len(prices) >= 20:
        ma5 = np.mean([p['close'] for p in prices[-5:]])
        ma20 = np.mean([p['close'] for p in prices[-20:]])
        if ma5 > ma20 and current > ma5:
            trend = "🟢 상승추세"
        elif ma5 > ma20:
            trend = "🟡 약세 상승"
        elif current > ma20:
            trend = "🟡 횡보"
        else:
            trend = "🔴 하락추세"
    else:
        trend = "⚪ 판단불가"

    return {
        'trend': trend,
        'entry_low': entry_low,
        'entry_high': entry_high,
        'target1': target1,
        'target1_pct': t1_pct,
        'target2': target2,
        'target2_pct': t2_pct,
        'stop_loss': stop_loss,
        'stop_pct': stop_pct,
        'risk_reward': rr,
        'atr': round(atr),
        'support': support,
        'resistance': resistance,
    }


# ============================================================
# 메인 분석 함수
# ============================================================
def analyze_stock_swing(prices, code=None, stock_name='', news_articles=None):
    """
    3단계 순차 필터 스윙 분석

    Args:
        prices: list of dict {'date', 'open', 'high', 'low', 'close', 'volume'}
        code: 종목코드 (수급 조회용)
        stock_name: 종목명 (악재 감지용)
        news_articles: 관련 뉴스 기사 리스트

    Returns:
        dict with grade, signals, trading_guide, reference indicators
    """
    if not prices or len(prices) < 10:
        return {
            'grade': 'D', 'score': 0, 'label': '데이터 부족',
            'color': '#666', 'show': False,
            'signals': [], 'warnings': ['가격 데이터 부족'],
            'stage1': None, 'stage2': None, 'stage3': None,
            'reference': {}, 'trading_guide': {}, 'risks': [],
        }

    signals = []
    warnings = []
    score = 0

    current_price = prices[-1]['close']

    # === 기본 제외 조건 (거래대금 5억 미만) ===
    recent_5 = prices[-5:]
    avg_trade_val = np.mean([p['close'] * p['volume'] for p in recent_5])
    if avg_trade_val < 500_000_000:  # 5억 미만
        return {
            'grade': 'D', 'score': 0, 'label': '거래대금 부족',
            'color': '#666', 'show': False,
            'signals': [], 'warnings': [f'거래대금 {avg_trade_val/100_000_000:.1f}억 (5억 미만)'],
            'stage1': None, 'stage2': None, 'stage3': None,
            'reference': {}, 'trading_guide': {}, 'risks': [],
        }

    # === 1차: 장대양봉 감지 ===
    big_candles = detect_big_candles(prices, lookback=15)
    stage1 = {
        'passed': len(big_candles) > 0,
        'candles': big_candles,
        'count': len(big_candles)
    }

    if big_candles:
        best = max(big_candles, key=lambda x: x['vol_ratio'] * x['body_pct'])
        score += 3
        strength = '🔥 강력' if best['strength'] == 'strong' else '📈'
        signals.append(f"{strength} 장대양봉 ({best['date']}, 거래량 {best['vol_ratio']}배, +{best['body_pct']}%)")
        if len(big_candles) > 1:
            signals.append(f"최근 15일 내 장대양봉 {len(big_candles)}회 출현")
    else:
        warnings.append("15일 내 장대양봉 미감지")

    # === 2차: 박스권 감지 ===
    box_info = detect_high_box(prices, big_candles if big_candles else None)
    box_position = detect_box_position(prices, box_info) if box_info else None

    stage2 = {
        'passed': box_info is not None,
        'box': box_info,
        'position': box_position
    }

    if box_info:
        score += 2
        signals.append(f"📦 박스권 형성 ({box_info['box_days']}일, 변동 {box_info['range_pct']}%)")
        if box_position:
            if box_position['zone'] == 'lower':
                score += 2
                signals.append(f"💎 박스 하단 눌림 구간 (매수 적기)")
            elif box_position['zone'] == 'breakout':
                score += 3
                signals.append(f"🚀 박스 상단 돌파!")
            elif box_position['zone'] == 'upper':
                signals.append(f"📍 박스 상단 부근 (돌파 대기)")

    # === 3차: 외국인/기관 수급 ===
    supply_demand = {'score': 0, 'signals': [], 'data': None}
    if code:
        investor_data = get_investor_data(code)
        supply_demand = analyze_supply_demand(investor_data)
        score += supply_demand['score']
        signals.extend(supply_demand['signals'])
    else:
        warnings.append("종목코드 없음 — 수급 분석 생략")
        supply_demand = {'score': 0, 'signals': [], 'data': None}

    stage3 = {
        'passed': supply_demand.get('score', 0) > 0,
        'supply_demand': supply_demand
    }

    # === 참고 지표 ===
    ref = calculate_reference_indicators(prices)

    # 참고 지표 중 주요 신호만 표시 (점수에 영향 없음)
    ref_signals = []
    if ref.get('obv_trend') == 'up':
        ref_signals.append(f"📊 {ref['obv_label']}")
    if ref.get('mfi') and ref['mfi'] <= 20:
        ref_signals.append(f"💰 {ref['mfi_label']}")
    if ref.get('bb_width') and ref['bb_width'] < 8:
        ref_signals.append(f"⏳ {ref['bb_label']}")
    if ref.get('alignment') == 'perfect':
        ref_signals.append(f"📐 {ref['alignment_label']}")

    # === 악재 감지 ===
    risks = check_risk_keywords(stock_name, news_articles)
    if risks:
        score -= len(risks)
        for r in risks:
            warnings.append(f"⚠️ 악재: {r['keyword']} — {r['title'][:30]}")

    # === 등급 판정 ===
    # A: 장대양봉 + 박스권 + 수급 (또는 장대양봉+박스돌파)
    # B: 장대양봉 + (박스권 OR 수급)
    # C: 장대양봉만 있음
    # D: 장대양봉도 없음

    stages_passed = sum([stage1['passed'], stage2['passed'], stage3['passed']])

    if score >= 8 or (stage1['passed'] and stage2['passed'] and stage3['passed']):
        grade = 'A'
        label = '적극 매수'
        color = '#22c55e'
    elif score >= 5 or (stage1['passed'] and (stage2['passed'] or stage3['passed'])):
        grade = 'B'
        label = '매수 고려'
        color = '#3b82f6'
    elif stage1['passed'] or score >= 2:
        grade = 'C'
        label = '관심 종목'
        color = '#f59e0b'
    else:
        grade = 'D'
        label = '부적합'
        color = '#666'

    # 매매 가이드 (C등급 이상)
    trading_guide = {}
    if grade in ['A', 'B', 'C']:
        trading_guide = build_trading_guide(prices, box_info)

    # A, B 등급만 표시 (C는 관심종목으로 선택적)
    # A/B등급이거나 1차+2차 둘 다 통과한 종목 표시
    show = grade in ['A', 'B'] or (stage1['passed'] and stage2['passed'])

    return {
        'grade': grade,
        'score': score,
        'label': label,
        'color': color,
        'show': show,
        'signals': signals,
        'ref_signals': ref_signals,
        'warnings': warnings,
        'stage1': stage1,
        'stage2': stage2,
        'stage3': stage3,
        'reference': ref,
        'trading_guide': trading_guide,
        'risks': risks,
        'stages_passed': stages_passed,
    }
