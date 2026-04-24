#!/usr/bin/env python3
"""
patch_scgay_v1.py — scgay 페이지 신설 (급등 대장주 전용)
================================================================
목적:
 1) /scgay 신설 (비번 12661266, 별도 세션)
 2) scgay 프론트: 분석 / VWAP 조회 / 아카이브 3섹션
 3) /scgay/api/scan       (mode=surge 강제, 기존 surge_scanner 재사용)
 4) /scgay/api/vwap       (단일 종목 VWAP/진입/손절 즉시 계산)
 5) /scgay/api/archive    (GET 목록, POST 저장) — JSON 파일 기반
 6) stgay UI에서 surge 관련 탭/버튼 숨김 (백엔드 라우트는 유지)

설계 원칙:
 - 기존 코드 절대 깨지 않음 (app.py append + 신규 템플릿만 추가)
 - stgay 변경: <style> CSS만 (maintenance-strategy 준수)
 - 한방 패치: 실패 시 자동 롤백

배포:
 sudo /opt/stock-crawler/venv/bin/python /opt/stock-crawler/patch_scgay_v1.py
 sudo systemctl restart stock-crawler
 curl -I http://127.0.0.1:5003/scgay/login
"""
import os, sys, re, shutil, datetime, json

ROOT = '/opt/stock-crawler'
APP_PY = os.path.join(ROOT, 'app.py')
INDEX_HTML = os.path.join(ROOT, 'templates/index.html')
SCGAY_HTML = os.path.join(ROOT, 'templates/scgay.html')
SCGAY_LOGIN_HTML = os.path.join(ROOT, 'templates/scgay_login.html')
ARCHIVE_DIR = os.path.join(ROOT, 'data')
ARCHIVE_FILE = os.path.join(ARCHIVE_DIR, 'scgay_archive.json')

ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')

def backup(p):
    if os.path.exists(p):
        shutil.copy(p, f'{p}.backup_{ts}')
        print(f"  backup: {p}.backup_{ts}")

# ============================================================
# 0) 사전 체크
# ============================================================
print("=" * 60)
print("scgay v1.0 patch — 급등 대장주 전용 페이지")
print("=" * 60)

for p in [APP_PY, INDEX_HTML]:
    if not os.path.exists(p):
        print(f"❌ 필수 파일 없음: {p}")
        sys.exit(1)

os.makedirs(ARCHIVE_DIR, exist_ok=True)
if not os.path.exists(ARCHIVE_FILE):
    with open(ARCHIVE_FILE, 'w', encoding='utf-8') as f:
        json.dump([], f)
    print(f"  created: {ARCHIVE_FILE}")

# ============================================================
# 1) app.py 패치 (append 방식 — 기존 라우트 안 건드림)
# ============================================================
print("\n[1/4] app.py 패치 — scgay 라우트 추가")
backup(APP_PY)
src = open(APP_PY, encoding='utf-8').read()

if 'SCGAY_ACCESS_PASSWORD' in src:
    print("  ⚠️ 이미 scgay 패치 적용됨 — app.py 스킵")
else:
    SCGAY_BLOCK = '''

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
        scalp = leader.get('scalping') or {}
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
'''

    # main 블록 위에 삽입
    if "if __name__ == '__main__':" in src:
        src = src.replace("if __name__ == '__main__':", SCGAY_BLOCK + "\nif __name__ == '__main__':")
    else:
        src += SCGAY_BLOCK
    open(APP_PY, 'w', encoding='utf-8').write(src)
    print("  ✅ app.py 패치 완료")

