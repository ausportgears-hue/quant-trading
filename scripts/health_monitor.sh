#!/bin/bash
# v7.5.2: External health monitor via crontab every 10min
WXPUSHER_TOKEN="AT_4SXteKULbrXkbLCD6VTl7x5WRErgskrC"
WXPUSHER_UID="UID_Oh2gbWKdgan3rEEnxtjymjxWnzBx"
LOG_FILE="/opt/quant_trading/data/logs/nohup.log"
SERVICE_NAME="quant-trading.service"

send_alert() {
    curl -s -X POST "https://wxpusher.zjiecode.com/api/send/message" \
        -H "Content-Type: application/json" \
        -d "{\"appToken\":\"$WXPUSHER_TOKEN\",\"content\":\"$1\",\"contentType\":3,\"uids\":[\"$WXPUSHER_UID\"]}" > /dev/null 2>&1
}

# Check 1: service active
if ! systemctl is-active --quiet $SERVICE_NAME; then
    send_alert "**System Alert**\n\nService $SERVICE_NAME is NOT running! Attempting restart..."
    systemctl restart $SERVICE_NAME
    exit 1
fi

# Check 2: log updated in last 20min
if [ -f "$LOG_FILE" ]; then
    LAST_MOD=$(stat -c %Y "$LOG_FILE" 2>/dev/null || echo 0)
    NOW=$(date +%s)
    DIFF=$(( NOW - LAST_MOD ))
    if [ $DIFF -gt 1200 ]; then
        send_alert "**System Alert**\n\nLog not updated for $((DIFF/60))min - engine may be stuck. Restarting..."
        systemctl restart $SERVICE_NAME
        exit 1
    fi
fi

# Check 3: duplicate processes
PROC_COUNT=$(pgrep -c -f "python3.*main.py" 2>/dev/null || echo 0)
if [ "$PROC_COUNT" -gt 1 ]; then
    SYSTEMD_PID=$(systemctl show $SERVICE_NAME -p MainPID --value)
    for PID in $(pgrep -f "python3.*main.py"); do
        if [ "$PID" != "$SYSTEMD_PID" ]; then
            kill -9 $PID 2>/dev/null
        fi
    done
    send_alert "**System Alert**\n\nKilled duplicate processes ($PROC_COUNT total)"
fi
