"""v7.7 风控管理器 — 手续费门控50% + 凯利仓位 + 组合风控

v7.0关键变更:
- 手续费门控: 20%→50% (修正逻辑bug)
- 日亏损: 5%→8%
- 周亏损: 10%→15%
- 连亏暂停: 3→5次
- 每日最大交易: 6→12次
"""
import pandas as pd
from datetime import datetime, timedelta
from core.models import Direction, Signal, Position, Trade, RiskState
from config.settings import settings
from adaptive.parameter_adapter import AdaptiveParameterAdapter
from utils.logger import log


class RiskManager:
    """风控管理器 — v7.0"""

    def __init__(self, initial_capital: float = None, adapter=None):
        self.capital = initial_capital or settings.initial_capital
        self.state = RiskState(
            peak_equity=self.capital,
            current_equity=self.capital,
        )
        self._restore_equity_from_db()
        self.trades: list[Trade] = []
        self.adapter = adapter if adapter is not None else AdaptiveParameterAdapter()
        self._stop_cooldown = {}  # v7.5: {symbol: last_stop_time}

    # ── 凯利仓位计算 ──────────────────────────────────────

    def get_dynamic_leverage(self, atr_ratio: float) -> int:
        """v7.6: ATR动态杠杆"""
        if not settings.dynamic_leverage:
            return settings.max_leverage
        # atr_ratio: current_atr / price
        ref = settings.leverage_atr_ref
        if atr_ratio > ref * 1.5:
            return settings.leverage_min  # 极端高波动→最低杠杆
        elif atr_ratio > ref:
            ratio = (atr_ratio - ref) / (ref * 0.5)
            return int(settings.leverage_max - ratio * (settings.leverage_max - settings.leverage_min))
        else:
            return settings.leverage_max   # 低波动→高杠杆

    def calculate_kelly_position(self, symbol: str, signal: Signal) -> dict:
        """v7.6: 动态杠杆 — 根据ATR波动率调整"""
        # Compute dynamic leverage based on ATR ratio
        atr_ratio = signal.atr_value / signal.entry_price if signal.entry_price > 0 else 0.02
        dynamic_lev = self.get_dynamic_leverage(atr_ratio)
        """
        凯利仓位计算:
        1. 半凯利 → 杠杆修正 → 地板保护
        2. 风险预算 → ATR止损 → 名义价值
        3. 爆仓距离检查
        4. 手续费门控
        5. 最小委托检查
        6. 滑点缓冲
        """
        # Step 1: 凯利公式
        p = self.adapter.get_kelly_win_rate()  # v7.4: 自适应胜率
        b = self.adapter.get_kelly_rr()  # v7.4: 自适应盈亏比
        q = 1 - p
        kelly_full = (b * p - q) / b  # = 0.25 = 25%

        # 半凯利
        half_kelly = kelly_full * settings.kelly_fraction  # = 12.5%

        # 杠杆修正
        kelly_pct = half_kelly / (settings.max_leverage / 10)  # = 6.25%

        # 地板保护
        kelly_pct = max(kelly_pct, settings.kelly_floor_pct)  # = 6.25%

        # Step 2: 风险预算
        risk_amount = self.capital * kelly_pct  # $31.25

        # ATR止损幅度
        stop_pct = abs(signal.entry_price - signal.stop_loss) / signal.entry_price

        if stop_pct <= 0:
            return {"approved": False, "reason": "止损幅度为零"}

        # 名义价值 = 风险金额 / 止损幅度
        notional_value = risk_amount / stop_pct

        # Step 3: 总仓位上限检查
        max_notional = self.capital * (self.adapter.get_max_position_ratio() if self.adapter else settings.max_position_ratio) * settings.max_leverage
        if notional_value > max_notional:
            notional_value = max_notional
            log.debug("仓位已触及上限", max_notional=f"${max_notional:,.0f}")

        # Step 4: 手续费门控
        fee = notional_value * settings.fee_rate_total  # 开平仓手续费+滑点
        fee_ratio = fee / risk_amount if risk_amount > 0 else float('inf')

        if fee_ratio > settings.max_fee_ratio_of_risk:
            return {
                "approved": False,
                "reason": f"手续费占风险{fee_ratio:.1%}>门控{settings.max_fee_ratio_of_risk:.0%}",
                "fee_ratio": fee_ratio,
            }

        # Step 5: 最小委托检查
        if notional_value < settings.min_order_value_usdt:
            return {
                "approved": False,
                "reason": f"名义价值${notional_value:.2f}<最小${settings.min_order_value_usdt}",
            }

        # Step 6: 滑点缓冲
        final_notional = notional_value * (1 - settings.slippage_buffer)  # ×0.95

        # 计算实际数量
        quantity = final_notional / signal.entry_price

        # 保证金
        margin = final_notional / settings.max_leverage

        # 爆仓价计算
        liq_price = self._calc_liquidation_price(signal.entry_price, signal.direction)

        result = {
            "approved": True,
            "kelly_pct": kelly_pct,
            "risk_amount": risk_amount,
            "notional_value": notional_value,
            "final_notional": final_notional,
            "quantity": quantity,
            "margin": margin,
            "fee": fee,
            "fee_ratio": fee_ratio,
            "stop_pct": stop_pct,
            "leverage": settings.max_leverage,
            "liquidation_price": liq_price,
            "liq_distance_pct": abs(signal.entry_price - liq_price) / signal.entry_price,
        }

        log.debug(
            "仓位计算完成",
            kelly=f"{kelly_pct:.2%}",
            risk=f"${risk_amount:.2f}",
            notional=f"${final_notional:,.0f}",
            fee=f"${fee:.2f}({fee_ratio:.1%})",
            liq_dist=f"{result['liq_distance_pct']:.1%}",
        )

        return result

    def _calc_liquidation_price(self, entry_price: float, direction: Direction) -> float:
        """爆仓价格计算: entry*(1-1/lev+MMR) for LONG, entry*(1+1/lev-MMR) for SHORT"""
        mmr = settings.maintenance_margin_rate
        lev = settings.max_leverage
        if direction == Direction.LONG:
            return entry_price * (1 - 1/lev + mmr)
        else:
            return entry_price * (1 + 1/lev - mmr)

    # ── 组合风控检查 ──────────────────────────────────────

    def check_risk(self, signal: Signal | None = None) -> tuple[bool, str]:
        """全面风控检查, 返回(是否通过, 原因)"""
        # 暂停状态
        if self.state.is_paused:
            return False, f"交易暂停: {self.state.pause_reason}"

        equity = self.state.current_equity

        # 日亏损检查
        if self.state.daily_pnl < 0:
            daily_loss_pct = abs(self.state.daily_pnl) / self.state.peak_equity
            if daily_loss_pct >= settings.max_daily_loss:
                self._pause(f"日亏损{daily_loss_pct:.1%}≥{settings.max_daily_loss:.0%}")
                return False, self.state.pause_reason

        # 周亏损检查
        if self.state.weekly_pnl < 0:
            weekly_loss_pct = abs(self.state.weekly_pnl) / self.state.peak_equity
            if weekly_loss_pct >= settings.max_weekly_loss:
                self._pause(f"周亏损{weekly_loss_pct:.1%}≥{settings.max_weekly_loss:.0%}")
                return False, self.state.pause_reason

        # 最大回撤检查
        drawdown = (self.state.peak_equity - equity) / self.state.peak_equity
        if drawdown >= settings.max_drawdown:
            self._pause(f"回撤{drawdown:.1%}≥{settings.max_drawdown:.0%}")
            return False, self.state.pause_reason

        # 连亏暂停
        if self.state.consecutive_losses >= settings.consecutive_loss_pause:
            self._pause(f"连亏{self.state.consecutive_losses}次≥{settings.consecutive_loss_pause}")
            return False, self.state.pause_reason

        # v7.5: 止损冷却期检查
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        for symbol, stop_time in self._stop_cooldown.items():
            cooldown = settings.stop_cooldown_minutes
            elapsed = (now - stop_time).total_seconds() / 60
            if elapsed < cooldown:
                return False, f"{symbol}止损冷却中({elapsed:.0f}/{cooldown}min)"

        # 每日交易次数
        if self.state.daily_trades >= settings.max_daily_trades:
            return False, f"今日交易{self.state.daily_trades}次已达上限{settings.max_daily_trades}"

        return True, "风控通过"

    def should_reduce_position(self) -> bool:
        """连亏降仓: 连亏3次→仓位×0.5"""
        return self.state.consecutive_losses >= settings.consecutive_loss_reduce

    # ── 状态更新 ──────────────────────────────────────

    def update_after_trade(self, trade: Trade):
        """交易完成后更新风控状态"""
        self.trades.append(trade)
        self.state.daily_pnl += trade.pnl
        # v7.6: daily_pnl now tracked from equity delta for snapshots
        self.state.weekly_pnl += trade.pnl
        self.state.total_pnl += trade.pnl
        if trade.close_reason != "partial":
            self.state.daily_trades += 1  # v7.5: partial不计入日交易次数
        self.state.current_equity += trade.pnl - trade.fee

        # v7.5: 记录止损时间用于冷却
        if trade.close_reason == "stop_loss":
            from datetime import datetime, timezone
            self._stop_cooldown[trade.symbol] = datetime.now(timezone.utc)

        if trade.pnl < 0:
            self.state.consecutive_losses += 1
        else:
            self.state.consecutive_losses = 0

        if self.state.current_equity > self.state.peak_equity:
            self.state.peak_equity = self.state.current_equity

        # v7.4: 自适应参数更新
        self.adapter.on_trade_completed(trade)

        log.info(
            "风控状态更新",
            pnl=f"${trade.pnl:+.2f}",
            equity=f"${self.state.current_equity:.2f}",
            daily_pnl=f"${self.state.daily_pnl:+.2f}",
            consecutive_losses=self.state.consecutive_losses,
            drawdown=f"{self._current_drawdown():.1%}",
        )

    def reset_daily(self):
        """每日重置"""
        self.state.daily_pnl = 0.0
        self.state.daily_trades = 0
        self.state._daily_start_equity = self.state.current_equity  # v7.6: snapshot daily_pnl
        self.state.is_paused = False
        self.state.pause_reason = ""

    def reset_weekly(self):
        """每周重置"""
        self.state.weekly_pnl = 0.0

    @property
    def snapshot_daily_pnl(self) -> float:
        """v7.6: 基于权益增量的日盈亏（不受reset_daily影响）"""
        return self.state.current_equity - getattr(self.state, '_daily_start_equity', self.state.current_equity)

    def _pause(self, reason: str):
        self.state.is_paused = True
        self.state.pause_reason = reason
        log.warning(f"⛔ 交易暂停: {reason}")

    def _restore_equity_from_db(self):
        """v7.5: 从数据库恢复历史权益，避免重启后权益归零"""
        try:
            import sqlite3
            db_path = "data/trading.db"
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            # 累计净盈亏(含手续费)
            c.execute("SELECT COALESCE(SUM(pnl - fee), 0) FROM trades")
            total_net_pnl = c.fetchone()[0]
            # 今日毛盈亏
            c.execute("SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE closed_at >= date('now')")
            today_pnl = c.fetchone()[0]
            # 今日交易次数(不含partial)
            c.execute("SELECT COUNT(*) FROM trades WHERE close_reason != 'partial' AND closed_at >= date('now')")
            today_trades = c.fetchone()[0]
            # 本周盈亏
            c.execute("SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE closed_at >= date('now', 'weekday 1', '-7 days')")
            weekly_pnl = c.fetchone()[0]
            conn.close()

            self.state.current_equity = self.capital + total_net_pnl
            self.state.daily_pnl = today_pnl
            self.state.daily_trades = today_trades
            self.state.weekly_pnl = weekly_pnl
            self.state.total_pnl = total_net_pnl
            if self.state.current_equity > self.state.peak_equity:
                self.state.peak_equity = self.state.current_equity
            log.info(
                "权益已从DB恢复",
                equity=f"${self.state.current_equity:.2f}",
                total_pnl=f"${total_net_pnl:+.2f}",
                today_pnl=f"${today_pnl:+.2f}",
            )
        except Exception as e:
            log.warning(f"权益恢复失败，使用默认值: {e}")

    def _current_drawdown(self) -> float:
        if self.state.peak_equity <= 0:
            return 0
        return (self.state.peak_equity - self.state.current_equity) / self.state.peak_equity

    # ── 爆仓监控 ──────────────────────────────────────

    def check_liquidation(self, position: Position) -> str | None:
        """
        爆仓距离分级预警
        返回: 'defensive'(防御平仓) / 'warning'(警告) / None(正常)
        """
        dist = position.liquidation_distance_pct

        if dist < settings.liq_defensive_distance:
            log.critical(
                "🚨 爆仓防御平仓!",
                symbol=position.symbol,
                direction=position.direction.value,
                liq_dist=f"{dist:.2%}",
                liq_price=f"${position.liquidation_price:,.2f}",
            )
            return "defensive"

        if dist < settings.liq_warning_distance:
            log.warning(
                "⚠️ 爆仓预警",
                symbol=position.symbol,
                liq_dist=f"{dist:.2%}",
            )
            return "warning"

        return None

    # ── 极端行情检测 ──────────────────────────────────────

    def check_extreme_move(self, df_15m: pd.DataFrame) -> bool:
        """15分钟内涨跌幅>2.5% → 熔断"""
        if len(df_15m) < 2:
            return False
        price_change_pct = abs(
            (df_15m["close"].iloc[-1] - df_15m["close"].iloc[-2]) / df_15m["close"].iloc[-2]
        )
        if price_change_pct > settings.extreme_move_pct:
            log.critical(
                "🚨 极端行情熔断!",
                move=f"{price_change_pct:.2%}",
                threshold=f"{settings.extreme_move_pct:.1%}",
            )
            return True
        return False

