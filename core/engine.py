"""v7.7 主交易引擎 — 15m主循环（胜率优化版）

v7.2变更:
- 新增: 同方向最多2个头寸（相关性风控）
- 新增: 4H ADX>20强趋势过滤
- 参数变更由settings.py统一管理

完整交易流程:
1. 拉取4H数据 → 方向判定 + 4H ADX强过滤
2. 拉取15m数据 → S1/S2信号扫描
3. 信号聚合 → 冲突处理
4. 风控检查 → 同向持仓数检查 → 仓位计算
5. 下单执行
6. 持仓管理 → 出场检查
7. 极端行情监控
"""
import uuid
import time
import os
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path
from config.settings import settings
from core.models import Direction, Position, PositionStatus, SignalSource
from core.position_manager import PositionManager
from strategies.strategy1_momentum import Strategy1Momentum
from strategies.strategy2_rsi_divergence import Strategy2RSIDivergence
from strategies.aggregator import SignalAggregator
from risk.risk_manager import RiskManager
from exchange.hyperliquid_adapter import HyperliquidAdapter
from data.storage import DataStorage
from utils.logger import log
from utils.notifier import notifier
from adaptive.parameter_adapter import AdaptiveParameterAdapter
from ai.ai_client import AIClient
from ai.ai_filter import SignalAIFilter


