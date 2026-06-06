# 🚀 WorkBuddy Quant v7.7

**Multi-strategy crypto trading bot with AI-powered signal filtering — paper trading proven.**

- **🟢 69.7%** Win Rate · **+$655** PnL · **BTC + SOL**
- ⚡ Momentum + Mean-Reversion dual strategy engine
- 🤖 DeepSeek AI filters fakeouts & confirms entries
- 📉 Dynamic leverage (10x–30x) with time-aware risk control

---

## How It Works

```
        ┌─────────────┐    ┌─────────────┐
 S1 ───►│  MOMENTUM   │───►│   AI FILTER │───► OPEN
 S2 ───►│  REVERSAL   │───►│ (DeepSeek)  │───► OPEN
        └─────────────┘    └─────────────┘
```

| Engine | Style | Timeframe | Trigger |
|--------|-------|-----------|---------|
| **S1 Momentum** | Trend-following (70%) | 4H + 15m | ADX · RSI · EMA · Volume · Structure |
| **S2 Reversal** | RSI oversold bounce (30%) | 15m | RSI extreme + volume spike |
| **AI Guard** | Signal gatekeeper | — | Blocks low-volume / false breakouts |

## Exit System

Breakeven → Partial TP → **Trailing Stop** → Full TP / Stop Loss

> Float your winners, cut your losers — automatically.

## Risk Engine

- **Kelly position sizing** — never overbet
- **ATR-adaptive leverage** — less leverage when volatile
- **Session-aware** — auto reduce exposure on weekends & Friday nights
- **Liquidation defense** — early exit before margin call

## Quick Start

```bash
pip install -r requirements.txt
cp .env.example .env   # add your API keys
python main.py
```

## Live Stats (Paper Trading)

| Metric | Value |
|--------|-------|
| Net PnL | **+$655** |
| Win Rate | **69.7%** |
| Best Pair | BTC/USDT (81.1% WR) |
| Active Since | v7.0, iterated through v7.7 |

---

*For educational use. Crypto trading is high-risk. Past performance ≠ future results.*
