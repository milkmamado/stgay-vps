# SCGAY ABCD systemd 유닛

VPS 재설치 시 복구 절차 (root 세션 기준):

    cd /opt/stock-crawler
    cp systemd/scgay-abcd.* /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable --now scgay-abcd.timer
    systemctl list-timers scgay-abcd.timer

env 파일 (/etc/stock-crawler.env)에 필수:
- TELEGRAM_BOT_TOKEN
- SCGAY_ALERT_CHAT_ID
- KRX_ID
- KRX_PW
