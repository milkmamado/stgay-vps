#!/usr/bin/env python3
"""
SCGAY ABCD 자동 추적 크론 (2분 주기, 09:30~14:30 평일).
- 토글 플래그 ON일 때만 동작
- 추적 종목 phase 갱신
- C 확정시 텔레그램 알림 (중복 방지: last_alerted_phase)
"""
import os
import sys
import json
import urllib.request
import urllib.parse
from datetime import datetime, time as dt_time

sys.path.insert(0, '/opt/stock-crawler')

STALKING_FILE = '/opt/stock-crawler/data/scgay_stalking.json'
ENABLED_FLAG = '/opt/stock-crawler/data/scgay_stalking_enabled'
LOG_FILE = '/opt/stock-crawler/data/scgay_cron.log'

BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
CHAT_ID = os.environ.get('SCGAY_ALERT_CHAT_ID', '')

# 알림 발송 phase (C 확정 + C+ = 매수신호)
ALERT_PHASES = {'C', 'C+', 'D'}


def log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def is_market_hours():
    """평일 09:30~14:30만 동작."""
    now = datetime.now()
    if now.weekday() >= 5:  # 토(5), 일(6)
        return False
    t = now.time()
    return dt_time(9, 30) <= t <= dt_time(14, 30)


def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        log("⚠️ TELEGRAM_BOT_TOKEN 또는 SCGAY_ALERT_CHAT_ID 미설정")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        'chat_id': CHAT_ID,
        'text': text,
        'parse_mode': 'HTML',
    }).encode('utf-8')
    try:
        req = urllib.request.Request(url, data=data, method='POST')
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        log(f"❌ 텔레그램 전송 실패: {e}")
        return False


def main():
    # 1. 토글 OFF면 스킵
    if not os.path.exists(ENABLED_FLAG):
        return

    # 2. 장 시간 체크
    if not is_market_hours():
        return

    # 3. 추적 종목 로드
    if not os.path.exists(STALKING_FILE):
        return
    try:
        with open(STALKING_FILE, 'r', encoding='utf-8') as f:
            items = json.load(f)
    except Exception as e:
        log(f"❌ JSON 로드 실패: {e}")
        return

    if not items:
        return

    # 4. 헬퍼 import (app.py 재사용)
    try:
        import app as scgay_app
        abcd_full = scgay_app._scgay_abcd_full
    except Exception as e:
        log(f"❌ app 모듈 로드 실패: {e}")
        return

    # 5. 종목별 phase 체크
    changed = False
    for it in items:
        code = it['code']
        try:
            r = abcd_full(code)
            phase = r.get('phase', 'NONE')
            name = r.get('name', code)

            # 종목명 미설정시 업데이트
            if not it.get('name'):
                it['name'] = name
                changed = True

            # 알림 대상 phase 도달 + 아직 알림 안 보낸 경우
            if phase in ALERT_PHASES and it.get('last_alerted_phase') != phase:
                # === Phase 1: CVD 게이트 (함정 알림 차단) ===
                cvd_signal = r.get('cvd_signal', 'NEUTRAL')
                cvd_divergence = r.get('cvd_divergence', False)
                cvd_reason = r.get('cvd_reason', '')
                cvd = r.get('cvd', 0)
                if cvd_divergence:
                    log(f"🚫 알림 차단 (다이버전스): {name} ({code}) {phase} — {cvd_reason}")
                    continue
                if cvd_signal == 'BEARISH' and phase in ('C', 'C+', 'D'):
                    log(f"🚫 알림 차단 (CVD 매도우위): {name} ({code}) {phase} — {cvd_reason}")
                    continue
                stars = '⭐' * r.get('reliability_stars', 1)
                surge = r.get('surge_from_open_pct', 0)
                msg = (
                    f"🚀 <b>{name}</b> ({code}) <b>{phase}</b> 확정!\n"
                    f"{stars}\n"
                    f"시가 대비: <b>+{surge:.1f}%</b>\n"
                    f"사유: {r.get('reason', '')[:100]}\n"
                    f"📊 CVD: {cvd:+,} ({cvd_reason})\n"
                    f"⏰ {datetime.now().strftime('%H:%M:%S')}"
                )
                if send_telegram(msg):
                    log(f"✅ 알림 전송: {name} ({code}) → {phase}")
                    it['last_alerted_phase'] = phase
                    it['alert_count'] = it.get('alert_count', 0) + 1
                    changed = True
                else:
                    log(f"❌ 알림 실패: {name} ({code})")

            # phase가 알림대상 밖으로 빠지면 리셋 (다음 진입 알림 가능)
            elif phase not in ALERT_PHASES and it.get('last_alerted_phase'):
                it['last_alerted_phase'] = None
                changed = True

        except Exception as e:
            log(f"❌ {code} 체크 실패: {e}")

    # 6. 변경분 저장
    if changed:
        try:
            with open(STALKING_FILE, 'w', encoding='utf-8') as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log(f"❌ JSON 저장 실패: {e}")


if __name__ == '__main__':
    main()
