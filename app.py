from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from functools import wraps
import threading

from modules.crawler import StockNewsCrawler
from modules.job import run_crawl_job, crawl_state, state_lock
from swing_engine import analyze_stock_swing

app = Flask(__name__)
app.secret_key = 'stgay_stock_crawler_secret_key_2026'
app.config['SESSION_COOKIE_PATH'] = '/'

ACCESS_PASSWORD = '12661266'


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('authenticated'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


@app.route('/stgay/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        password = request.form.get('password', '')
        if password == ACCESS_PASSWORD:
            session['authenticated'] = True
            return redirect('/stgay/')
        return render_template('login.html', error='비밀번호가 틀렸습니다.')
    return render_template('login.html', error=None)

@app.route('/stgay/logout')
def logout():
    session.pop('authenticated', None)
    return redirect('/stgay/login')

@app.route('/stgay/')
@login_required
def index():
    return render_template('index.html')

@app.route('/stgay/api/start', methods=['POST'])
@login_required
def api_start():
    with state_lock:
        if crawl_state['running']:
            return jsonify({'error': '이미 실행 중입니다'}), 400
    data = request.get_json(silent=True) or {}
    mode = data.get('mode', 'all')
    if mode not in ('all', 'swing', 'surge'):
        mode = 'all'
    t = threading.Thread(target=run_crawl_job, kwargs={'mode': mode}, daemon=True)
    t.start()
    return jsonify({'ok': True, 'mode': mode})

@app.route('/stgay/api/status')
@login_required
def api_status():
    with state_lock:
        return jsonify({
            'running': crawl_state['running'],
            'progress': crawl_state['progress'],
            'phase': crawl_state['phase'],
            'percent': crawl_state['percent'],
            'error': crawl_state['error'],
            'has_result': crawl_state['result'] is not None
        })

@app.route('/stgay/api/result')
@login_required
def api_result():
    with state_lock:
        if crawl_state['result']:
            return jsonify(crawl_state['result'])
        return jsonify({'error': '결과 없음'}), 404

@app.route('/stgay/api/theme/<code>')
@login_required
def api_theme_detail(code):
    crawler = StockNewsCrawler()
    detail = crawler.get_theme_detail(code)
    if detail:
        return jsonify(detail)
    return jsonify({'error': '테마 정보를 찾을 수 없습니다'}), 404

@app.route('/stgay/api/stock/<code>/tech')
@login_required
def api_stock_tech(code):
    crawler = StockNewsCrawler()
    prices = crawler.get_stock_price_data(code)
    if not prices or len(prices) < 10:
        return jsonify({'error': '가격 데이터 부족'}), 404
    tech = crawler.calculate_technical_indicators(prices) if len(prices) >= 20 else {}
    current_price = crawler.get_current_price(code)
    if current_price and tech:
        tech['current'] = current_price
    swing = analyze_stock_swing(prices=prices, code=code)
    return jsonify({
        'indicators': tech,
        'swing': swing,
        'prices': [{'date': p['date'], 'open': p['open'], 'high': p['high'],
                     'low': p['low'], 'close': p['close'], 'volume': p['volume']}
                    for p in prices[-60:]]
    })


def _num(v, default=0.0):
    try:
        return float(str(v).replace(',', '').strip())
    except Exception:
        return default

def _int(v, default=0):
    try:
        return int(float(str(v).replace(',', '').strip()))
    except Exception:
        return default

@app.route('/stgay/api/calculate', methods=['POST'])
@app.route('/api/calculate', methods=['POST'])
def api_calculate():
    if not session.get('authenticated'):
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json(silent=True) or request.form.to_dict() or {}

    high = _num(data.get('high') or data.get('box_high') or data.get('resistance') or data.get('high_price'))
    low = _num(data.get('low') or data.get('box_low') or data.get('support') or data.get('low_price'))
    budget = _num(data.get('budget') or data.get('amount') or data.get('capital') or data.get('buy_budget'))
    splits = max(1, min(10, _int(data.get('splits') or data.get('split_count') or 3, 3)))
    holding_qty = max(0, _int(data.get('holding_qty') or data.get('holdingQty') or data.get('qty') or 0, 0))
    mode = str(data.get('mode') or 'equal').strip().lower()

    if high <= 0 or low <= 0 or budget <= 0:
        return jsonify({'error': 'high, low, budget 값이 필요합니다.'}), 400
    if low >= high:
        return jsonify({'error': '저점은 고점보다 작아야 합니다.'}), 400

    if splits == 1:
        buy_prices = [round(low)]
    else:
        step = (high - low) / (splits - 1)
        buy_prices = [round(high - (step * i)) for i in range(splits)]

    if mode in ('pyramid_up', 'up', '업', '피라미드업'):
        weights = [i + 1 for i in range(splits)]
    elif mode in ('pyramid_down', 'down', '다운', '피라미드다운'):
        weights = [splits - i for i in range(splits)]
    else:
        weights = [1] * splits

    weight_sum = sum(weights)
    buy_plan = []
    total_qty = 0
    total_amount = 0

    for i, price in enumerate(buy_prices):
        alloc = budget * (weights[i] / weight_sum)
        qty = int(alloc // price) if price > 0 else 0
        amount = int(qty * price)

        item = {
            'step': i + 1,
            'price': int(price),
            'weight': round(weights[i] / weight_sum * 100, 2),
            'percent': round(weights[i] / weight_sum * 100, 2),
            'qty': qty,
            'quantity': qty,
            'amount': amount
        }
        buy_plan.append(item)
        total_qty += qty
        total_amount += amount

    avg_price = round(total_amount / total_qty, 2) if total_qty > 0 else 0

    sell_plan = []
    if holding_qty > 0:
        target_prices = [round(high * 1.02), round(high * 1.05), round(high * 1.08)]
        base_qty = holding_qty // 3
        remainder = holding_qty % 3
        sell_qtys = [base_qty, base_qty, base_qty]
        for i in range(remainder):
            sell_qtys[i] += 1

        for i, price in enumerate(target_prices):
            item = {
                'step': i + 1,
                'price': int(price),
                'weight': round(sell_qtys[i] / holding_qty * 100, 2) if holding_qty else 0,
                'percent': round(sell_qtys[i] / holding_qty * 100, 2) if holding_qty else 0,
                'qty': sell_qtys[i],
                'quantity': sell_qtys[i],
                'amount': int(sell_qtys[i] * price)
            }
            sell_plan.append(item)

    summary = {
        'buy_total_amount': total_amount,
        'buyTotalAmount': total_amount,
        'buy_total_qty': total_qty,
        'buyTotalQty': total_qty,
        'avg_buy_price': avg_price,
        'avgBuyPrice': avg_price,
        'sell_total_amount': sum(x['amount'] for x in sell_plan),
        'sellTotalAmount': sum(x['amount'] for x in sell_plan),
    }


    for p in buy_plan:
        if isinstance(p, dict):
            p.setdefault('weight_pct', p.get('weight', p.get('percent', 0)))
            p.setdefault('used_budget', p.get('amount', 0))

    for p in sell_plan:
        if isinstance(p, dict):
            p.setdefault('weight_pct', p.get('weight', p.get('percent', 0)))
            p.setdefault('expected_amount', p.get('amount', 0))

    if isinstance(summary, dict):
        buy_summary = summary.get('buy') or {}
        if 'avg_price' not in buy_summary and 'avg_buy_price' in buy_summary:
            buy_summary['avg_price'] = buy_summary.get('avg_buy_price', 0)
        if 'total_amount' not in buy_summary:
            buy_summary['total_amount'] = total_amount
        summary['buy'] = buy_summary

        sell_summary = summary.get('sell') or {}
        if 'total_amount' not in sell_summary:
            sell_summary['total_amount'] = sum(
                int((p.get('expected_amount', p.get('amount', 0)) or 0))
                for p in sell_plan if isinstance(p, dict)
            )
        summary['sell'] = sell_summary

    return jsonify({
        'ok': True,
        'buy_plan': buy_plan,
        'buyPlan': buy_plan,
        'sell_plan': sell_plan,
        'sellPlan': sell_plan,
        'summary': summary
    })




# ============================================================
# scgay — 급등 대장주 전용 (손부장님 스캘핑)
# ============================================================
import json as _scgay_json
import os as _scgay_os
from datetime import datetime as _scgay_dt

SCGAY_ACCESS_PASSWORD = '12661266'
SCGAY_ARCHIVE_FILE = _scgay_os.path.join(_scgay_os.path.dirname(_scgay_os.path.abspath(__file__)), 'data', 'scgay_archive.json')

def scgay_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('scgay_authenticated'):
            return redirect('/scgay/login')
        return f(*args, **kwargs)
    return decorated

@app.route('/scgay/login', methods=['GET', 'POST'])
def scgay_login():
    if request.method == 'POST':
        pw = request.form.get('password', '')
        if pw == SCGAY_ACCESS_PASSWORD:
            session['scgay_authenticated'] = True
            return redirect('/scgay/')
        return render_template('scgay_login.html', error='비밀번호가 틀렸습니다.')
    return render_template('scgay_login.html', error=None)

@app.route('/scgay/logout')
def scgay_logout():
    session.pop('scgay_authenticated', None)
    return redirect('/scgay/login')

@app.route('/scgay/')
@scgay_login_required
def scgay_index():
    return render_template('scgay.html')

@app.route('/scgay/api/scan', methods=['POST'])
@scgay_login_required
def scgay_api_scan():
    """급등 대장주 스캔 시작 (mode=surge 강제)"""
    with state_lock:
        if crawl_state['running']:
            return jsonify({'error': '이미 실행 중입니다'}), 400
        t = threading.Thread(target=run_crawl_job, kwargs={'mode': 'surge'}, daemon=True)
        t.start()
    return jsonify({'ok': True, 'mode': 'surge'})

@app.route('/scgay/api/status')
@scgay_login_required
def scgay_api_status():
    with state_lock:
        return jsonify({
            'running': crawl_state['running'],
            'progress': crawl_state['progress'],
            'phase': crawl_state['phase'],
            'percent': crawl_state['percent'],
            'error': crawl_state['error'],
            'has_result': crawl_state['result'] is not None
        })

@app.route('/scgay/api/result')
@scgay_login_required
def scgay_api_result():
    with state_lock:
        if crawl_state['result']:
            res = crawl_state['result']
            # 자동 아카이빙 (대장주만)
            try:
                leaders = (res or {}).get('surge_leaders', {}).get('leaders', []) or []
                if leaders:
                    _scgay_archive_save(leaders)
            except Exception as _e:
                print(f"[scgay] archive save fail: {_e}")
            return jsonify(res)
    return jsonify({'error': '결과 없음'}), 404

@app.route('/scgay/api/vwap')
@scgay_login_required
def scgay_api_vwap():
    """단일 종목 VWAP/진입/손절 즉시 계산 (네이버 fchart 1분봉)"""
    code = (request.args.get('code') or '').strip()
    if not (code.isdigit() and len(code) == 6):
        return jsonify({'error': '6자리 종목코드를 입력하세요'}), 400
    try:
        from modules.surge_scanner import _fetch_naver_5min_candles, _fetch_naver_realtime_price
        try:
            from pykrx import stock as _pykrx_stock
            stock_name = _pykrx_stock.get_market_ticker_name(code) or code
        except Exception:
            stock_name = code
    except Exception as e:
        return jsonify({'error': f'surge_scanner 모듈 로드 실패: {e}'}), 500

    try:
        candles = _fetch_naver_5min_candles(code, count=390) or []
        current = _fetch_naver_realtime_price(code)
        if not candles or len(candles) < 5:
            return jsonify({'error': '1분봉 데이터 부족 (장 시작 전이거나 거래 정지)'}), 404

        recent = candles[-30:] if len(candles) >= 30 else candles
        total_v = sum(float(c.get('volume', 0)) for c in recent) or 1.0
        vwap = sum(((float(c['high']) + float(c['low']) + float(c['close'])) / 3.0) * float(c.get('volume', 0)) for c in recent) / total_v
        recent_low = min(float(c['low']) for c in recent)
        day_high = max(float(c['high']) for c in candles)
        price = float(current) if current else float(candles[-1]['close'])

        recent_avg_vol = sum(float(c.get('volume', 0)) for c in recent) / max(1, len(recent))
        last_vol = float(recent[-1].get('volume', 0))
        volume_ratio = round(last_vol / max(1.0, recent_avg_vol), 2)
        if volume_ratio >= 1.5:
            volume_signal = '🟢 활발'
        elif volume_ratio >= 1.0:
            volume_signal = '🟡 보통'
        else:
            volume_signal = '🔴 약함'
        vwap_position = '위 (강세)' if price > vwap else ('아래 (약세)' if price < vwap else '동일')
        disparity_pct = round((price - vwap) / max(1.0, vwap) * 100, 2)

        entry_low = round(max(recent_low, vwap * 0.997))
        entry_high = round(vwap * 1.005)
        if entry_high <= entry_low:
            entry_high = round(entry_low * 1.003)
        target1 = round(entry_high * 1.02)
        target2 = round(entry_high * 1.04)
        stop = round(entry_low * 0.98)
        rr = round((target1 - entry_high) / max(1, (entry_high - stop)), 2)

        return jsonify({
            'code': code,
            'name': stock_name,
            'current_price': round(price),
            'vwap': round(vwap),
            'day_high': round(day_high),
            'recent_low': round(recent_low),
            'entry_low': entry_low,
            'entry_high': entry_high,
            'target1': target1,
            'target2': target2,
            'stop': stop,
            'rr_ratio': rr,
            'candle_count': len(candles),
            'volume_ratio': volume_ratio,
            'volume_signal': volume_signal,
            'vwap_position': vwap_position,
            'disparity_pct': disparity_pct,
            'basis': '네이버 1분봉 VWAP+지지',
            'updated_at': _scgay_dt.now().strftime('%Y-%m-%d %H:%M:%S'),
        })
    except Exception as e:
        return jsonify({'error': f'VWAP 계산 실패: {e}'}), 500

def _scgay_archive_load():
    try:
        with open(SCGAY_ARCHIVE_FILE, 'r', encoding='utf-8') as f:
            return _scgay_json.load(f) or []
    except Exception:
        return []

def _scgay_archive_save(leaders):
    """대장주 리스트 받아서 날짜별로 저장 (중복 종목 무시)"""
    archive = _scgay_archive_load()
    today = _scgay_dt.now().strftime('%Y-%m-%d')
    today_codes = {a['code'] for a in archive if a.get('date') == today}
    added = 0
    for item in leaders:
        leader = item.get('leader') or {}
        code = leader.get('code')
        if not code or code in today_codes:
            continue
        scalp = item.get('scalping') or {}
        archive.append({
            'date': today,
            'recorded_at': _scgay_dt.now().strftime('%Y-%m-%d %H:%M:%S'),
            'code': code,
            'name': leader.get('name', ''),
            'theme_name': item.get('theme_name', ''),
            'price': leader.get('price', 0),
            'cum_return_pct': leader.get('cum_return_pct', 0),
            'entry_low': scalp.get('entry_low'),
            'entry_high': scalp.get('entry_high'),
            'target1': scalp.get('target1'),
            'stop': scalp.get('stop'),
            'rr_ratio': scalp.get('rr_ratio'),
            'basis': scalp.get('basis', ''),
        })
        today_codes.add(code)
        added += 1
    if added:
        # 최근 200건만 유지
        archive = archive[-200:]
        with open(SCGAY_ARCHIVE_FILE, 'w', encoding='utf-8') as f:
            _scgay_json.dump(archive, f, ensure_ascii=False, indent=2)
    return added

@app.route('/scgay/api/archive')
@scgay_login_required
def scgay_api_archive():
    archive = _scgay_archive_load()
    # 최신순
    archive.sort(key=lambda x: x.get('recorded_at', ''), reverse=True)
    return jsonify({'items': archive[:100], 'total': len(archive)})

# ──────────────────────────────────────────────────────────
# SCGAY ABCD 패턴 판정 라우트 (Phase 2)
# ──────────────────────────────────────────────────────────
def _aggregate_1min_to_5min(candles_1min):
    """네이버 1분봉 list → 5분봉 list ('HH:MM' 시간키)."""
    if not candles_1min:
        return []
    buckets = {}
    order = []
    for c in candles_1min:
        try:
            t = c['time']
            if len(t) < 12:
                continue
            hh = int(t[8:10]); mm = int(t[10:12])
            bucket_min = (mm // 5) * 5
            key = f"{hh:02d}:{bucket_min:02d}"
            if key not in buckets:
                buckets[key] = {
                    'time': key,
                    'open': float(c['open']),
                    'high': float(c['high']),
                    'low': float(c['low']),
                    'close': float(c['close']),
                    'volume': float(c.get('volume', 0)),
                }
                order.append(key)
            else:
                b = buckets[key]
                b['high'] = max(b['high'], float(c['high']))
                b['low'] = min(b['low'], float(c['low']))
                b['close'] = float(c['close'])
                b['volume'] += float(c.get('volume', 0))
        except (ValueError, KeyError, TypeError):
            continue
    return [buckets[k] for k in order]


@app.route('/scgay/api/abcd_check')
@scgay_login_required
def scgay_api_abcd_check():
    """단일 종목 ABCD 패턴 판정 (5분봉 기준)."""
    code = (request.args.get('code') or '').strip()
    if not (code.isdigit() and len(code) == 6):
        return jsonify({'error': '6자리 종목코드를 입력하세요'}), 400
    try:
        from modules.surge_scanner import _fetch_naver_5min_candles
        from modules.abcd_detector import detect_abcd_phase
    except Exception as e:
        return jsonify({'error': f'모듈 로드 실패: {e}'}), 500
    try:
        from pykrx import stock as _pykrx_stock
        stock_name = _pykrx_stock.get_market_ticker_name(code) or code
    except Exception:
        stock_name = code
    try:
        candles_1min = _fetch_naver_5min_candles(code, count=390) or []
        if not candles_1min:
            return jsonify({'error': '분봉 데이터 없음 (장 시작 전이거나 거래 정지)'}), 404
        candles_5min = _aggregate_1min_to_5min(candles_1min)
        if not candles_5min:
            return jsonify({'error': '5분봉 집계 실패'}), 500
        day_open = candles_5min[0]['open']
        result = detect_abcd_phase(candles_5min, day_open=day_open)
        trackable_phases = {'B', 'B→C 형성중', 'C', 'D'}
        result['trackable'] = result['phase'] in trackable_phases
        result['code'] = code
        result['name'] = stock_name
        result['day_open'] = round(day_open, 2)
        result['candle_count_5min'] = len(candles_5min)
        result['updated_at'] = _scgay_dt.now().strftime('%Y-%m-%d %H:%M:%S')
        return jsonify(result)
    except Exception as e:
        import traceback
        return jsonify({'error': f'ABCD 판정 실패: {e}', 'trace': traceback.format_exc()[-500:]}), 500





# ============= 🍿 육봉 스토킹 (Phase B+C) =============
import os as _stalk_os
import json as _stalk_json
from datetime import datetime as _stalk_dt
from modules.abcd_detector import detect_abcd_phase as _stalk_detect

SCGAY_STALKING_FILE = '/opt/stock-crawler/data/scgay_stalking.json'
SCGAY_STALKING_ENABLED_FLAG = '/opt/stock-crawler/data/scgay_stalking_enabled'


def _stalk_load():
    try:
        with open(SCGAY_STALKING_FILE, 'r', encoding='utf-8') as f:
            return _stalk_json.load(f) or []
    except Exception:
        return []


def _stalk_save(items):
    _stalk_os.makedirs(_stalk_os.path.dirname(SCGAY_STALKING_FILE), exist_ok=True)
    with open(SCGAY_STALKING_FILE, 'w', encoding='utf-8') as f:
        _stalk_json.dump(items, f, ensure_ascii=False, indent=2)


@app.route('/scgay/api/stalking/list')
@scgay_login_required
def scgay_stalking_list():
    """추적 리스트 + 각 종목의 현재 ABCD phase"""
    items = _stalk_load()
    enriched = []
    for it in items:
        try:
            abcd = _stalk_detect(it['code'])
            enriched.append({
                **it,
                'phase': abcd.get('phase', 'NONE'),
                'name': abcd.get('name') or it.get('name', ''),
                'reliability_stars': abcd.get('reliability_stars', 0),
                'surge_from_open_pct': abcd.get('surge_from_open_pct', 0),
                'c_plus_signal': abcd.get('c_plus_signal', False),
                'updated_at': abcd.get('updated_at', ''),
            })
        except Exception as e:
            enriched.append({**it, 'phase': 'ERROR', 'error': str(e)[:80]})
    return jsonify({
        'items': enriched,
        'enabled': _stalk_os.path.exists(SCGAY_STALKING_ENABLED_FLAG),
        'count': len(enriched),
    })


@app.route('/scgay/api/stalking/add', methods=['POST'])
@scgay_login_required
def scgay_stalking_add():
    code = (request.args.get('code') or request.json.get('code') if request.is_json else request.args.get('code', '')).strip() if False else (request.args.get('code') or '').strip()
    # 위 한줄이 복잡해서 다시:
    code = (request.args.get('code') or '').strip()
    if not request.args.get('code') and request.is_json:
        code = (request.json.get('code') or '').strip()
    if not (code.isdigit() and len(code) == 6):
        return jsonify({'error': '6자리 종목코드 필요'}), 400
    items = _stalk_load()
    if any(it['code'] == code for it in items):
        return jsonify({'error': '이미 추적 중', 'code': code}), 409
    # ABCD로 종목명 가져오기
    try:
        abcd = _stalk_detect(code)
        name = abcd.get('name', '')
    except Exception:
        name = ''
    items.append({
        'code': code,
        'name': name,
        'added_at': _stalk_dt.now().strftime('%Y-%m-%d %H:%M:%S'),
        'last_alerted_phase': None,  # C / C+ 알림 중복 방지용
        'alert_count': 0,
    })
    _stalk_save(items)
    return jsonify({'ok': True, 'code': code, 'name': name, 'total': len(items)})


@app.route('/scgay/api/stalking/remove', methods=['POST', 'DELETE'])
@scgay_login_required
def scgay_stalking_remove():
    code = (request.args.get('code') or '').strip()
    if not (code.isdigit() and len(code) == 6):
        return jsonify({'error': '6자리 종목코드 필요'}), 400
    items = _stalk_load()
    new_items = [it for it in items if it['code'] != code]
    if len(new_items) == len(items):
        return jsonify({'error': '추적 목록에 없음', 'code': code}), 404
    _stalk_save(new_items)
    return jsonify({'ok': True, 'code': code, 'remaining': len(new_items)})


@app.route('/scgay/api/stalking/toggle', methods=['POST'])
@scgay_login_required
def scgay_stalking_toggle():
    """크론 ON/OFF 토글 (플래그 파일 존재 여부)"""
    _stalk_os.makedirs(_stalk_os.path.dirname(SCGAY_STALKING_ENABLED_FLAG), exist_ok=True)
    if _stalk_os.path.exists(SCGAY_STALKING_ENABLED_FLAG):
        _stalk_os.remove(SCGAY_STALKING_ENABLED_FLAG)
        return jsonify({'enabled': False, 'msg': '🛑 스토킹 중단'})
    else:
        Path(SCGAY_STALKING_ENABLED_FLAG).touch()
        return jsonify({'enabled': True, 'msg': '▶️ 스토킹 시작'})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5003, debug=False)
