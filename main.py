"""v7.7 主入口 — breakeven移止损+TrailingStop+TP2.5x+S2激活"""
import os
import sys
import fcntl
from core.engine import TradingEngine
from config.settings import settings
from utils.logger import log


def main():
    # 进程锁：防止重复启动导致双倍开仓
    lock_file = open("/tmp/quant_trading.lock", "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        print("Another instance is running, exiting")
        log.critical("Duplicate launch blocked: instance already running")
        sys.exit(1)

    mode_str = "paper" if settings.paper_trading else "live"
    log.info("=" * 60)
    log.info("  Quant Trading System v7.7")
    log.info("  Mode: %s" % mode_str)
    log.info("  Capital: $%s | Leverage: %sx" % (settings.initial_capital, settings.max_leverage))
    log.info("  Timeframe: %s+%s" % (settings.direction_timeframe, settings.entry_timeframe))
    log.info("  Strategy: S1(%0.0f%%) + S2(%0.0f%%)" % (settings.s1_weight*100, settings.s2_weight*100))
    log.info("=" * 60)

    # v7.6: 崩溃追踪 — 记录启动次数（systemd已配置StartLimitBurst=5/5min）
    crash_marker = "/tmp/quant_trading.startups"
    try:
        with open(crash_marker, "a") as f:
            from datetime import datetime
            f.write(f"{datetime.utcnow().isoformat()}\n")
        # Check recent startups
        with open(crash_marker) as f:
            lines = f.readlines()
        recent_5min = sum(1 for l in lines if l.strip() and 
                         (datetime.utcnow() - datetime.fromisoformat(l.strip())).total_seconds() < 300)
        if recent_5min > 4:
            log.warning(f"Frequent restarts detected: {recent_5min} in 5min (systemd will stop after {5})")
    except Exception:
        pass

    engine = TradingEngine()

    try:
        engine.run()
    except KeyboardInterrupt:
        log.info("Received stop signal")
    finally:
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()


if __name__ == "__main__":
    main()
