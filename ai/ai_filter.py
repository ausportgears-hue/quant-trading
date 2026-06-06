"""v7.5.5 AI 信号过滤器 — L1 信号二次确认

插入位置: aggregator.aggregate() 返回后, risk_mgr.check_risk() 之前
功能: 发送信号+市场上下文给 LLM, 由 AI 判断是否存在规则盲区风险
降级: API 不可用时自动放行, 退回纯规则模式
"""
import json
from datetime import datetime, timezone
import pandas as pd
from config.settings import settings
from core.models import Direction, Signal
from ai.ai_client import AIClient
from ai.ai_prompts import SYSTEM_PROMPT_SIGNAL_FILTER
from utils.logger import log


class SignalAIFilter:
    """L1: AI 信号二次确认"""

    def __init__(self, client: AIClient):
        self.client = client
        self.approve_count = 0
        self.reject_count = 0
        self.fallback_count = 0  # API 不可用时放行的次数

    @property
    def enabled(self) -> bool:
        return (settings.ai_enabled and settings.ai_signal_filter_enabled
                and self.client.is_available())

    def check_signal(self, signal: Signal, df_15m: pd.DataFrame,
                     df_4h: pd.DataFrame, system_state: dict) -> tuple:
        """检查信号是否应被 AI 否决

        Args:
            signal: 聚合后的最终信号
            df_15m: 15m K 线数据
            df_4h: 4H K 线数据
            system_state: {"equity": 618.46, "daily_pnl": 45.2, ...}

        Returns:
            (approved: bool, reasoning: str, risk_flags: list)
            approved=True 表示 AI 放行（包括降级时自动放行）
        """
        if not self.enabled:
            self.fallback_count += 1
            return True, "AI disabled/degraded (auto-approved)", []

        try:
            context = self._build_context(signal, df_15m, df_4h, system_state)
            result = self.client.chat([
                {"role": "system", "content": SYSTEM_PROMPT_SIGNAL_FILTER},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ])

            approved = result.get("approved", True)
            reasoning = result.get("reasoning", "no reasoning provided")
            risk_flags = result.get("risk_flags", [])

            if approved:
                self.approve_count += 1
                log.info(f"AI approved signal: {reasoning}")
            else:
                self.reject_count += 1
                log.info(f"AI rejected signal: {reasoning}", risk_flags=risk_flags)

            return approved, reasoning, risk_flags

        except Exception as e:
            self.fallback_count += 1
            log.error(f"AI filter error (fallback to approve): {e}")
            return True, f"AI unavailable: {str(e)[:80]}", []

    def _build_context(self, signal: Signal, df_15m: pd.DataFrame,
                       df_4h: pd.DataFrame, system_state: dict) -> dict:
        """构建发送给 AI 的上下文"""
        close_15m = df_15m["close"]
        high_15m = df_15m["high"]
        low_15m = df_15m["low"]
        volume_15m = df_15m.get("volume", pd.Series([0] * len(df_15m)))
        open_15m = df_15m["open"]

        # 最近 5 根 15m K 线摘要
        recent_candles = []
        for i in range(-5, 0):
            if len(close_15m) >= abs(i):
                recent_candles.append({
                    "open": round(float(open_15m.iloc[i]), 2),
                    "high": round(float(high_15m.iloc[i]), 2),
                    "low": round(float(low_15m.iloc[i]), 2),
                    "close": round(float(close_15m.iloc[i]), 2),
                    "volume": int(volume_15m.iloc[i]) if volume_15m.iloc[i] else 0,
                })

        # 15m 指标快照
        try:
            from core.indicators import rsi, atr, volume_ratio
            current_rsi = float(rsi(close_15m, 14).iloc[-1])
            current_atr = float(atr(high_15m, low_15m, close_15m, 14).iloc[-1])
            atr_pct = current_atr / float(close_15m.iloc[-1])
            vol_ratio = float(volume_ratio(volume_15m))
        except Exception:
            current_rsi = 50
            current_atr = 0
            atr_pct = 0
            vol_ratio = 1.0

        # 价格波动
        if len(close_15m) >= 3:
            price_change_3 = (close_15m.iloc[-1] - close_15m.iloc[-4]) / close_15m.iloc[-4]
        else:
            price_change_3 = 0

        # 整数关口检测
        current_price = float(close_15m.iloc[-1])
        round_levels = []
        for level in [50, 100, 200, 500, 1000, 2000, 5000, 10000]:
            dist = abs(current_price % level - level) % level
            pct = dist / current_price
            if pct < 0.005:
                round_levels.append(level)

        # 4H 快照
        if not df_4h.empty:
            from core.indicators import adx, ema
            close_4h = df_4h["close"]
            try:
                adx_4h = float(adx(df_4h["high"], df_4h["low"], close_4h, 14).iloc[-1])
            except Exception:
                adx_4h = 20
            try:
                ema20_4h = float(ema(close_4h, 20).iloc[-1])
            except Exception:
                ema20_4h = current_price
            price_vs_ema20 = (current_price - ema20_4h) / ema20_4h
        else:
            adx_4h = 20
            price_vs_ema20 = 0

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "market_context": {
                "symbol": signal.symbol,
                "current_price": round(current_price, 2),
                "timeframe": "15m",
                "price_change_3candles": f"{price_change_3:+.2%}",
                "recent_candles_15m": recent_candles,
                "indicators_15m": {
                    "rsi14": round(current_rsi, 1),
                    "atr": round(current_atr, 2),
                    "atr_pct": f"{atr_pct:.2%}",
                    "volume_ratio": round(vol_ratio, 2),
                },
                "indicators_4h": {
                    "adx": round(adx_4h, 1),
                    "price_vs_ema20": f"{price_vs_ema20:+.2%}",
                },
                "round_levels_nearby": round_levels,
            },
            "signal": {
                "source": signal.source.value,
                "direction": signal.direction.value,
                "strength": round(signal.strength, 3),
                "entry_price": round(signal.entry_price, 2),
                "stop_loss": round(signal.stop_loss, 2),
                "take_profit": round(signal.take_profit, 2),
                "risk_reward": round(signal.risk_reward, 2),
                "is_counter_trend": signal.is_counter_trend,
                "details": signal.details,
            },
            "system_state": system_state,
        }


    def check_stop_loss(self, position, current_price: float, 
                        df_15m, df_4h, 
                        stop_distance_pct: float) -> tuple:
        """AI 辅助止损守卫 — 在规则触发止损前，AI二次确认是否真的需要止损
        
        Args:
            position: 当前持仓对象
            current_price: 当前价格
            df_15m: 15m K线
            df_4h: 4H K线
            stop_distance_pct: 当前价格距止损的距离比例（如0.005=距止损0.5%）
        
        Returns:
            (should_close: bool, reasoning: str, confidence: float)
            should_close=True 表示AI建议立即平仓
            should_close=False 表示AI认为是噪音，继续持有
        """
        from ai.ai_prompts import SYSTEM_PROMPT_STOP_LOSS_GUARD
        
        if not self.enabled:
            return True, "AI disabled (default: close)", 1.0  # 无AI则执行规则止损
        
        # 仅在止损距离 < 0.8% 时介入（太远没必要）
        if stop_distance_pct > 0.008:
            return True, f"Far from stop ({stop_distance_pct:.2%}), execute stop", 0.9
        
        try:
            import json
            from datetime import datetime, timezone
            
            close_15m = df_15m["close"]
            high_15m = df_15m["high"]
            low_15m = df_15m["low"]
            
            # 当前价格距止损
            sl_price = position.stop_loss if hasattr(position, 'stop_loss') else None
            entry_price = position.entry_price
            direction = position.direction.value if hasattr(position.direction, 'value') else position.direction
            
            # ATR
            try:
                from core.indicators import atr, rsi
                current_atr = float(atr(high_15m, low_15m, close_15m, 14).iloc[-1])
                current_rsi = float(rsi(close_15m, 14).iloc[-1])
            except Exception:
                current_atr = abs(float(close_15m.iloc[-1]) - float(close_15m.iloc[-2]))
                current_rsi = 50
            
            # 最近5根K线
            recent_candles = []
            for i in range(-5, 0):
                try:
                    recent_candles.append({
                        "open": round(float(df_15m["open"].iloc[i]), 2),
                        "high": round(float(high_15m.iloc[i]), 2),
                        "low": round(float(low_15m.iloc[i]), 2),
                        "close": round(float(close_15m.iloc[i]), 2),
                    })
                except Exception:
                    pass
            
            # 4H趋势
            adx_4h = 20
            price_vs_ema20_4h = 0
            if not df_4h.empty:
                try:
                    from core.indicators import adx, ema
                    adx_4h = float(adx(df_4h["high"], df_4h["low"], df_4h["close"], 14).iloc[-1])
                    ema20_4h = float(ema(df_4h["close"], 20).iloc[-1])
                    price_vs_ema20_4h = (current_price - ema20_4h) / ema20_4h
                except Exception:
                    pass
            
            # PnL
            if direction == "short":
                unrealized_pnl_pct = (entry_price - current_price) / entry_price
            else:
                unrealized_pnl_pct = (current_price - entry_price) / entry_price
            
            context = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "position": {
                    "symbol": position.symbol,
                    "direction": direction,
                    "entry_price": round(entry_price, 2),
                    "current_price": round(current_price, 2),
                    "stop_loss": round(sl_price, 2) if sl_price else None,
                    "stop_distance_pct": f"{stop_distance_pct:+.2%}",
                    "unrealized_pnl_pct": f"{unrealized_pnl_pct:+.2%}",
                    "hold_minutes": (lambda oa: int((datetime.now(timezone.utc) - (oa.replace(tzinfo=timezone.utc) if oa.tzinfo is None else oa)).total_seconds() / 60) if (oa := getattr(position, 'opened_at', None)) is not None else 0),
                },
                "market": {
                    "recent_candles_15m": recent_candles,
                    "rsi14": round(current_rsi, 1),
                    "atr": round(current_atr, 2),
                    "atr_pct": f"{current_atr/current_price:.2%}",
                    "adx_4h": round(adx_4h, 1),
                    "price_vs_4h_ema20": f"{price_vs_ema20_4h:+.2%}",
                },
                "question": "这是真正的趋势反转还是噪音扫损？给出close(立即平仓)或hold(继续持有)建议。"
            }
            
            result = self.client.chat([
                {"role": "system", "content": SYSTEM_PROMPT_STOP_LOSS_GUARD},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ])
            
            action = result.get("action", "close")
            confidence = float(result.get("confidence", 0.5))
            reasoning = result.get("reasoning", "no reasoning")
            risk_level = result.get("risk_level", "medium")
            
            should_close = (action == "close")
            
            if should_close:
                log.info(f"AI StopGuard: CLOSE recommended | {reasoning} | conf={confidence:.2f} risk={risk_level}")
            else:
                log.info(f"AI StopGuard: HOLD recommended (noise) | {reasoning} | conf={confidence:.2f}")
            
            return should_close, reasoning, confidence
        
        except Exception as e:
            log.error(f"AI stop guard error (default: execute stop): {e}")
            return True, f"AI unavailable: {str(e)[:60]}", 1.0  # 失败时默认执行止损（保守原则）

    def stats(self) -> dict:
        return {
            "approve": self.approve_count,
            "reject": self.reject_count,
            "fallback": self.fallback_count,
            "total": self.approve_count + self.reject_count + self.fallback_count,
            "reject_rate": (self.reject_count / max(self.approve_count + self.reject_count, 1)),
        }
