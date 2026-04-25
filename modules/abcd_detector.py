"""
ABCD 패턴 판정 모듈 (SCGAY 전용)
====================================
당일 테마 대장주의 A→B→C→D 가격 흐름을 판별합니다.

A: 당일 시가 또는 직전 30분 저점 중 더 낮은 값 (진짜 지지선)
B: A 이후 당일 최고가 (상승 정점)
C: B 이후 보합 구간 (눌림목, 변동폭 30% 이내 3봉)
D: C 이후 재상승 목표가 (B + (B-A)*0.5 ~ 1.0)
"""

from typing import List, Dict, Any, Optional
from datetime import datetime


# ──────────────────────────────────────────────────────────
# 임계값 상수
# ──────────────────────────────────────────────────────────
MIN_SURGE_FROM_OPEN_PCT = 5.0
MIN_AB_RISE_PCT = 5.0
C_CONSOLIDATION_BARS = 3
C_RANGE_RATIO = 0.30
LOOKBACK_BARS_FOR_A = 12


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
    }

    if not candles_5min or len(candles_5min) < 1:
        result_skeleton['reason'] = '5분봉 데이터 없음'
        return result_skeleton

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
    result_skeleton['reliability_stars'] = _calc_reliability_stars_by_time(b_time_str)

    bars_after_b = candles_5min[b_idx + 1:]

    if len(bars_after_b) < C_CONSOLIDATION_BARS:
        result_skeleton['phase'] = 'B'
        result_skeleton['reason'] = f'B 도달, C 형성 대기 중 ({len(bars_after_b)}/{C_CONSOLIDATION_BARS}봉)'
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
        return result_skeleton

    if c_low < a_price:
        result_skeleton['phase'] = 'NONE'
        result_skeleton['reason'] = f'A({a_price:.0f}) 이탈 (c_low={c_low:.0f}) - 패턴 무효'
        return result_skeleton

    result_skeleton['phase'] = 'B→C 형성중'
    result_skeleton['reason'] = f'C 형성 진행 중 (변동폭 {range_ratio*100:.0f}% > 30%)'
    return result_skeleton


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
    sample = [
        {'time': '09:00', 'open': 8800, 'high': 9000, 'low': 8800, 'close': 8950},
        {'time': '09:05', 'open': 8950, 'high': 9100, 'low': 8900, 'close': 9050},
        {'time': '09:10', 'open': 9050, 'high': 9200, 'low': 9000, 'close': 9150},
        {'time': '09:15', 'open': 9150, 'high': 9300, 'low': 9100, 'close': 9250},
        {'time': '09:20', 'open': 9250, 'high': 9400, 'low': 9200, 'close': 9350},
        {'time': '09:25', 'open': 9350, 'high': 9450, 'low': 9300, 'close': 9400},
        {'time': '09:30', 'open': 9400, 'high': 9420, 'low': 9300, 'close': 9320},
        {'time': '09:35', 'open': 9320, 'high': 9350, 'low': 9250, 'close': 9280},
        {'time': '09:40', 'open': 9280, 'high': 9320, 'low': 9230, 'close': 9270},
    ]
    res = detect_abcd_phase(sample, day_open=8800)
    print('SCGAY ABCD 자체 테스트 (정통 대장주):')
    for k, v in res.items():
        print(f'  {k}: {v}')
