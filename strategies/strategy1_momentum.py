"""v7.6.1 策略一：多时间框架动量趋势（核心策略, 70%权重）

4H定方向 + 15m找入场, 4/5精选, MACD放宽+做空补atr
"""
import pandas as pd
from datetime import datetime
from core.models import Direction, DirectionJudgment, Signal, SignalSource
from core.indicators import (
    ema, macd, adx, atr, rsi, is_rising,
    atr_spike_ratio, volume_ratio
)
from config.settings import settings
from adaptive.parameter_adapter import AdaptiveParameterAdapter
from utils.logger import log


class Strategy1Momentum:
    """S1: 多TF动量趋势 — v7.0"""

    def __init__(self):
        self.name = SignalSource.S1_MOMENTUM
        self.weight = settings.s1_weight  # 初始值，运行时由adapter更新
        self._adapter = AdaptiveParameterAdapter()

    # ── 4H方向判定 ──────────────────────────────────────

    def judge_direction(self, df_4h: pd.DataFrame) -> DirectionJudgment:
        """
        4H方向判定: EMA排列+MACD+ADX>25+价格位置
        ≥3条→做多, ≤1条→做空, 2条→空仓
        """
        close = df_4h["close"]
        high = df_4h["high"]
        low = df_4h["low"]

        # 计算指标
        ema20 = ema(close, 20)
        ema50 = ema(close, 50)
        ema200 = ema(close, 200)
        macd_line, signal_line, _ = macd(close)
        adx_val = adx(high, low, close, 14)
        current_price = close.iloc[-1]

        # 4个条件
        ema_aligned = (ema20.iloc[-1] > ema50.iloc[-1] > ema200.iloc[-1])
        macd_bullish = (macd_line.iloc[-1] > 0) and (macd_line.iloc[-1] > signal_line.iloc[-1])
        adx_strong = adx_val.iloc[-1] > settings.adx_threshold
        price_above_ema50 = current_price > ema50.iloc[-1]

        score = sum([ema_aligned, macd_bullish, adx_strong, price_above_ema50])

        if score >= settings.direction_min_score:
            direction = Direction.LONG
        elif score <= 1:
            direction = Direction.SHORT
        else:
            direction = Direction.NEUTRAL

        # 对于做空: 反转条件
        if direction == Direction.NEUTRAL:
            ema_bearish = (ema20.iloc[-1] < ema50.iloc[-1] < ema200.iloc[-1])
            macd_bearish = (macd_line.iloc[-1] < 0) and (macd_line.iloc[-1] < signal_line.iloc[-1])
            price_below_ema50 = current_price < ema50.iloc[-1]
            bear_score = sum([ema_bearish, macd_bearish, adx_strong, price_below_ema50])
            if bear_score >= settings.direction_min_score:
                direction = Direction.SHORT
            elif bear_score <= 1:
                direction = Direction.LONG

        details = (
            f"4H方向判定: score={score}/4, "
            f"EMA排列={'✓' if ema_aligned else '✗'}, "
            f"MACD={'✓' if macd_bullish else '✗'}, "
            f"ADX={adx_val.iloc[-1]:.1f}(>{settings.adx_threshold}={'✓' if adx_strong else '✗'}), "
            f"价格>EMA50={'✓' if price_above_ema50 else '✗'} → {direction.value}"
        )

        return DirectionJudgment(
            score=score,
            direction=direction,
            ema_aligned=ema_aligned,
            macd_bullish=macd_bullish,
            adx_strong=adx_strong,
            price_above_ema50=price_above_ema50,
            adx_value=adx_val.iloc[-1],
            details=details,
        )

    # ── 15m入场判定 ──────────────────────────────────────

    def check_entry(self, df_15m: pd.DataFrame, direction_judgment: DirectionJudgment, symbol: str = "") -> Signal | None:
        """
        15m入场: 5/5全部满足才入场
        1. EMA10上升
        2. EMA50上升
        3. MACD转强
        4. 波动率过滤 ATR/价格<1.2%
        5. RSI<68(做多) / RSI>32(做空)
        """
        if direction_judgment.direction == Direction.NEUTRAL:
            log.debug("S1: 4H方向中性，跳过", score=direction_judgment.score)
            return None

        close = df_15m["close"]
        high = df_15m["high"]
        low = df_15m["low"]

        # 计算指标
        ema10 = ema(close, 10)
        ema50_val = ema(close, 50)
        macd_line, signal_line, _ = macd(close)
        atr_val = atr(high, low, close, 14)
        rsi_val = rsi(close, 14)
        current_price = close.iloc[-1]

        direction = direction_judgment.direction

        # ATR突增过滤
        spike = atr_spike_ratio(atr_val, lookback=20)
        if spike > settings.atr_spike_mult:
            log.debug("S1: ATR突增过滤", spike_ratio=f"{spike:.2f}x")
            return None

        # 5个入场条件
        checks = {}

        if direction == Direction.LONG:
            checks["ema10_rising"] = is_rising(ema10, 3)
            checks["ema50_rising"] = is_rising(ema50_val, 3)
            checks["macd_bullish"] = (macd_line.iloc[-1] > signal_line.iloc[-1]) or (macd_line.iloc[-1] > macd_line.iloc[-2])  # v7.3: MACD>signal 或 MACD在上升
            atr_cap = settings.atr_volatility_cap_alt if symbol in settings.alt_symbols else settings.atr_volatility_cap  # v7.5
            checks["atr_filter"] = (atr_val.iloc[-1] / current_price) < atr_cap
            checks["rsi_filter"] = rsi_val.iloc[-1] < settings.rsi_overbought
        else:  # SHORT
            checks["ema10_falling"] = not is_rising(ema10, 3)  # EMA10下降
            checks["ema50_falling"] = not is_rising(ema50_val, 3)
            checks["macd_bearish"] = (macd_line.iloc[-1] < signal_line.iloc[-1]) or (macd_line.iloc[-1] < macd_line.iloc[-2])  # v7.3: MACD<signal 或 MACD在下降
            atr_cap = settings.atr_volatility_cap_alt if symbol in settings.alt_symbols else settings.atr_volatility_cap  # v7.5
            checks["atr_filter"] = (atr_val.iloc[-1] / current_price) < atr_cap  # v7.3: 补回做空缺失的atr_filter
            checks["rsi_filter"] = rsi_val.iloc[-1] > (100 - settings.rsi_overbought)

        passed = sum(checks.values())
        all_pass = passed >= settings.s1_min_confirm

        if not all_pass:
            log.debug(
                "S1: 15m入场条件不足",
                passed=f"{passed}/{len(checks)}",
                checks={k: "✓" if v else "✗" for k, v in checks.items()},
            )
            return None

        # 计算止损止盈
        current_atr = atr_val.iloc[-1]

        # v7.6.1: 结构位止损（近期摆动高低点 + ATR缓冲）
        tp_mult = self._adapter.get_tp_atr_mult()
        atr_buffer_mult = 0.3  # 止损缓冲倍数

        # 近8根K线摆动极值（排除当前K线）
        lookback = min(8, len(df_15m) - 1)
        recent_high = df_15m["high"].iloc[-lookback-1:-1].max()
        recent_low  = df_15m["low"].iloc[-lookback-1:-1].min()
        atr_buf = current_atr * atr_buffer_mult

        if direction == Direction.LONG:
            stop_loss   = recent_low - atr_buf
            take_profit = current_price + current_atr * tp_mult
        else:
            stop_loss   = recent_high + atr_buf
            take_profit = current_price - current_atr * tp_mult

        risk   = abs(current_price - stop_loss)
        reward = abs(take_profit - current_price)
        rr     = reward / risk if risk > 0 else 0

        # 止损距离超过 2.0×ATR 时放弃（太宽了，宁可不做）
        max_stop_dist = current_atr * 5.0
        if risk > max_stop_dist:
            log.debug(
                "S1: 结构止损太宽，放弃",
                risk_dist=f"${risk:.2f}",
                max_allowed=f"${max_stop_dist:.2f}",
                atr=f"${current_atr:.2f}",
            )
            return None

        if rr < settings.min_risk_reward:
            log.debug("S1: 盈亏比不足", rr=f"{rr:.2f}")
            return None

        log.info(
            "🔥 S1信号触发",
            direction=direction.value,
            price=f"${current_price:,.2f}",
            atr=f"${current_atr:,.2f}",
            sl=f"${stop_loss:,.2f}",
            tp=f"${take_profit:,.2f}",
            rr=f"{rr:.1f}",
            adx=f"{direction_judgment.adx_value:.1f}",
        )

        return Signal(
            source=SignalSource.S1_MOMENTUM,
            symbol="",  # 由engine填充
            direction=direction,
            strength=1.0 * self.weight,
            entry_price=current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            atr_value=current_atr,
            risk_reward=rr,
            direction_judgment=direction_judgment,
            is_counter_trend=False,
            details=f"S1动量 | 5/5✓ | ADX={direction_judgment.adx_value:.1f} | RR={rr:.1f}",
        )


