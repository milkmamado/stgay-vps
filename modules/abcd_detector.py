"""
ABCD 패턴 판정 모듈 v2 (SCGAY 전용) - 옵션 B 단순화
====================================
역할 분리:
- ABCD = 대장주 후보 발견 (러프, 페이크 알림 일부 OK)
- VWAP = 진입 결정 (별도 API /scgay/api/vwap 사용)

v2 변경 (옵션 B):
- ✅ B 후 최소 30분 경과 (명백한 페이크 차단)
- ✅ C+ 신호탄 (조용한 양봉 = D 임박)
- ❌ 거래량 체크 (VWAP에 위임)
- ❌ 단봉 체크 (VWAP에 위임)
"""

from typing import List, Dict, Any, Optional
from datetime import datetime


MIN_SURGE_FROM_OPEN_PCT = 5.0
MIN_AB_RISE_PCT = 5.0
C_CONSOLIDATION_BARS = 5
C_RANGE_RATIO = 0.30
LOOKBACK_BARS_FOR_A = 12

MIN_BARS_AFTER_B = 6

C_PLUS_BODY_PCT_MIN = 0.02
C_PLUS_BODY_PCT_MAX = 0.05
C_PLUS_VOLUME_RATIO = 0.30


def detect_abcd_phase(
    candles_5min: List[Dict[str, Any]],
    day_open: float,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    now = now or datetime.now()
    result_skeleton = {
        'phase': 'NONE', 'reason': '',
        'a': None, 'a_source': None,
        'b': None, 'c_low': None, 'c_high': None,
        'd_target_min': None, 'd_target_max': None,
        'surge_from_open_pct': 0.0, 'surge_from_a_pct': 0.0,
        'reliability_stars': 0, 'partial': False,
        'bars_after_b': 0,
        'c_plus_signal': False,
        'c_plus_reason': None,
        # === Phase 1: CVD 근사치 ===
        'cvd': 0,
        'cvd_signal': 'NEUTRAL',
        'cvd_divergence': False,
        'cvd_reason': '',
    }

    if not candles_5min or len(candles_5min) < 1:
        result_skeleton['reason'] = '5분봉 데이터 없음'
        return result_skeleton

    # CVD 계산 (모든 phase에서 활용)
    _enrich_with_cvd(result_skeleton, candles_5min)

    is_partial = len(candles_5min) < 6
    result_skeleton['partial'] = is_partial

    a1_price = day_open
    lookback = candles_5min[-LOOKBACK_BARS_FOR_A:]
    a2_price = min(c['low'] for c in lookback)

    if a1_price <= a2_price:
        a_price, a_source = a1_price, '시가'
    else:
        a_price, a_source = a2_price, '직전저점'

    b_price = max(c['high'] for c in candles_5min)
    surge_from_open = (b_price - day_open) / day_open * 100
    surge_from_a = (b_price - a_price) / a_price * 100

    result_skeleton.update({
        'a': round(a_price, 2), 'a_source': a_source,
        'b': round(b_price, 2),
        'surge_from_open_pct': round(surge_from_open, 2),
        'surge_from_a_pct': round(surge_from_a, 2),
    })

    if is_partial:
        if surge_from_open >= MIN_SURGE_FROM_OPEN_PCT:
            result_skeleton['phase'] = 'A→B 진행중'
            result_skeleton['reason'] = '데이터 수집 중 (장 초반)'
            result_skeleton['reliability_stars'] = _calc_reliability_stars(now, surge_from_open)
        else:
            result_skeleton['phase'] = 'A 형성중'
            result_skeleton['reason'] = '데이터 수집 중 (시가 대비 5% 미달)'
        return result_skeleton

    if surge_from_open < MIN_SURGE_FROM_OPEN_PCT:
        result_skeleton['phase'] = 'NONE'
        result_skeleton['reason'] = (
            f'시가 대비 {surge_from_open:.1f}% (대장주 자격 미달, '
            f'{MIN_SURGE_FROM_OPEN_PCT}% 필요)'
        )
        return result_skeleton

    if surge_from_a < MIN_AB_RISE_PCT:
        result_skeleton['phase'] = 'A'
        result_skeleton['reason'] = f'A→B 상승 {surge_from_a:.1f}% (5% 미달)'
        return result_skeleton

    b_idx = max(range(len(candles_5min)), key=lambda i: candles_5min[i]['high'])
    b_time_str = candles_5min[b_idx].get('time', '09:00')
    b_volume = candles_5min[b_idx].get('volume', 0)
    result_skeleton['reliability_stars'] = _calc_reliability_stars_by_time(b_time_str)

    bars_after_b = candles_5min[b_idx + 1:]
    result_skeleton['bars_after_b'] = len(bars_after_b)

    if len(bars_after_b) < C_CONSOLIDATION_BARS:
        result_skeleton['phase'] = 'B'
        result_skeleton['reason'] = f'B 도달, C 형성 대기 중 ({len(bars_after_b)}/{C_CONSOLIDATION_BARS}봉)'
        return result_skeleton

    if len(bars_after_b) < MIN_BARS_AFTER_B:
        result_skeleton['phase'] = 'B→C 대기'
        result_skeleton['reason'] = (
            f'B 직후 {len(bars_after_b)*5}분 경과 '
            f'(최소 {MIN_BARS_AFTER_B*5}분 필요, 페이크 C 1차 차단)'
        )
        return result_skeleton

    recent = bars_after_b[-C_CONSOLIDATION_BARS:]
    c_high = max(c['high'] for c in recent)
    c_low = min(c['low'] for c in recent)
    c_range = c_high - c_low
    ab_range = b_price - a_price
    range_ratio = c_range / ab_range if ab_range > 0 else 1.0

    result_skeleton['c_high'] = round(c_high, 2)
    result_skeleton['c_low'] = round(c_low, 2)
    result_skeleton['d_target_min'] = round(b_price + ab_range * 0.5, 2)
    result_skeleton['d_target_max'] = round(b_price + ab_range * 1.0, 2)

    current_close = candles_5min[-1]['close']

    if current_close > b_price:
        result_skeleton['phase'] = 'D'
        result_skeleton['reason'] = f'B({b_price:.0f}) 돌파, D 진입'
        return result_skeleton

    if range_ratio <= C_RANGE_RATIO and c_low >= a_price:
        result_skeleton['phase'] = 'C'
        result_skeleton['reason'] = (
            f'C 확정 (변동폭 {range_ratio*100:.0f}% / 30%, '
            f'{C_CONSOLIDATION_BARS}봉 보합)'
        )
        c_plus = _detect_c_plus_signal(candles_5min, b_volume)
        if c_plus['signal']:
            result_skeleton['phase'] = 'C+'
            result_skeleton['c_plus_signal'] = True
            result_skeleton['c_plus_reason'] = c_plus['reason']
            result_skeleton['reason'] += f" | 🚨 C+ 신호탄: {c_plus['reason']}"
        return result_skeleton

    if c_low < a_price:
        result_skeleton['phase'] = 'NONE'
        result_skeleton['reason'] = f'A({a_price:.0f}) 이탈 (c_low={c_low:.0f}) - 패턴 무효'
        return result_skeleton

    result_skeleton['phase'] = 'B→C 형성중'
    result_skeleton['reason'] = f'C 형성 진행 중 (변동폭 {range_ratio*100:.0f}% > 30%)'
    return result_skeleton


def _detect_c_plus_signal(candles_5min: List[Dict[str, Any]], b_volume: float) -> Dict[str, Any]:
    if not candles_5min or b_volume <= 0:
        return {'signal': False, 'reason': None}
    
    last = candles_5min[-1]
    last_open = last.get('open', 0)
    last_close = last.get('close', 0)
    last_vol = last.get('volume', 0)
    
    if last_open <= 0:
        return {'signal': False, 'reason': None}
    
    if last_close <= last_open:
        return {'signal': False, 'reason': None}
    
    body_pct = (last_close - last_open) / last_open
    if body_pct < C_PLUS_BODY_PCT_MIN or body_pct > C_PLUS_BODY_PCT_MAX:
        return {'signal': False, 'reason': None}
    
    vol_ratio = last_vol / b_volume
    if vol_ratio > C_PLUS_VOLUME_RATIO:
        return {'signal': False, 'reason': None}
    
    return {
        'signal': True,
        'reason': f'조용한 양봉 +{body_pct*100:.1f}% (거래량 B의 {vol_ratio*100:.0f}%)'
    }


def _calc_reliability_stars_by_time(b_time_str: str) -> int:
    try:
        hh, mm = map(int, b_time_str.split(':'))
        b_minutes = hh * 60 + mm
    except (ValueError, AttributeError):
        return 1
    if b_minutes <= 9 * 60 + 30:
        return 3
    elif b_minutes <= 10 * 60 + 30:
        return 2
    return 1


def _calc_reliability_stars(now: datetime, surge_pct: float) -> int:
    minutes = now.hour * 60 + now.minute
    if minutes <= 9 * 60 + 30 and surge_pct >= MIN_SURGE_FROM_OPEN_PCT:
        return 3
    elif minutes <= 10 * 60 + 30 and surge_pct >= MIN_SURGE_FROM_OPEN_PCT:
        return 2
    return 1


if __name__ == '__main__':
    print('=== TEST 1: 정통 대장주 + C+ 신호탄 (예상 phase: C+) ===')
    sample_good = [
        {'time': '09:00', 'open': 8800, 'high': 9000, 'low': 8800, 'close': 8950, 'volume': 50000},
        {'time': '09:05', 'open': 8950, 'high': 9100, 'low': 8900, 'close': 9050, 'volume': 80000},
        {'time': '09:10', 'open': 9050, 'high': 9200, 'low': 9000, 'close': 9150, 'volume': 100000},
        {'time': '09:15', 'open': 9150, 'high': 9300, 'low': 9100, 'close': 9250, 'volume': 150000},
        {'time': '09:20', 'open': 9250, 'high': 9400, 'low': 9200, 'close': 9350, 'volume': 200000},
        {'time': '09:25', 'open': 9350, 'high': 9450, 'low': 9300, 'close': 9400, 'volume': 300000},
        {'time': '09:30', 'open': 9400, 'high': 9420, 'low': 9300, 'close': 9320, 'volume': 60000},
        {'time': '09:35', 'open': 9320, 'high': 9350, 'low': 9250, 'close': 9280, 'volume': 40000},
        {'time': '09:40', 'open': 9280, 'high': 9320, 'low': 9230, 'close': 9270, 'volume': 30000},
        {'time': '09:45', 'open': 9270, 'high': 9310, 'low': 9250, 'close': 9290, 'volume': 25000},
        {'time': '09:50', 'open': 9290, 'high': 9330, 'low': 9260, 'close': 9310, 'volume': 20000},
        {'time': '09:55', 'open': 9310, 'high': 9350, 'low': 9290, 'close': 9320, 'volume': 18000},
        {'time': '10:00', 'open': 9320, 'high': 9420, 'low': 9310, 'close': 9410, 'volume': 50000},
    ]
    res = detect_abcd_phase(sample_good, day_open=8800)
    for k, v in res.items():
        print(f'  {k}: {v}')

    print()
    print('=== TEST 2: 페이크 C 차단 (예상 phase: B→C 대기) ===')
    sample_fake = [
        {'time': '09:00', 'open': 8800, 'high': 9000, 'low': 8800, 'close': 8950, 'volume': 50000},
        {'time': '09:05', 'open': 8950, 'high': 9100, 'low': 8900, 'close': 9050, 'volume': 80000},
        {'time': '09:10', 'open': 9050, 'high': 9200, 'low': 9000, 'close': 9150, 'volume': 100000},
        {'time': '09:15', 'open': 9150, 'high': 9300, 'low': 9100, 'close': 9250, 'volume': 150000},
        {'time': '09:20', 'open': 9250, 'high': 9400, 'low': 9200, 'close': 9350, 'volume': 200000},
        {'time': '09:25', 'open': 9350, 'high': 9450, 'low': 9300, 'close': 9400, 'volume': 300000},
        {'time': '09:30', 'open': 9400, 'high': 9420, 'low': 9350, 'close': 9380, 'volume': 250000},
        {'time': '09:35', 'open': 9380, 'high': 9410, 'low': 9360, 'close': 9390, 'volume': 220000},
        {'time': '09:40', 'open': 9390, 'high': 9410, 'low': 9370, 'close': 9395, 'volume': 200000},
    ]
    res = detect_abcd_phase(sample_fake, day_open=8800)
    for k, v in res.items():
        print(f'  {k}: {v}')


# ============= Phase 1: CVD (Cumulative Volume Delta) 근사치 =============
def _calc_cvd_proxy(candles):
    cvd = 0
    for c in candles:
        o = c.get('open', 0); cl = c.get('close', 0); v = c.get('volume', 0)
        if cl > o: cvd += v
        elif cl < o: cvd -= v
    return int(cvd)


def _classify_cvd_signal(candles):
    cvd = _calc_cvd_proxy(candles)
    total_vol = sum(c.get('volume', 0) for c in candles) or 1
    ratio = cvd / total_vol
    if ratio >= 0.15: return {'signal': 'BULLISH', 'cvd': cvd, 'ratio': ratio}
    elif ratio <= -0.15: return {'signal': 'BEARISH', 'cvd': cvd, 'ratio': ratio}
    return {'signal': 'NEUTRAL', 'cvd': cvd, 'ratio': ratio}


def _detect_cvd_divergence(candles):
    if len(candles) < 6:
        return {'has_divergence': False, 'reason': '데이터 부족'}
    half = len(candles) // 2
    first_half = candles[:half]; second_half = candles[half:]
    first_high = max(c.get('high', 0) for c in first_half)
    second_high = max(c.get('high', 0) for c in second_half)
    if second_high <= first_high:
        return {'has_divergence': False, 'reason': '가격 신고가 미갱신'}
    first_cvd = _calc_cvd_proxy(first_half); second_cvd = _calc_cvd_proxy(second_half)
    if second_cvd < 0:
        return {'has_divergence': True, 'reason': f'가격 신고가({first_high:.0f}->{second_high:.0f}) but 후반 CVD 음수({second_cvd:,})'}
    if first_cvd > 0 and second_cvd < first_cvd * 0.3:
        return {'has_divergence': True, 'reason': f'가격 신고가 갱신 but CVD 약화({first_cvd:,}->{second_cvd:,})'}
    return {'has_divergence': False, 'reason': 'CVD 정렬 정상'}


def _enrich_with_cvd(result, candles):
    sig = _classify_cvd_signal(candles); div = _detect_cvd_divergence(candles)
    result['cvd'] = sig['cvd']
    result['cvd_signal'] = sig['signal']
    result['cvd_divergence'] = div['has_divergence']
    if div['has_divergence']:
        result['cvd_reason'] = "⚠️ " + div['reason']
    else:
        ratio_pct = sig['ratio'] * 100
        label = {'BULLISH': '🟢 매수우위', 'BEARISH': '🔴 매도우위', 'NEUTRAL': '🟡 중립'}[sig['signal']]
        result['cvd_reason'] = f"{label} ({ratio_pct:+.1f}%)"