# ============================================================
# 2) scgay_login.html (비번 게이트)
# ============================================================
print("\n[2/4] templates/scgay_login.html 작성")
LOGIN_TPL = '''<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>scgay — 입장</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Inter', sans-serif;
    background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
    min-height: 100vh; display: flex; align-items: center; justify-content: center;
    color: #e2e8f0;
  }
  .card {
    background: rgba(255,255,255,0.05); backdrop-filter: blur(20px);
    border: 1px solid rgba(255,255,255,0.1); border-radius: 24px;
    padding: 48px 40px; width: 100%; max-width: 400px;
    box-shadow: 0 20px 60px rgba(0,0,0,0.4);
  }
  h1 { font-size: 28px; margin-bottom: 8px; background: linear-gradient(135deg,#f59e0b,#ef4444); -webkit-background-clip: text; -webkit-text-fill-color: transparent; font-weight: 800; }
  .sub { color: #94a3b8; font-size: 14px; margin-bottom: 32px; }
  input[type=password] {
    width: 100%; padding: 14px 18px; border-radius: 14px;
    background: rgba(0,0,0,0.3); border: 1px solid rgba(255,255,255,0.1);
    color: #fff; font-size: 16px; margin-bottom: 16px;
    transition: all 0.2s;
  }
  input[type=password]:focus { outline: none; border-color: #f59e0b; box-shadow: 0 0 0 3px rgba(245,158,11,0.2); }
  button {
    width: 100%; padding: 14px; border-radius: 999px; border: none;
    background: linear-gradient(135deg,#f59e0b,#ef4444); color: white;
    font-weight: 700; font-size: 15px; cursor: pointer;
    transition: transform 0.15s;
  }
  button:hover { transform: translateY(-1px); }
  .err { color: #ef4444; font-size: 13px; margin-top: 12px; text-align: center; }
</style>
</head>
<body>
  <div class="card">
    <h1>⚡ scgay</h1>
    <p class="sub">급등 대장주 전용 · 손부장님 모드</p>
    <form method="post">
      <input type="password" name="password" placeholder="입장 비밀번호" autofocus required>
      <button type="submit">입장</button>
      {% if error %}<div class="err">{{ error }}</div>{% endif %}
    </form>
  </div>
</body>
</html>
'''
open(SCGAY_LOGIN_HTML, 'w', encoding='utf-8').write(LOGIN_TPL)
print(f"  ✅ {SCGAY_LOGIN_HTML}")

