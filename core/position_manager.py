"""v7.7 持仓管理器 — 爆仓监控 + 部分止盈 + 保本移止损 + 追踪止损 + 持仓超时"""
from datetime import datetime
from core.models import Position, Trade, Direction, PositionStatus, SignalSource
from config.settings import settings
from utils.logger import log


class PositionManager:
    """持仓管理 — v7.0"""

    def __init__(self):
        self.positions: dict[str, Position] = {}

    def add_position(self, position: Position):
        self.positions[position.id] = position
        log.info(
            "📂 开仓",
            id=position.id,
            symbol=position.symbol,
            direction=position.direction.value,
            price=f"${position.entry_price:,.2f}",
            qty=f"{position.quantity:.6f}",
            notional=f"${position.notional_value:,.0f}",
            liq=f"${position.liquidation_price:,.2f}({position.liquidation_distance_pct:.1%})",
        )

    def close_position(self, position_id: str, exit_price: float,
                       reason: str) -> Trade | None:
        pos = self.positions.get(position_id)
        if not pos or pos.status != PositionStatus.OPEN:
            return None

        # 计算盈亏
        if pos.direction == Direction.LONG:
            pnl = (exit_price - pos.entry_price) / pos.entry_price * pos.notional_value
        else:
            pnl = (pos.entry_price - exit_price) / pos.entry_price * pos.notional_value

        fee = pos.notional_value * settings.fee_rate_total
        pnl -= fee  # 扣除平仓手续费

        pnl_pct = pnl / pos.margin

        trade = Trade(
            position_id=pos.id,
            symbol=pos.symbol,
            direction=pos.direction,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            quantity=pos.quantity,
            pnl=pnl,
            pnl_pct=pnl_pct,
            fee=fee,
            source=pos.source,
            opened_at=pos.opened_at,
            closed_at=datetime.utcnow(),
            close_reason=reason,
            is_counter_trend=pos.is_counter_trend,
        )

        pos.status = PositionStatus.CLOSED
        del self.positions[position_id]

        tag = "✅" if pnl > 0 else "❌"
        log.info(
            f"{tag} 平仓 [{reason}]",
            id=position_id,
            symbol=pos.symbol,
            direction=pos.direction.value,
            entry=f"${pos.entry_price:,.2f}",
            exit=f"${exit_price:,.2f}",
            pnl=f"${pnl:+.2f}({pnl_pct:+.1%})",
            fee=f"${fee:.2f}",
            hold=f"{pos.hold_hours:.1f}h",
        )

        return trade

    def partial_close(self, position_id: str, exit_price: float,
                      ratio: float = 0.5) -> Trade | None:
        """部分止盈: 减仓50%"""
        pos = self.positions.get(position_id)
        if not pos or pos.partial_closed:
            return None

        close_quantity = pos.quantity * ratio
        close_notional = pos.notional_value * ratio

        if pos.direction == Direction.LONG:
            pnl = (exit_price - pos.entry_price) / pos.entry_price * close_notional
        else:
            pnl = (pos.entry_price - exit_price) / pos.entry_price * close_notional

        fee = close_notional * settings.fee_rate_total
        pnl -= fee

        # 更新持仓
        pos.quantity -= close_quantity
        pos.notional_value -= close_notional
        pos.margin -= pos.margin * ratio
        pos.partial_closed = True

        trade = Trade(
            position_id=f"{pos.id}_partial",
            symbol=pos.symbol,
            direction=pos.direction,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            quantity=close_quantity,
            pnl=pnl,
            pnl_pct=pnl / (pos.margin * ratio) if pos.margin > 0 else 0,
            fee=fee,
            source=pos.source,
            opened_at=pos.opened_at,
            closed_at=datetime.utcnow(),
            close_reason="partial",
            is_counter_trend=pos.is_counter_trend,
        )

        log.info(
            "📊 部分止盈",
            id=position_id,
            close_ratio=f"{ratio:.0%}",
            pnl=f"${pnl:+.2f}",
            remaining_qty=f"{pos.quantity:.6f}",
        )

        return trade

    # ── 出场条件检查 ──────────────────────────────────────

    def check_exit_conditions(self, position: Position, current_price: float,
                              high: float, low: float) -> str | None:
        """
        检查出场条件, 返回出场原因或None
        优先级: 止损 > 止盈 > 保本 > 移动止损 > 部分止盈 > 持仓超时
        """
        # v7.5: 开仓30min内止损缓冲(×1.3)防噪音扫损
        hold_minutes = position.hold_hours * 60
        early_buffer = settings.early_stop_buffer if hold_minutes < 30 else 1.0

        if position.direction == Direction.LONG:
            # 止损 (early buffer: widen stop downward)
            effective_stop = position.stop_loss * (2 - early_buffer)  # e.g. 1.3 → ×0.7 (wider)
            if low <= effective_stop:
                return "stop_loss"
            # 止盈
            if high >= position.take_profit:
                return "take_profit"
            # 浮盈计算
            profit_pct = (current_price - position.entry_price) / position.entry_price
        else:
            # 止损 (early buffer: widen stop upward)
            effective_stop = position.stop_loss * early_buffer  # e.g. 1.3 → ×1.3 (wider)
            if high >= effective_stop:
                return "stop_loss"
            # 止盈
            if low <= position.take_profit:
                return "take_profit"
            profit_pct = (position.entry_price - current_price) / position.entry_price

        # 部分止盈
        if not position.partial_closed and profit_pct >= settings.s1_partial_tp_trigger:
            return "partial"

        # v7.7: 保本 -> 移止损到入场价（不再直接平仓）
        if profit_pct >= settings.s1_breakeven_trigger and not getattr(position, 'breakeven_moved', False):
            if position.direction == Direction.LONG:
                position.stop_loss = max(position.stop_loss, position.entry_price)
            else:
                position.stop_loss = min(position.stop_loss, position.entry_price)
            position.breakeven_moved = True
            log.info(
                'breakeven_moved',
                id=position.id,
                symbol=position.symbol,
                entry=round(position.entry_price, 2),
                new_stop=round(position.stop_loss, 2),
            )

        # v7.7: Trailing Stop — partial/breakeven 后追踪最高/低点保护浮盈
        if position.partial_closed or getattr(position, 'breakeven_moved', False):
            trail_dist = current_price * settings.s1_trailing_stop
            if position.direction == Direction.LONG:
                trail_price = current_price - trail_dist
                if trail_price > position.stop_loss:
                    position.stop_loss = trail_price
            else:
                trail_price = current_price + trail_dist
                if trail_price < position.stop_loss:
                    position.stop_loss = trail_price

        # v7.5: 持仓超时 — 按策略分别限制
        from core.models import SignalSource
        if position.source == SignalSource.S2_RSI_DIVERGENCE:
            max_hold = settings.max_hold_hours_s2  # S2: 8h
        else:
            max_hold = settings.max_hold_hours     # S1: 24h
        if position.hold_hours >= max_hold:
            return "timeout"

        return None

    def get_open_position(self, symbol: str) -> Position | None:
        """查询某品种的持仓"""
        for pos in self.positions.values():
            if pos.symbol == symbol and pos.status == PositionStatus.OPEN:
                return pos
        return None

    def get_all_positions(self) -> list[Position]:
        return list(self.positions.values())
