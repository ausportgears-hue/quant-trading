"""策略2: RSI背离逆势策略 (v7.7: adapter支持)"""
import pandas as pd
from core.indicators import rsi, atr, volume_ratio, detect_rsi_divergence, find_swing_points
from core.models import Direction, DirectionJudgment, Signal, SignalSource
import structlog
log = structlog.get_logger()

class Strategy2RSIDivergence:
    """RSI背离逆势策略"""
    
    def __init__(self, adapter=None):
        self.name = "S2_RSI_Divergence"
        self.weight = 0.3
        self.adapter = adapter

    def check_entry(self, df_15m: pd.DataFrame, direction_judgment: DirectionJudgment, symbol: str = "") -> Signal | None:
        """
        RSI背离入场:
        1. RSI背离确认 (15m级别)
        2. 突破前期摆动高/低点
        3. 放量确认 >2.0x
        """
        close = df_15m["close"]
        high = df_15m["high"]
        low = df_15m["low"]
        volume = df_15m["volume"]
        current_price = float(close.iloc[-1])

        atr_val = atr(high, low, close, 14)
        rsi_val = rsi(close, 14)
        current_atr = float(atr_val.iloc[-1])
        current_rsi = float(rsi_val.iloc[-1])

        # 成交量确认 (v7.6.5: 时段自适应)
        vol_ratio = volume_ratio(volume, period=20)
        s2_vol = self.adapter.get_s2_volume_confirm() if self.adapter else 0.05
        if vol_ratio < s2_vol:
            log.debug("S2: 放量不足", vol_ratio=f"{vol_ratio:.2f}x")
            return None

        swing_highs, swing_lows = find_swing_points(high, low, left=5, right=5)

        signal_direction = None
        is_counter_trend = False

        if detect_rsi_divergence(close, rsi_val, high, low, direction="bullish", lookback=50):
            if swing_highs:
                recent_swing_high = float(high.iloc[swing_highs[-1]])
                if current_price > recent_swing_high:
                    signal_direction = Direction.LONG

        if detect_rsi_divergence(close, rsi_val, high, low, direction="bearish", lookback=50):
            if swing_lows:
                recent_swing_low = float(low.iloc[swing_lows[-1]])
                if current_price < recent_swing_low:
                    signal_direction = Direction.SHORT

        if signal_direction is None:
            return None

        if direction_judgment.direction != Direction.NEUTRAL:
            is_counter_trend = (signal_direction != direction_judgment.direction)

        from config.settings import settings
        if signal_direction == Direction.LONG:
            stop_loss = current_price - current_atr * settings.s2_stop_atr_mult
            take_profit = current_price + (abs(current_price - stop_loss) * settings.s2_tp_risk_mult)
        else:
            stop_loss = current_price + current_atr * settings.s2_stop_atr_mult
            take_profit = current_price - (abs(current_price - stop_loss) * settings.s2_tp_risk_mult)

        risk = abs(current_price - stop_loss)
        reward = abs(take_profit - current_price)
        rr = reward / risk if risk > 0 else 0

        if rr < settings.min_risk_reward:
            log.debug("S2: 盈亏比不足", rr=f"{rr:.2f}")
            return None

        effective_strength = 1.0 * self.weight
        if is_counter_trend:
            effective_strength *= settings.counter_trend_weight_factor

        counter_tag = "逆势" if is_counter_trend else "顺势"
        log.info(f"S2信号触发 [{counter_tag}]", direction=signal_direction.value, price=current_price)

        return Signal(
            source=SignalSource.S2_RSI_DIVERGENCE,
            symbol=symbol,
            direction=signal_direction,
            strength=effective_strength,
            entry_price=current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