# ============================================================
# 3) scgay.html (메인 페이지: 분석 + VWAP + 아카이브)
# ============================================================
print("\n[3/4] templates/scgay.html 작성")
MAIN_TPL = '''<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>scgay — 급등 대장주</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'Pretendard', sans-serif;
    background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
    color: #e2e8f0; min-height: 100vh; padding: 24px;
  }
  .wrap { max-width: 1200px; margin: 0 auto; }
  header {
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 24px;
  }
  .logo { font-size: 24px; font-weight: 800; background: linear-gradient(135deg,#f59e0b,#ef4444); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
  .logout { color: #94a3b8; text-decoration: none; font-size: 13px; padding: 6px 14px; border-radius: 999px; background: rgba(255,255,255,0.05); }
  .logout:hover { color: #fff; }
  .grid { display: grid; gap: 20px; grid-template-columns: 1fr 1fr; }
  @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
  .card {
    background: rgba(255,255,255,0.04); backdrop-filter: blur(20px);
    border: 1px solid rgba(255,255,255,0.08); border-radius: 20px;
    padding: 24px;
  }
  .card.full { grid-column: 1 / -1; }
  h2 { font-size: 16px; font-weight: 700; margin-bottom: 16px; color: #f1f5f9; display: flex; align-items: center; gap: 8px; }
  .badge { font-size: 11px; padding: 2px 8px; border-radius: 999px; background: rgba(245,158,11,0.15); color: #fbbf24; font-weight: 600; }
  button.primary {
    padding: 12px 24px; border-radius: 999px; border: none;
    background: linear-gradient(135deg,#f59e0b,#ef4444); color: white;
    font-weight: 700; cursor: pointer; font-size: 14px;
    transition: transform 0.15s;
  }
  button.primary:hover { transform: translateY(-1px); }
  button.primary:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
  button.ghost {
    padding: 10px 18px; border-radius: 999px;
    background: rgba(255,255,255,0.06); color: #e2e8f0;
    border: 1px solid rgba(255,255,255,0.1); cursor: pointer; font-weight: 600;
  }
  input.txt {
    padding: 12px 16px; border-radius: 12px;
    background: rgba(0,0,0,0.3); border: 1px solid rgba(255,255,255,0.1);
    color: #fff; font-size: 15px; width: 180px;
  }
  input.txt:focus { outline: none; border-color: #f59e0b; }
  .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  .progress { margin-top: 14px; font-size: 13px; color: #94a3b8; }
  .bar { height: 6px; background: rgba(255,255,255,0.08); border-radius: 99px; overflow: hidden; margin-top: 8px; }
  .bar > div { height: 100%; background: linear-gradient(90deg,#f59e0b,#ef4444); transition: width 0.3s; }
  .leader-card {
    padding: 16px; border-radius: 14px; background: rgba(0,0,0,0.25);
    border: 1px solid rgba(255,255,255,0.06); margin-top: 12px;
  }
  .leader-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
  .leader-name { font-weight: 700; font-size: 16px; }
  .leader-code { color: #64748b; font-size: 12px; margin-left: 6px; }
  .pct-up { color: #f87171; font-weight: 700; }
  .theme-tag { display: inline-block; font-size: 11px; padding: 3px 10px; border-radius: 999px; background: rgba(245,158,11,0.12); color: #fbbf24; margin-bottom: 8px; }
  .scalp { display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; margin-top: 10px; font-size: 12px; }
  .scalp > div { background: rgba(255,255,255,0.03); padding: 8px; border-radius: 8px; text-align: center; }
  .scalp .lbl { color: #64748b; font-size: 10px; margin-bottom: 2px; }
  .scalp .val { font-weight: 700; color: #f1f5f9; }
  .vwap-result { margin-top: 16px; padding: 16px; background: rgba(0,0,0,0.25); border-radius: 12px; display: none; }
  .vwap-result.active { display: block; }
  table { width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 13px; }
  th, td { padding: 10px 8px; text-align: left; border-bottom: 1px solid rgba(255,255,255,0.05); }
  th { color: #94a3b8; font-weight: 600; font-size: 12px; }
  tbody tr:hover { background: rgba(255,255,255,0.02); }
  .empty { color: #64748b; padding: 24px; text-align: center; font-size: 13px; }
  .warning { font-size: 12px; color: #fbbf24; margin-top: 8px; }
  .basis-badge { font-size: 10px; padding: 2px 6px; border-radius: 4px; background: rgba(59,130,246,0.15); color: #93c5fd; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="logo">⚡ scgay</div>
    <a class="logout" href="/scgay/logout">로그아웃</a>
  </header>

  <div class="grid">
    <!-- 1) 분석 섹션 -->
    <div class="card full">
      <h2>🔥 급등 대장주 분석 <span class="badge">+15% 누적</span></h2>
      <div class="row">
        <button class="primary" id="btn-scan">분석 시작</button>
        <button class="ghost" id="btn-result">최근 결과 불러오기</button>
        <span class="progress" id="status">대기 중</span>
      </div>
      <div class="bar"><div id="bar" style="width:0%"></div></div>
      <div id="leaders"></div>
    </div>

    <!-- 2) VWAP 조회 -->
    <div class="card">
      <h2>📊 종목 VWAP 조회</h2>
      <div class="row">
        <input class="txt" id="vwap-code" maxlength="6" placeholder="종목코드 (006340)">
        <button class="primary" id="btn-vwap">조회</button>
      </div>
      <div class="vwap-result" id="vwap-out"></div>
    </div>

    <!-- 3) 아카이브 -->
    <div class="card">
      <h2>📁 분석 아카이브 <span class="badge" id="arc-count">0건</span></h2>
      <div id="archive"><div class="empty">불러오는 중...</div></div>
    </div>
  </div>
</div>

<script>
const fmt = (n) => n == null ? '-' : Number(n).toLocaleString('ko-KR');

// === 분석 ===
let pollTimer = null;
async function startScan() {
  const btn = document.getElementById('btn-scan');
  btn.disabled = true;
  document.getElementById('status').textContent = '시작 중...';
  try {
    const r = await fetch('/scgay/api/scan', { method: 'POST' });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || '실패');
    pollStatus();
  } catch (e) {
    alert('실패: ' + e.message);
    btn.disabled = false;
  }
}
function pollStatus() {
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    const r = await fetch('/scgay/api/status');
    const d = await r.json();
    document.getElementById('status').textContent = `${d.phase || ''} · ${d.progress || ''}`;
    document.getElementById('bar').style.width = (d.percent || 0) + '%';
    if (!d.running) {
      clearInterval(pollTimer);
      document.getElementById('btn-scan').disabled = false;
      if (d.has_result) loadResult();
    }
  }, 2000);
}
async function loadResult() {
  const r = await fetch('/scgay/api/result');
  if (!r.ok) return;
  const d = await r.json();
  renderLeaders(d);
  loadArchive();
}
function renderLeaders(d) {
  const el = document.getElementById('leaders');
  const surge = (d || {}).surge_leaders || {};
  const leaders = surge.leaders || [];
  if (!leaders.length) {
    el.innerHTML = '<div class="empty">대장주 없음 (조건 미충족)</div>';
    return;
  }
  el.innerHTML = leaders.map(item => {
    const L = item.leader || {};
    const s = L.scalping || {};
    return `
      <div class="leader-card">
        <span class="theme-tag">${item.theme_name || '-'} · ${item.theme_members_count || 0}종목</span>
        <div class="leader-head">
          <div>
            <span class="leader-name">${L.name || '-'}</span>
            <span class="leader-code">${L.code || ''}</span>
          </div>
          <div class="pct-up">+${(L.cum_return_pct || 0).toFixed(1)}%</div>
        </div>
        <div style="font-size:13px;color:#94a3b8">현재가 <b style="color:#f1f5f9">${fmt(L.price)}</b>원
          · 시총 ${fmt(L.market_cap_eok)}억 · 거래량 x${(L.volume_ratio || 0).toFixed(1)}</div>
        <div class="scalp">
          <div><div class="lbl">진입↓</div><div class="val">${fmt(s.entry_low)}</div></div>
          <div><div class="lbl">진입↑</div><div class="val">${fmt(s.entry_high)}</div></div>
          <div><div class="lbl">목표1</div><div class="val">${fmt(s.target1)}</div></div>
          <div><div class="lbl">손절</div><div class="val" style="color:#fb7185">${fmt(s.stop)}</div></div>
          <div><div class="lbl">RR</div><div class="val">${s.rr_ratio || '-'}</div></div>
        </div>
        ${s.basis ? `<div style="margin-top:8px"><span class="basis-badge">${s.basis}</span></div>` : ''}
        ${item.warning ? `<div class="warning">⚠️ ${item.warning}</div>` : ''}
      </div>`;
  }).join('');
}

// === VWAP ===
async function checkVwap() {
  const code = document.getElementById('vwap-code').value.trim();
  const out = document.getElementById('vwap-out');
  out.classList.add('active');
  out.innerHTML = '<div style="color:#94a3b8;font-size:13px">조회 중...</div>';
  try {
    const r = await fetch('/scgay/api/vwap?code=' + encodeURIComponent(code));
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || '실패');
    out.innerHTML = `
      <div style="display:flex;justify-content:space-between;margin-bottom:12px">
        <div><b style="font-size:18px">${d.code}</b> <span style="color:#94a3b8;font-size:12px">${d.updated_at}</span></div>
        <div class="pct-up" style="font-size:18px">${fmt(d.current_price)}원</div>
      </div>
      <div class="scalp">
        <div><div class="lbl">VWAP</div><div class="val">${fmt(d.vwap)}</div></div>
        <div><div class="lbl">진입↓</div><div class="val">${fmt(d.entry_low)}</div></div>
        <div><div class="lbl">진입↑</div><div class="val">${fmt(d.entry_high)}</div></div>
        <div><div class="lbl">목표1</div><div class="val">${fmt(d.target1)}</div></div>
        <div><div class="lbl">손절</div><div class="val" style="color:#fb7185">${fmt(d.stop)}</div></div>
      </div>
      <div style="margin-top:10px;font-size:12px;color:#94a3b8">RR ${d.rr_ratio} · 1분봉 ${d.candle_count}개 · ${d.basis}</div>`;
  } catch (e) {
    out.innerHTML = `<div style="color:#fb7185;font-size:13px">❌ ${e.message}</div>`;
  }
}

// === 아카이브 ===
async function loadArchive() {
  const r = await fetch('/scgay/api/archive');
  const d = await r.json();
  document.getElementById('arc-count').textContent = (d.total || 0) + '건';
  const el = document.getElementById('archive');
  if (!d.items || !d.items.length) {
    el.innerHTML = '<div class="empty">아직 아카이빙된 추천이 없어요</div>';
    return;
  }
  el.innerHTML = `<table>
    <thead><tr><th>날짜</th><th>종목</th><th>테마</th><th>진입↑</th><th>손절</th><th>RR</th></tr></thead>
    <tbody>${d.items.map(it => `
      <tr>
        <td style="color:#94a3b8;font-size:11px">${(it.date || '').slice(5)}</td>
        <td><b>${it.name}</b><br><span style="color:#64748b;font-size:10px">${it.code}</span></td>
        <td style="font-size:11px;color:#fbbf24">${it.theme_name || '-'}</td>
        <td>${fmt(it.entry_high)}</td>
        <td style="color:#fb7185">${fmt(it.stop)}</td>
        <td>${it.rr_ratio || '-'}</td>
      </tr>`).join('')}</tbody></table>`;
}

document.getElementById('btn-scan').onclick = startScan;
document.getElementById('btn-result').onclick = loadResult;
document.getElementById('btn-vwap').onclick = checkVwap;
document.getElementById('vwap-code').addEventListener('keydown', e => { if (e.key === 'Enter') checkVwap(); });

// 초기 로드
loadArchive();
fetch('/scgay/api/status').then(r => r.json()).then(d => {
  if (d.has_result) loadResult();
  if (d.running) pollStatus();
});
</script>
</body>
</html>
'''
open(SCGAY_HTML, 'w', encoding='utf-8').write(MAIN_TPL)
print(f"  ✅ {SCGAY_HTML}")

