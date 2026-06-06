"""v7.7 技术指标计算 — 4H方向+15m入场指标"""
import pandas as pd
import numpy as np


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple:
    """返回 (macd_line, signal_line, histogram)"""
    fast_ema = ema(series, fast)
    slow_ema = ema(series, slow)
    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average Directional Index"""
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr_val = tr.ewm(alpha=1/period, min_periods=period).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1/period, min_periods=period).mean() / atr_val)
    minus_di = 100 * (minus_dm.ewm(alpha=1/period, min_periods=period).mean() / atr_val)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx_val = dx.ewm(alpha=1/period, min_periods=period).mean()
    return adx_val


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range"""
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, min_periods=period).mean()


def is_rising(series: pd.Series, bars: int = 3) -> bool:
    """最近N根K线是否持续上升"""
    if len(series) < bars + 1:
        return False
    recent = series.iloc[-(bars+1):]
    return all(recent.iloc[i] < recent.iloc[i+1] for i in range(len(recent)-1))


def volume_ratio(volume: pd.Series, period: int = 20) -> float:
    """当前成交量 / 历史均量 (v7.5.3修复: 排除当前未完成K线作为分母基准)
    
    修复前: avg包含当前K线自身 -> 15m盘中K线成交量天然偏低 -> ratio永远<1.3
    修复后: hist_avg = [-period-1:-1] 排除当前K线 -> 准确反映当前K线的放量程度
    """
    if len(volume) < period + 1:
        return 1.0
    hist_avg = volume.iloc[-(period + 1):-1].mean()  # 历史period根，排除当前K线
    return volume.iloc[-1] / hist_avg if hist_avg > 0 else 1.0


def atr_spike_ratio(atr_series: pd.Series, lookback: int = 20) -> float:
    """当前ATR / 过去N根ATR均值"""
    if len(atr_series) < lookback:
        return 1.0
    avg = atr_series.iloc[-lookback:].mean()
    return atr_series.iloc[-1] / avg if avg > 0 else 1.0


def find_swing_points(high: pd.Series, low: pd.Series, left: int = 5, right: int = 5) -> tuple:
    """寻找摆动高低点 → (swing_highs: list[int], swing_lows: list[int])"""
    swing_highs = []
    swing_lows = []
    for i in range(left, len(high) - right):
        is_high = all(high.iloc[i] >= high.iloc[i-j] for j in range(1, left+1)) and \
                  all(high.iloc[i] >= high.iloc[i+j] for j in range(1, right+1))
        is_low = all(low.iloc[i] <= low.iloc[i-j] for j in range(1, left+1)) and \
                 all(low.iloc[i] <= low.iloc[i+j] for j in range(1, right+1))
        if is_high:
            swing_highs.append(i)
        if is_low:
            swing_lows.append(i)
    return swing_highs, swing_lows


def detect_rsi_divergence(
    close: pd.Series,
    rsi_series: pd.Series,
    high: pd.Series,
    low: pd.Series,
    direction: str = "bullish",
    lookback: int = 50,
) -> bool:
    """
    检测RSI背离
    direction: 'bullish' (价格新低+RSI未新低) 或 'bearish' (价格新高+RSI未新高)
    """
    if len(close) < lookback:
        return False

    recent_close = close.iloc[-lookback:]
    recent_rsi = rsi_series.iloc[-lookback:]

    if direction == "bullish":
        # 价格创新低，但RSI未创新低
        price_min_idx = recent_close.idxmin()
        rsi_at_price_min = recent_rsi.loc[price_min_idx]

        # 找前一个低点
        first_half_close = recent_close.iloc[:len(recent_close)//2]
        if len(first_half_close) < 2:
            return False
        prev_price_min_idx = first_half_close.idxmin()
        rsi_at_prev_min = recent_rsi.loc[prev_price_min_idx]

        if recent_close.min() < first_half_close.min():  # 价格更低
            return rsi_at_price_min > rsi_at_prev_min  # RSI更高 → 看涨背离

    elif direction == "bearish":
        # 价格创新高，但RSI未创新高
        price_max_idx = recent_close.idxmax()
        rsi_at_price_max = recent_rsi.loc[price_max_idx]

        first_half_close = recent_close.iloc[:len(recent_close)//2]
        if len(first_half_close) < 2:
            return False
        prev_price_max_idx = first_half_close.idxmax()
        rsi_at_prev_max = recent_rsi.loc[prev_price_max_idx]

        if recent_close.max() > first_half_close.max():
            return rsi_at_price_max < rsi_at_prev_max

    return False