class TradingEngine:
    """v7.2 交易引擎 — 胜率优化版"""

    def __init__(self):
        self.s1 = Strategy1Momentum()
        self.s2 = Strategy2RSIDivergence()
        self.aggregator = SignalAggregator()
        self.adapter = AdaptiveParameterAdapter()
        self.s2.adapter = self.adapter  # v7.6.5: S2 时段自适应
        self.risk_mgr = RiskManager(adapter=self.adapter)
        self.position_mgr = PositionManager()
        self.exchange = HyperliquidAdapter(paper_trading=settings.paper_trading)
        self.storage = DataStorage()
        self._recover_positions()  # v7.5.2: recover from DB

        self.running = False
        self.direction_cache = {}
        if settings.ai_enabled:
            self.ai_client = AIClient()
            self.ai_filter = SignalAIFilter(self.ai_client)
            log.info("AI filter enabled", provider=settings.ai_provider, model=settings.ai_model)
        else:
            self.ai_client = None
            self.ai_filter = None
        self.api_fail_count = 0
        self.cycle_count = 0
        self._last_report_date = None
        self._price_cache = {}  # v7.5: 缓存最新价格
        self._last_healthy_cycle = datetime.now(timezone.utc)  # v7.5.2
        self._health_check_interval = 1800  # v7.5.2: 30min
        self._last_health_check = datetime.now(timezone.utc)  # v7.5.2
        self._last_equity_snapshot = datetime.now(timezone.utc) - timedelta(hours=1)  # v7.6
        self._last_backup_date = None  # v7.6
        self._backup_db()  # v7.6: startup backup

        log.info(
            "🚀 v7.7引擎初始化完成（胜率优化版）",
            capital=f"${settings.initial_capital}",
            leverage=f"{settings.max_leverage}x",
            timeframe=f"{settings.direction_timeframe}+{settings.entry_timeframe}",
            strategies="S1(70%)+S2(30%)",
            paper_trading=settings.paper_trading,
            adx_threshold=settings.adx_threshold,
            s1_confirm=f"{settings.s1_min_confirm}/5",
            max_same_dir=settings.max_same_direction_positions,
        )

    def run(self, cycles: int = 0):
        """运行主循环, cycles=0无限, N=运行N轮"""
        self.running = True
        interval = self._get_interval_seconds()
        log.info(f"🔄 开始交易循环, 间隔{interval}秒 ({settings.entry_timeframe})")

        while self.running:
            try:
                self.cycle_count += 1
                # v7.5: 每日/每周重置风控计数器
                from datetime import date as date_mod
                today = date_mod.today()
                if not hasattr(self, '_last_reset_date') or self._last_reset_date != today:
                    self.risk_mgr.reset_daily()
                    # Monday = weekly reset
                    if today.weekday() == 0 and (not hasattr(self, '_last_reset_week') or self._last_reset_week != today.isocalendar()[1]):
                        self.risk_mgr.reset_weekly()
                        self._last_reset_week = today.isocalendar()[1]
                    self._last_reset_date = today
                    log.info("📅 风控日计数器已重置")
                self._run_cycle()
                if cycles > 0 and self.cycle_count >= cycles:
                    log.info(f"已完成{cycles}轮循环, 停止")
                    break
                time.sleep(interval)
            except KeyboardInterrupt:
                log.info("手动停止")
                self.running = False
                break
            except Exception as e:
                log.error(f"循环异常: {e}")
                self.api_fail_count += 1
                if self.api_fail_count >= settings.api_fail_threshold:
                    log.critical("API连续失败, 暂停交易")
                    notifier.notify_emergency(
                        "API连续失败",
                        [p for p in self.position_mgr.get_all_positions()],
                        paper=settings.paper_trading,
                    )
                    self.running = False
                    break
                time.sleep(30)

    def run_once(self):
        """单轮执行(测试用)"""
        self._run_cycle()

    def _run_cycle(self):
        now = datetime.now(timezone.utc)
        log.debug(f"--- 周期 #{self.cycle_count} @ {now.strftime('%H:%M:%S')} ---")

        for symbol in settings.symbols:
            try:
                self._process_symbol(symbol)
            except Exception as e:
                log.error(f"处理{symbol}异常: {e}")

        # v7.6.5: Session-aware logging every 10 cycles
        if self.cycle_count % 10 == 1:
            from datetime import datetime as _dt
            _now_utc = _dt.utcnow()
            h = _now_utc.hour
            wd = _now_utc.weekday()
            days = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
            try:
                is_we = self.adapter._is_weekend()
                if is_we:
                    sess = "WEEKEND"
                elif h < 8:
                    sess = "Asian"
                elif h < 16:
                    sess = "European"
                else:
                    sess = "US"
                log.info("Session: " + sess + " | UTC " + str(h) + ":00 | " + days[wd] + " | s1_w=" + str(round(self.adapter.get_s1_weight()*100)) + "% s2_vol=" + str(round(self.adapter.get_s2_volume_confirm(),2)) + "x")
            except Exception:
                pass

        self._save_equity_snapshot()
        self._check_daily_report(now)
        self._last_healthy_cycle = datetime.now(timezone.utc)  # v7.5.2
        self._periodic_health_check(now)  # v7.5.2
        self._backup_db()  # v7.6: daily backup
        self._monitor_resources(now)  # v7.6: resource
        self._update_regime_classification()  # v7.6: L2 regime
        self._analyze_signals_loop()  # v7.6: signal analysis

    def _process_symbol(self, symbol: str):
        df_4h = self.exchange.fetch_ohlcv(symbol, settings.direction_timeframe, settings.candle_limit)
        df_15m = self.exchange.fetch_ohlcv(symbol, settings.entry_timeframe, settings.candle_limit)

        if df_4h.empty or df_15m.empty:
            log.warning(f"{symbol}: 数据不足, 跳过")
            return

        # 极端行情
        if self.risk_mgr.check_extreme_move(df_15m):
            self._emergency_close_all("极端行情熔断")
            return

        # 持仓管理
        existing_pos = self.position_mgr.get_open_position(symbol)
        if existing_pos:
            self._manage_position(existing_pos, df_15m)

        # 4H方向
        direction_judgment = self.s1.judge_direction(df_4h)
        self.direction_cache[symbol] = direction_judgment

        if direction_judgment.direction == Direction.NEUTRAL:
            log.debug(f"{symbol}: 4H方向中性, S1跳过但S2仍可触发")
            # v7.3: 4H中性时只跳过S1，仍允许S2反转策略扫描
            sig2 = self.s2.check_entry(df_15m, direction_judgment, symbol=symbol)
            if sig2:
                sig2.symbol = symbol
                self._process_trade_candidate(symbol, [sig2], df_15m, df_4h)
            return

        # v7.3: 4H ADX过滤已由adx_threshold+4H评分机制覆盖，移除冗余过滤

        if existing_pos:
            return

        # v7.5.1: V-reversal filter - skip SHORT when 15m shows strong bounce
        if direction_judgment.direction == Direction.SHORT:
            if self._check_v_reversal(df_15m, symbol):
                log.info(f"{symbol}: V-reversal detected, skip SHORT")
                return

        # v7.5.1: Per-symbol position limit (max 1 per symbol)
        # Check both in-memory positions AND recent DB trades (anti-duplicate for multi-process safety)
        symbol_positions = sum(
            1 for p in self.position_mgr.get_all_positions()
            if p.symbol == symbol and p.status == PositionStatus.OPEN
        )
        db_has_open = self._check_db_open_position(symbol, direction_judgment.direction.value)
        if symbol_positions >= 1 or db_has_open:
            log.debug(f"{symbol}: already has position (mem={symbol_positions}, db={db_has_open}), skip")
            return

        # v7.2新增: 同方向持仓数限制
        same_dir_count = sum(
            1 for p in self.position_mgr.get_all_positions()
            if p.direction == direction_judgment.direction and p.status == PositionStatus.OPEN
        )
        if same_dir_count >= settings.max_same_direction_positions:
            log.debug(
                f"{symbol}: 同方向({direction_judgment.direction.value})持仓已达{same_dir_count}个上限{settings.max_same_direction_positions}",
            )
            return

        # 信号扫描
        signals = []
        sig1 = self.s1.check_entry(df_15m, direction_judgment, symbol=symbol)
        if sig1:
            sig1.symbol = symbol
            signals.append(sig1)
        sig2 = self.s2.check_entry(df_15m, direction_judgment, symbol=symbol)
        if sig2:
            sig2.symbol = symbol
            signals.append(sig2)

        if not signals:
            return

        self._process_trade_candidate(symbol, signals, df_15m, df_4h)

    def _execute_signal(self, symbol, signal, position_info: dict, ai_info: dict = None):
        order = self.exchange.place_order(
            symbol=symbol,
            direction=signal.direction,
            quantity=position_info["quantity"],
            price=signal.entry_price,
            leverage=position_info["leverage"],
        )
        if not order:
            log.warning("下单失败")
            return

        position = Position(
            id=order.get("id", uuid.uuid4().hex[:8]),
            symbol=symbol,
            direction=signal.direction,
            entry_price=signal.entry_price,
            quantity=position_info["quantity"],
            notional_value=position_info["final_notional"],
            margin=position_info["margin"],
            leverage=position_info["leverage"],
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            liquidation_price=position_info["liquidation_price"],
            source=signal.source,
            is_counter_trend=signal.is_counter_trend,
        )
        self.position_mgr.add_position(position)
        self.storage.save_open_position(position, extra_meta=ai_info)  # v7.5.8: AI tracking

        self.storage.save_signal({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol, "source": signal.source.value,
            "direction": signal.direction.value, "strength": signal.strength,
            "entry_price": signal.entry_price, "stop_loss": signal.stop_loss,
            "take_profit": signal.take_profit, "risk_reward": signal.risk_reward,
            "executed": 1, "details": signal.details,
        })

        # 📢 WxPusher开仓通知 (v7.6 filtered)
        if settings.notify_trade_open:
            notifier.notify_trade_open(
            symbol=symbol,
            direction=signal.direction.value,
            price=signal.entry_price,
            quantity=position_info["quantity"],
            leverage=position_info["leverage"],
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            source=signal.source.value,
            paper=settings.paper_trading,
        )

    def _check_db_open_position(self, symbol: str, direction: str) -> bool:
        """v7.5.1: Check DB for recent open trades (anti-duplicate across process restarts).
        If a trade was opened in the last 30 min and not yet closed, consider it active.
        """
        try:
            import sqlite3
            from datetime import datetime, timezone, timedelta
            conn = sqlite3.connect(settings.db_path)
            c = conn.cursor()
            cutoff = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
            c.execute("""
                SELECT COUNT(*) FROM trades
                WHERE symbol = ? AND direction = ? AND pnl = 0
                AND opened_at > ?
            """, (symbol, direction, cutoff))
            count = c.fetchone()[0]
            conn.close()
            return count > 0
        except Exception as e:
            log.warning(f"DB position check error: {e}")
            return False

    def _check_v_reversal(self, df_15m, symbol: str) -> bool:
        """v7.5.1: Detect V-reversal bounce to prevent shorting into a bounce.
        Triggered when 2+ of 3 conditions met:
        1. RSI bounced from low (<50) to above 55
        2. Last 3 x 15m candles all bullish (close > open)
        3. Price bounced >0.8% from recent 8-candle low
        """
        try:
            from core.indicators import rsi
            close = df_15m["close"]
            low = df_15m["low"]
            rsi_val = rsi(close, 14)
            current_rsi = rsi_val.iloc[-1]
            recent_low_rsi = rsi_val.iloc[-8:].min()
            rsi_bounce = recent_low_rsi < 50 and current_rsi > 55
            recent_3_bullish = all(
                close.iloc[-i] > df_15m["open"].iloc[-i]
                for i in range(1, 4)
            )
            recent_low = low.iloc[-8:].min()
            current_price = close.iloc[-1]
            bounce_pct = (current_price - recent_low) / recent_low
            bounce_from_low = bounce_pct > 0.008
            signals = [rsi_bounce, recent_3_bullish, bounce_from_low]
            triggered = sum(signals)
            if triggered >= 2:
                log.info("V-reversal detected", symbol=symbol,
                         rsi=f"{recent_low_rsi:.1f}->{current_rsi:.1f}",
                         bullish=recent_3_bullish, bounce=f"{bounce_pct:.2%}")
                return True
        except Exception as e:
            log.warning(f"V-reversal check error: {e}")
        return False

    def _manage_position(self, position: Position, df_15m):
        current_price = df_15m["close"].iloc[-1]
        high = df_15m["high"].iloc[-1]
        low = df_15m["low"].iloc[-1]

        liq_status = self.risk_mgr.check_liquidation(position)
        if liq_status == "defensive":
            self._close_position(position, current_price, "liquidation_defense")
            return
        elif liq_status == "warning":
            notifier.notify_risk_alert(
                "爆仓预警",
                f"{position.symbol} 距爆仓{position.liquidation_distance_pct:.1%}, "
                f"entry=${position.entry_price:,.2f} liq=${position.liquidation_price:,.2f}",
            )

        exit_reason = self.position_mgr.check_exit_conditions(position, current_price, high, low)

        if exit_reason == "partial":
            trade = self.position_mgr.partial_close(position.id, current_price)
            if trade:
                self.risk_mgr.update_after_trade(trade)
                self.storage.save_trade(trade)
        elif exit_reason == "stop_loss":
            # v7.5.7: AI止损守卫 - 止损触发前AI二次确认是否为噪音扫损
            ai_should_close = True
            if self.ai_filter and self.ai_filter.enabled:
                try:
                    sl_price = position.stop_loss
                    if position.direction.value == "short":
                        stop_dist_pct = abs((current_price - sl_price) / sl_price)
                    else:
                        stop_dist_pct = abs((sl_price - current_price) / sl_price)
                    df_15m = self.exchange.fetch_ohlcv(position.symbol, "15m", limit=25)
                    df_4h = self.exchange.fetch_ohlcv(position.symbol, "4h", limit=25)
                    if df_4h is None:
                        import pandas as pd
                        df_4h = pd.DataFrame()
                    ai_should_close, ai_reasoning, ai_conf = self.ai_filter.check_stop_loss(
                        position, current_price, df_15m, df_4h, stop_dist_pct
                    )
                    if not ai_should_close:
                        log.info("AI StopGuard BLOCKED stop (noise): " + ai_reasoning,
                                 symbol=position.symbol, conf=str(round(ai_conf, 2)))
                        return  # 跳过止损，本轮继续观察
                except Exception as e:
                    log.warning("AI stop guard error, fallback to rule: " + str(e))
            self._close_position(position, current_price, exit_reason)
        elif exit_reason:
            self._close_position(position, current_price, exit_reason)

    def _process_trade_candidate(self, symbol, signals, df_15m, df_4h):
        """v7.7: 公共交易流水线 — aggregate + AI filter + risk + kelly + execute."""
        final_signal = self.aggregator.aggregate(signals)
        if not final_signal:
            return

        ai_info = {"ai_assisted": False, "ai_reason": ""}
        if self.ai_filter and self.ai_filter.enabled:
            ai_state = {
                "equity": round(self.risk_mgr.state.current_equity, 2),
                "daily_pnl": round(self.risk_mgr.state.daily_pnl, 2),
                "consecutive_losses": self.risk_mgr.state.consecutive_losses,
                "open_positions": len(self.position_mgr.get_all_positions()),
                "daily_trades": self.risk_mgr.state.daily_trades,
                "win_rate": self.storage.get_daily_stats().get("win_rate", 0),
            }
            approved, reason, flags = self.ai_filter.check_signal(
                final_signal, df_15m, df_4h, ai_state
            )
            if not approved:
                log.info(f"AI blocked signal [{final_signal.symbol}]: {reason}")
                return
            ai_info = {"ai_assisted": True, "ai_reason": reason}

        risk_ok, risk_reason = self.risk_mgr.check_risk(final_signal)
        if not risk_ok:
            log.info(f"风控拦截: {risk_reason}")
            notifier.notify_risk_alert("信号被风控拦截", risk_reason)
            return

        position_info = self.risk_mgr.calculate_kelly_position(symbol, final_signal)
        if not position_info.get("approved"):
            log.info(f"仓位计算未通过: {position_info.get('reason')}")
            return

        if self.risk_mgr.should_reduce_position():
            position_info["final_notional"] *= settings.reduce_factor
            position_info["quantity"] *= settings.reduce_factor
            log.info("连亏降仓: 仓位×0.5")

        funding_rate = self.exchange.fetch_funding_rate(symbol)
        if abs(funding_rate) > settings.funding_rate_threshold:
            log.info(f"资金费率过高: {funding_rate:.4%}, 跳过")
            return

        self._execute_signal(symbol, final_signal, position_info, ai_info=ai_info)

    def _close_position(self, position: Position, exit_price: float, reason: str):
        trade = self.position_mgr.close_position(position.id, exit_price, reason)
        if trade:
            self.risk_mgr.update_after_trade(trade)
            self.storage.update_trade_on_close(trade)  # v7.5.1: update instead of insert

            # 📢 WxPusher平仓通知 (v7.6 filtered)
            if settings.notify_trade_close:
                notifier.notify_trade_close(
                symbol=position.symbol,
                direction=position.direction.value,
                entry_price=position.entry_price,
                exit_price=exit_price,
                pnl=trade.pnl,
                reason=reason,
                paper=settings.paper_trading,
            )

    def _update_volatility_regime(self):
        """v7.4: 从当前持仓/扫描数据中提取ATR比率，更新波动率环境"""
        for symbol in settings.symbols:
            try:
                df_15m = self.exchange.fetch_ohlcv(symbol, "15m", limit=25)
                if df_15m is not None and len(df_15m) >= 21:
                    from core.indicators import atr
                    atr_vals = atr(df_15m["high"], df_15m["low"], df_15m["close"], 14)
                    if len(atr_vals) >= 20:
                        current_atr = atr_vals.iloc[-1]
                        avg_atr = atr_vals.iloc[-20:].mean()
                        if avg_atr > 0:
                            self.adapter.update_volatility_regime(current_atr / avg_atr)
            except Exception:
                pass

    def _emergency_close_all(self, reason: str):
        log.critical(f"🚨 紧急全平: {reason}")
        all_positions = self.position_mgr.get_all_positions()
        notifier.notify_emergency(reason, all_positions, paper=settings.paper_trading)
        for pos in all_positions:
            ticker = self.exchange.fetch_ticker(pos.symbol)
            exit_price = ticker.get("last", pos.entry_price)
            self._close_position(pos, exit_price, reason)

    def _get_interval_seconds(self) -> int:
        intervals = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400}
        return intervals.get(settings.entry_timeframe, 900)

    def _save_equity_snapshot(self):
        """v7.6: Throttled to hourly by default"""
        now = datetime.now(timezone.utc)
        elapsed = (now - self._last_equity_snapshot).total_seconds()
        if elapsed < settings.equity_snapshot_interval:
            return
        self._last_equity_snapshot = now
        equity = self.risk_mgr.state.current_equity
        drawdown = self.risk_mgr._current_drawdown()
        self.storage.save_equity_snapshot(
            equity=equity, daily_pnl=round(self.risk_mgr.snapshot_daily_pnl, 2),  # v7.6: equity-delta based
            positions=len(self.position_mgr.positions), drawdown=drawdown,
        )

    def _check_daily_report(self, now: datetime):
        """每天UTC 00:00推送日报 — v7.5: DB数据 + 未实现盈亏"""
        today = now.strftime("%Y-%m-%d")
        if self._last_report_date == today:
            return
        if now.hour == 0 and self.cycle_count > 1:
            self._last_report_date = today
            state = self.risk_mgr.state
            # v7.5: 用DB统计(不受reset_daily影响)
            stats = self.storage.get_daily_stats()
            positions = self.position_mgr.get_all_positions()
            # 计算未实现盈亏
            unrealized = 0.0
            for p in positions:
                cur = self._price_cache.get(p.symbol, p.entry_price)
                if p.direction == Direction.LONG:
                    unrealized += (cur - p.entry_price) * p.quantity
                else:
                    unrealized += (p.entry_price - cur) * p.quantity
            net_pnl = stats['total_pnl'] - stats['total_fee']
            total_equity = state.current_equity + unrealized
            notifier.notify_daily_report(
                equity=total_equity,
                daily_pnl=stats['total_pnl'],
                trades=stats['trades'],
                win_rate=stats['win_rate'],
                drawdown=self.risk_mgr._current_drawdown(),
                positions=positions,
                paper=settings.paper_trading,
                total_fee=stats['total_fee'],
                net_pnl=net_pnl,
            )


    def _backup_db(self):
        """v7.6: Daily DB backup, keep N days"""
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        if self._last_backup_date == today:
            return
        self._last_backup_date = today
        try:
            backup_dir = Path(settings.db_backup_dir)
            backup_dir.mkdir(parents=True, exist_ok=True)
            src = Path(settings.db_path)
            if src.exists():
                dst = backup_dir / f'trading-{today}.db'
                shutil.copy2(src, dst)
                log.info(f'DB backed up: {dst}')
                self._cleanup_old_backups(backup_dir)
        except Exception as e:
            log.warning(f'DB backup failed: {e}')

    def _cleanup_old_backups(self, backup_dir: Path):
        """v7.6: Remove backups older than N days"""
        cutoff = datetime.now(timezone.utc) - timedelta(days=settings.db_backup_keep_days)
        for f in backup_dir.glob('trading-*.db'):
            try:
                mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
                if mtime < cutoff:
                    f.unlink()
            except Exception:
                pass

    def _recover_positions(self):
        """v7.5.2: Recover positions from DB to memory on restart"""
        try:
            open_positions = self.storage.get_open_positions()
            if not open_positions:
                log.info("No open positions to recover from DB")
                return
            recovered = 0
            for row in open_positions:
                pos_id = row['id']
                if pos_id in self.position_mgr.positions:
                    continue
                import json as _json
                meta = {}
                if row.get('position_meta'):
                    meta = _json.loads(row['position_meta']) if isinstance(row['position_meta'], str) else row['position_meta']
                position = Position(
                    id=pos_id,
                    symbol=row['symbol'],
                    direction=Direction(row['direction']),
                    entry_price=row['entry_price'],
                    quantity=row['quantity'],
                    notional_value=meta.get('notional_value', row['entry_price'] * row['quantity']),
                    margin=meta.get('margin', row['entry_price'] * row['quantity'] / 20),
                    leverage=meta.get('leverage', settings.max_leverage),
                    stop_loss=meta.get('stop_loss', 0),
                    take_profit=meta.get('take_profit', 0),
                    liquidation_price=meta.get('liquidation_price', 0),
                    source=SignalSource(row['source']),
                    is_counter_trend=bool(row.get('is_counter_trend', 0)),
                    partial_closed=meta.get('partial_closed', False),
                )
                try:
                    from datetime import datetime as _dt
                    position.opened_at = _dt.fromisoformat(row['opened_at'])
                except Exception:
                    pass
                self.position_mgr.positions[pos_id] = position
                recovered += 1
                log.info(f"Recovered position: {pos_id} {row['symbol']} {row['direction']} @ ${row['entry_price']:,.2f}")
            if recovered > 0:
                log.info(f"Recovered {recovered} positions from DB")
                notifier.notify_health_check("Position Recovery", f"Recovered {recovered} positions from DB", is_alert=False)
                # v7.5.9: Restore risk state from database on restart
                try:
                    from datetime import datetime as _dt
                    recent = self.storage.get_recent_trades(limit=20)
                    if recent:
                        consec = 0
                        for t in recent:
                            if t.pnl <= 0:
                                consec += 1
                            else:
                                break
                        self.risk_mgr.consecutive_losses = min(consec, getattr(self.risk_mgr, 'max_consecutive_losses', 5))
                        daily_pnl = 0.0
                        try:
                            snap = self.storage.get_latest_equity_snapshot()
                            if snap and snap.get("daily_pnl"):
                                daily_pnl = float(snap["daily_pnl"])
                        except Exception as e:
                            log.debug("equity snapshot read failed", error=str(e))
                        self.risk_mgr.daily_pnl = daily_pnl
                        log.info(f"Risk state restored: cons_losses={self.risk_mgr.consecutive_losses}, daily_pnl={daily_pnl:.2f}")
                except Exception as e:
                    log.warning(f"Risk restore failed (starting fresh): {e}")
        except Exception as e:
            log.error(f"Position recovery failed: {e}")

    def _periodic_health_check(self, now):
        """v7.5.2: Periodic health check every 30min"""
        elapsed = (now - self._last_health_check).total_seconds()
        if elapsed < self._health_check_interval:
            return
        self._last_health_check = now
        alerts = []

        # 1. Cycle health
        since_healthy = (now - self._last_healthy_cycle).total_seconds()
        if since_healthy > 1800:
            alerts.append(f"Engine no cycle for {since_healthy/60:.0f}min")

        # 2. API connectivity
        try:
            ticker = self.exchange.fetch_ticker(settings.symbols[0])
            if not ticker:
                alerts.append("API returned empty data")
        except Exception as e:
            alerts.append(f"API connection failed: {e}")

        # 3. Position consistency
        try:
            db_opens = self.storage.get_open_positions()
            mem_positions = {pid: p for pid, p in self.position_mgr.positions.items() if p.status == PositionStatus.OPEN}
            db_ids = {r['id'] for r in db_opens}
            mem_ids = set(mem_positions.keys())
            orphaned = db_ids - mem_ids
            if orphaned:
                alerts.append(f"DB has {len(orphaned)} positions not in memory: {orphaned}")
            untracked = mem_ids - db_ids
            if untracked:
                alerts.append(f"Memory has {len(untracked)} positions not in DB: {untracked}")
                for pid in untracked:
                    pos = mem_positions[pid]
                    self.storage.save_open_position(pos)
                    log.info(f"Auto-fix: position {pid} saved to DB")
        except Exception as e:
            alerts.append(f"Position consistency check failed: {e}")

        # 4. Duplicate process check
        try:
            import subprocess
            result = subprocess.run(["pgrep", "-c", "-f", "python3.*main.py"], capture_output=True, text=True, timeout=5)
            count = int(result.stdout.strip()) if result.returncode == 0 else 0
            if count > 1:
                alerts.append(f"Detected {count} trading processes!")
        except Exception:
            pass

        if alerts:
            alert_text = "\\n".join(alerts)
            log.warning(f"Health check ALERT: {alert_text}")
            notifier.notify_health_check("ALERT", alert_text, is_alert=True)
        else:
            log.info(f"Health check OK | cycle#{self.cycle_count} | positions={len(self.position_mgr.positions)} | last_healthy={since_healthy/60:.0f}min ago")

    def _monitor_resources(self, now):
        """v7.6: System resource monitoring with psutil"""
        if not getattr(settings, 'monitor_enabled', False):
            return
        if not hasattr(self, '_last_monitor_time'):
            self._last_monitor_time = now - timedelta(seconds=settings.monitor_interval)
        if (now - self._last_monitor_time).total_seconds() < settings.monitor_interval:
            return
        self._last_monitor_time = now
        try:
            import psutil
            mem = psutil.virtual_memory()
            mem_used_mb = mem.used / 1024 / 1024
            cpu_pct = psutil.cpu_percent(interval=1)
            disk = psutil.disk_usage('/opt')
            disk_pct = disk.percent
            alerts = []
            if mem_used_mb > settings.monitor_memory_warning_mb:
                alerts.append(f'Memory: {mem_used_mb:.0f}MB > {settings.monitor_memory_warning_mb}MB')
            if cpu_pct > settings.monitor_cpu_warning_pct:
                alerts.append(f'CPU: {cpu_pct:.0f}%')
            if disk_pct > settings.monitor_disk_warning_pct:
                alerts.append(f'Disk: {disk_pct:.0f}%')
            if alerts:
                log.warning(f'Resource alert: {"; ".join(alerts)}')
                if settings.notify_health_alert:
                    notifier.notify_health_check('RESOURCE', '; '.join(alerts), is_alert=True)
        except ImportError:
            pass
        except Exception as e:
            log.debug(f'Resource monitor error: {e}')

    def _update_regime_classification(self):
        """v7.6: L2 Market regime classification (rule-based)
        Classifies: TRENDING / RANGING / HIGH_VOL / NORMAL
        Uses 4H BTC data as market benchmark."""
        if not settings.ai_regime_analysis_enabled:
            return
        try:
            from core.indicators import adx, ema, atr
            symbol = settings.symbols[0]
            df_4h = self.exchange.fetch_ohlcv(symbol, '4h', 100)
            if df_4h is None or df_4h.empty or len(df_4h) < 60:
                return
            close = df_4h['close']
            high_v = df_4h['high']
            low_v = df_4h['low']
            adx_val = adx(high_v, low_v, close, 14).iloc[-1]
            ema50 = ema(close, 50).iloc[-1]
            current_price = close.iloc[-1]
            atr_vals = atr(high_v, low_v, close, 14)
            current_atr = atr_vals.iloc[-1]
            avg_atr = atr_vals.iloc[-20:].mean()
            atr_ratio = current_atr / avg_atr if avg_atr > 0 else 1.0
            price_vs_ema = abs(current_price - ema50) / ema50
            if adx_val > 25 and price_vs_ema > 0.03:
                regime = 'TRENDING'
            elif adx_val < 20 and price_vs_ema < 0.02:
                regime = 'RANGING'
            elif atr_ratio > 1.5:
                regime = 'HIGH_VOL'
            else:
                regime = 'NORMAL'
            if not hasattr(self, '_last_regime'):
                self._last_regime = ''
            if regime != self._last_regime:
                log.info(f'L2 Regime: {self._last_regime} -> {regime}', adx=f'{adx_val:.1f}', atr_ratio=f'{atr_ratio:.2f}x')
                self._last_regime = regime
        except Exception as e:
            log.debug(f'Regime classification error: {e}')

    def _analyze_signals_loop(self):
        """v7.6: Periodic signal quality analysis (every ~30 cycles)"""
        if not getattr(settings, 'signal_analysis_enabled', False):
            return
        if self.cycle_count % 30 != 0:
            return
        try:
            recent = self.storage.get_recent_trades(settings.signal_analysis_window)
            if len(recent) < 10:
                return
            s1_trades = [t for t in recent if t.get('source') == 's1_momentum']
            s2_trades = [t for t in recent if t.get('source') == 's2_rsi_divergence']
            s1_wins = [t for t in s1_trades if t.get('pnl', 0) > 0]
            s2_wins = [t for t in s2_trades if t.get('pnl', 0) > 0]
            s1_wr = len(s1_wins) / len(s1_trades) if s1_trades else 0
            s2_wr = len(s2_wins) / len(s2_trades) if s2_trades else 0
            log.info(f'Signal analysis (last {len(recent)} trades)', s1_wr=f'{s1_wr:.0%}', s1_n=len(s1_trades), s2_wr=f'{s2_wr:.0%}', s2_n=len(s2_trades), total_pnl=f'{sum(t.get("pnl", 0) for t in recent):+.2f}')
        except Exception as e:
            log.debug(f'Signal analysis error: {e}')

    def status_report(self) -> str:
        state = self.risk_mgr.state
        stats = self.storage.get_daily_stats()
        positions = self.position_mgr.get_all_positions()
        lines = [
            f"📊 v7.7 状态报告",
            f"  权益: ${state.current_equity:.2f} (峰值: ${state.peak_equity:.2f})",
            f"  回撤: {self.risk_mgr._current_drawdown():.1%}",
            f"  今日: ${state.daily_pnl:+.2f} ({state.daily_trades}笔)",
            f"  连亏: {state.consecutive_losses}次",
            f"  持仓: {len(positions)}个",
            f"  今日胜率: {stats['win_rate']:.0%} ({stats['wins']}/{stats['trades']})",
            f"  暂停: {'是' if state.is_paused else '否'} {state.pause_reason}",
        ]
        for pos in positions:
            lines.append(
                f"  📂 {pos.symbol} {pos.direction.value} "
                f"entry=${pos.entry_price:,.2f} liq={pos.liquidation_distance_pct:.1%} "
                f"hold={pos.hold_hours:.1f}h"
            )
        return "\n".join(lines)
