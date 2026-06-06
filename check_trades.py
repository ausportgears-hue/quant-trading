import sqlite3
conn = sqlite3.connect("data/trading.db")
c = conn.cursor()
c.execute("SELECT symbol, direction, entry_price, exit_price, pnl, close_reason, opened_at, closed_at FROM trades WHERE date(closed_at) = date('now') ORDER BY closed_at DESC LIMIT 20")
rows = c.fetchall()
if rows:
    print("=== Today Closed Trades ===")
    for r in rows:
        closed = r[7][:16] if r[7] else "N/A"
        print(f"{closed} {r[0]:10s} {r[1]:5s} entry={r[2]:>10} exit={r[3]:>10} pnl={r[4]:>+8.2f} {r[5]}")
    c.execute("SELECT count(*), sum(case when pnl>0 then 1 else 0 end), sum(pnl) FROM trades WHERE date(closed_at)=date('now')")
    s = c.fetchone()
    wr = s[1]/s[0]*100 if s[0] else 0
    print(f"\nTotal: {s[0]} trades, {s[1]} win ({wr:.0f}%), net=${s[2]:+.2f}")
else:
    print("No closed trades today")
conn.close()
