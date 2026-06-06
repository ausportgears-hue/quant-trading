import sqlite3
from collections import defaultdict

conn = sqlite3.connect('data/trading.db')
cur = conn.cursor()

cur.execute('SELECT * FROM trades ORDER BY opened_at ASC')
cols = [d[0] for d in cur.description]
trades = [dict(zip(cols, row)) for row in cur.fetchall()]

cur.execute('SELECT * FROM equity_snapshots ORDER BY timestamp DESC LIMIT 1')
eq = cur.fetchone()
eq_cols = [d[0] for d in cur.description]
eq_dict = dict(zip(eq_cols, eq)) if eq else {}

conn.close()

total_trades = len(trades)
wins = [t for t in trades if t['pnl'] > 0]
losses = [t for t in trades if t['pnl'] < 0]
total_pnl = sum(t['pnl'] for t in trades)
total_fees = sum(t['fee'] for t in trades)
net_pnl = total_pnl - total_fees
win_rate = len(wins)/total_trades*100 if total_trades > 0 else 0
avg_win = sum(t['pnl'] for t in wins)/len(wins) if wins else 0
avg_loss = sum(t['pnl'] for t in losses)/len(losses) if losses else 0

by_symbol = defaultdict(lambda: {'count':0, 'pnl':0.0, 'fees':0.0, 'wins':0})
for t in trades:
    s = by_symbol[t['symbol']]
    s['count'] += 1
    s['pnl'] += t['pnl']
    s['fees'] += t['fee']
    if t['pnl'] > 0: s['wins'] += 1

by_dir = defaultdict(lambda: {'count':0, 'pnl':0.0, 'wins':0})
for t in trades:
    d = by_dir[t['direction']]
    d['count'] += 1
    d['pnl'] += t['pnl']
    if t['pnl'] > 0: d['wins'] += 1

by_reason = defaultdict(lambda: {'count':0, 'pnl':0.0})
for t in trades:
    r = by_reason[t['close_reason']]
    r['count'] += 1
    r['pnl'] += t['pnl']

print('=== 量化系统交易汇总 ===')
print('运行时段: 2026-06-01 21:43 ~ 2026-06-02 12:28 (北京时间)')
print('总交易数:', total_trades)
print('胜率: %.1f%% (%dW/%dL)' % (win_rate, len(wins), len(losses)))
print('总PnL(毛): $%.2f' % total_pnl)
print('总手续费: $%.2f' % total_fees)
print('净PnL: $%.2f' % net_pnl)
print('平均盈利: $%.2f | 平均亏损: $%.2f' % (avg_win, avg_loss))
if avg_loss != 0:
    print('盈亏比: %.2f' % abs(avg_win/avg_loss))
if eq_dict:
    print('最新权益: $%.2f' % eq_dict.get('equity', 0))
print()
print('--- 按币种 ---')
for sym, data in sorted(by_symbol.items()):
    wr = data['wins']/data['count']*100
    print('%s: %d笔 | 胜率%.0f%% | PnL $%.2f | 手续费 $%.2f' % (sym, data['count'], wr, data['pnl'], data['fees']))
print()
print('--- 按方向 ---')
for direction, data in sorted(by_dir.items()):
    wr = data['wins']/data['count']*100
    print('%s: %d笔 | 胜率%.0f%% | PnL $%.2f' % (direction, data['count'], wr, data['pnl']))
print()
print('--- 按平仓原因 ---')
for reason, data in sorted(by_reason.items()):
    print('%s: %d笔 | PnL $%.2f' % (reason, data['count'], data['pnl']))
print()
print('--- 逐笔明细 ---')
for i, t in enumerate(trades, 1):
    tag = 'W' if t['pnl'] > 0 else 'L'
    print('%d. [%s] %s %s | 入场%.2f -> 出场%.2f | PnL $%.2f (%.2f%%) | 费$%.2f | %s | %s~%s' % (
        i, tag, t['symbol'], t['direction'], t['entry_price'], t['exit_price'],
        t['pnl'], t['pnl_pct']*100, t['fee'], t['close_reason'],
        t['opened_at'][:16], t['closed_at'][:16]))
