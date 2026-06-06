#!/usr/bin/env python3
"""AI Assisted Trade Report - v2
- AI was enabled since 2026-06-03 15:53 UTC
- All trades opened after that time passed the AI gate (approved signals only)
- v7.5.8+ trades store ai_assisted/ai_reason explicitly in position_meta JSON
"""

import sqlite3, json, os
from datetime import datetime, timezone

DB_PATH = '/opt/quant_trading/data/trading.db'
BACKUP_DIR = '/opt/quant_trading/data/backups'
NOHUP_LOG = '/opt/quant_trading/data/logs/nohup.log'
AI_ENABLED_SINCE = datetime(2026, 6, 3, 15, 53, 0)
AI_ENABLED_SINCE_UTC = datetime(2026, 6, 3, 15, 53, 0, tzinfo=timezone.utc)


def parse_dt(s):
    """Parse various datetime formats. Returns naive datetime (no tz)."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace('Z', '+00:00')).replace(tzinfo=None)
    except (ValueError, TypeError):
        pass
    for fmt in ['%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S',
                '%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S']:
        try:
            return datetime.strptime(str(s)[:26], fmt)
        except ValueError:
            pass
    return None


def get_ai_log_stats():
    """Extract AI decision statistics from nohup.log."""
    approved = 0
    rejected = 0
    stopguard_hold = 0
    stopguard_close = 0
    if os.path.exists(NOHUP_LOG):
        with open(NOHUP_LOG, 'r', errors='ignore') as f:
            for line in f:
                if 'AI approved signal:' in line:
                    approved += 1
                elif 'AI blocked signal' in line:
                    rejected += 1
                elif 'AI StopGuard: HOLD' in line:
                    stopguard_hold += 1
                elif 'AI StopGuard: CLOSE' in line:
                    stopguard_close += 1
    return {
        'approved': approved, 'rejected': rejected,
        'stopguard_hold': stopguard_hold, 'stopguard_close': stopguard_close,
    }


def get_all_trades(db_path):
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM trades ORDER BY opened_at ASC")
    trades = [dict(r) for r in cur.fetchall()]
    conn.close()
    return trades


def is_ai_assisted(trade):
    """Determine if a trade had AI assistance.
    1. Check position_meta JSON (v7.5.8+)
    2. Check if opened after AI was enabled (AI filter is a gate)
    """
    # Check position_meta for explicit AI flag
    meta_raw = trade.get('position_meta')
    if meta_raw and meta_raw.strip():
        try:
            pm = json.loads(meta_raw)
            if pm.get('ai_assisted'):
                return True, pm.get('ai_reason', '')
        except ValueError:
            pass

    # Fallback: trades opened after AI was enabled all went through the filter
    opened = parse_dt(trade.get('opened_at'))
    if opened and opened >= AI_ENABLED_SINCE:
        return True, '(pre-v7.5.8, AI gate)'

    return False, ''


def generate_report():
    # Load trades
    all_trades = get_all_trades(DB_PATH)
    active_ids = {t['id'] for t in all_trades}

    # Also load from backup for historical data
    backups = sorted(
        [f for f in os.listdir(BACKUP_DIR) if f.endswith('.db')],
        reverse=True
    )
    for bkp in backups:
        for bt in get_all_trades(os.path.join(BACKUP_DIR, bkp)):
            if bt['id'] not in active_ids:
                all_trades.append(bt)
                active_ids.add(bt['id'])

    # Separate closed trades
    closed = []
    for t in all_trades:
        ot = parse_dt(t.get('opened_at'))
        ct = parse_dt(t.get('closed_at'))
        if ot and ct and ct > ot:
            closed.append(t)

    # Classify
    ai_trades = []
    non_ai_trades = []
    for t in closed:
        assisted, reason = is_ai_assisted(t)
        t['_ai'] = assisted
        t['_ai_reason'] = reason
        if assisted:
            ai_trades.append(t)
        else:
            non_ai_trades.append(t)

    # Sort by time
    ai_trades.sort(key=lambda t: t.get('opened_at', ''))
    non_ai_trades.sort(key=lambda t: t.get('opened_at', ''))

    # Current equity
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT equity, daily_pnl, drawdown "
        "FROM equity_snapshots ORDER BY timestamp DESC LIMIT 1"
    )
    eq = cur.fetchone()
    conn.close()
    equity = eq['equity'] if eq else 0
    daily_pnl = eq['daily_pnl'] if eq else 0
    drawdown = eq['drawdown'] if eq else 0

    # AI log stats
    ai_stats = get_ai_log_stats()

    # Calculate
    total_pnl = sum(t['pnl'] or 0 for t in closed)
    ai_pnl = sum(t['pnl'] or 0 for t in ai_trades)
    non_ai_pnl = sum(t['pnl'] or 0 for t in non_ai_trades)
    ai_pct = len(ai_trades) / len(closed) * 100 if closed else 0

    ai_win = sum(1 for t in ai_trades if (t['pnl'] or 0) > 0)
    ai_loss = sum(1 for t in ai_trades if (t['pnl'] or 0) < 0)
    non_ai_win = sum(1 for t in non_ai_trades if (t['pnl'] or 0) > 0)
    non_ai_loss = sum(1 for t in non_ai_trades if (t['pnl'] or 0) < 0)

    # === OUTPUT ===
    print(f"EQUITY:{equity:.2f}")
    print(f"DAILY_PNL:{daily_pnl:.2f}")
    print(f"DRAWDOWN:{drawdown*100:.1f}%")
    print(f"TOTAL_CLOSED:{len(closed)}")
    print(f"AI_COUNT:{len(ai_trades)}")
    print(f"AI_PCT:{ai_pct:.1f}%")
    print(f"NON_AI_COUNT:{len(non_ai_trades)}")
    print(f"TOTAL_PNL:{total_pnl:.2f}")
    print(f"AI_PNL:{ai_pnl:.2f}")
    print(f"NON_AI_PNL:{non_ai_pnl:.2f}")
    if ai_win + ai_loss > 0:
        print(f"AI_WIN_RATE:{ai_win}/{ai_win+ai_loss}={ai_win/(ai_win+ai_loss)*100:.1f}%")
    else:
        print("AI_WIN_RATE:N/A")
    if non_ai_win + non_ai_loss > 0:
        print(f"NON_AI_WIN_RATE:{non_ai_win}/{non_ai_win+non_ai_loss}={non_ai_win/(non_ai_win+non_ai_loss)*100:.1f}%")
    else:
        print("NON_AI_WIN_RATE:N/A")
    print(f"AI_LOG_APPROVED:{ai_stats['approved']}")
    print(f"AI_LOG_REJECTED:{ai_stats['rejected']}")
    print(f"AI_STOPGUARD_HOLD:{ai_stats['stopguard_hold']}")
    print(f"AI_STOPGUARD_CLOSE:{ai_stats['stopguard_close']}")

    print("\n=== AI辅助交易 ===")
    for t in ai_trades:
        r = (t.get('_ai_reason', '') or '')[:80].replace('|', '/')
        pnl_s = f"{t['pnl']:.2f}" if t['pnl'] else "0.00"
        pnl_pct_s = f"{t['pnl_pct']*100:.1f}%" if t['pnl_pct'] else "0.0%"
        print(
            f"{t['id']}|{t['symbol']}|{t['direction']}|{t['source']}|"
            f"entry={t['entry_price']}|exit={t['exit_price']}|"
            f"pnl={pnl_s}({pnl_pct_s})|"
            f"open={str(t['opened_at'])[:16]}|close={str(t['closed_at'])[:16]}|"
            f"reason={t['close_reason']}|AI={r}"
        )

    print("\n=== 非AI辅助交易 ===")
    for t in non_ai_trades:
        pnl_s = f"{t['pnl']:.2f}" if t['pnl'] else "0.00"
        pnl_pct_s = f"{t['pnl_pct']*100:.1f}%" if t['pnl_pct'] else "0.0%"
        print(
            f"{t['id']}|{t['symbol']}|{t['direction']}|{t['source']}|"
            f"entry={t['entry_price']}|exit={t['exit_price']}|"
            f"pnl={pnl_s}({pnl_pct_s})|"
            f"open={str(t['opened_at'])[:16]}|close={str(t['closed_at'])[:16]}|"
            f"reason={t['close_reason']}"
        )

    print(f"\n=== 汇总 ===")
    print(f"AI辅助: {len(ai_trades)}/{len(closed)} ({ai_pct:.1f}%) | 盈亏: ${ai_pnl:+.2f} | 胜率: {ai_win}/{ai_win+ai_loss}" if (ai_win+ai_loss) > 0 else f"AI辅助: 0 | 盈亏: $0")
    print(f"非AI: {len(non_ai_trades)}/{len(closed)} ({100-ai_pct:.1f}%) | 盈亏: ${non_ai_pnl:+.2f} | 胜率: {non_ai_win}/{non_ai_win+non_ai_loss}")
    print(f"AI过滤统计: 批准{ai_stats['approved']}次 | 拒绝{ai_stats['rejected']}次 | StopGuard HOLD {ai_stats['stopguard_hold']} | CLOSE {ai_stats['stopguard_close']}")
    print(f"当前权益: ${equity:.2f} | 今日: ${daily_pnl:+.2f} | 回撤: {drawdown*100:.1f}%")


if __name__ == '__main__':
    generate_report()