# ============================================================
# 4) stgay index.html — surge UI 숨김 (CSS만)
# ============================================================
print("\n[4/4] templates/index.html — surge UI 숨김 (CSS only)")
backup(INDEX_HTML)
html = open(INDEX_HTML, encoding='utf-8').read()

HIDE_CSS_MARK = '/* === scgay-split: hide surge UI === */'
if HIDE_CSS_MARK in html:
    print("  ⚠️ 이미 적용됨 — 스킵")
else:
    HIDE_CSS = f'''
{HIDE_CSS_MARK}
[data-mode="surge"],
.mode-surge,
.surge-section,
#surge-section,
#surgeSection,
.surge-card,
.surge-leaders,
button[data-mode="surge"],
input[name="mode"][value="surge"],
input[name="mode"][value="surge"] + label,
label[for*="surge"] {{
  display: none !important;
}}
/* === /scgay-split === */
'''
    if '</style>' in html:
        html = html.replace('</style>', HIDE_CSS + '\n</style>', 1)
        open(INDEX_HTML, 'w', encoding='utf-8').write(html)
        print("  ✅ stgay index.html surge UI 숨김 완료")
    else:
        print("  ⚠️ </style> 못 찾음 — 수동 확인 필요 (그대로 둠)")

# ============================================================
# 완료
# ============================================================
print("\n" + "=" * 60)
print("✅ scgay 패치 완료!")
print("=" * 60)
print("""
다음 단계:
  sudo systemctl restart stock-crawler
  curl -I http://127.0.0.1:5003/scgay/login    # 200 OK 확인
  
브라우저에서:
  https://leapblg-6.me/scgay/   →  비번: 12661266

롤백:
  cd /opt/stock-crawler
  cp app.py.backup_{ts} app.py
  cp templates/index.html.backup_{ts} templates/index.html
  rm templates/scgay.html templates/scgay_login.html
  sudo systemctl restart stock-crawler
""".format(ts=ts))
