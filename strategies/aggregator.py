"""v7.7 信号聚合器 — S1(70%) + S2(30%) 加权投票 + 冲突处理

冲突处理规则 (v7.5.4):
- 方向冲突时门槛从0.6→0.7
- S2逆势权重减半，独立门槛0.15（解决S2逆势被0.60聚合门槛结构性封死）
- 方向差异必须>30%
- 净持仓: 不分别开仓, 加权投票决定单一方向
"""
from core.models import Direction, Signal, SignalSource
from config.settings import settings
from adaptive.parameter_adapter import AdaptiveParameterAdapter
from utils.logger import log


class SignalAggregator:
    """信号聚合与冲突处理 — v7.7"""

    def __init__(self):
        self._adapter = AdaptiveParameterAdapter()
        self.s1_weight = settings.s1_weight
        self.s2_weight = settings.s2_weight

    def aggregate(self, signals: list[Signal]) -> Signal | None:
        """v7.7: 聚合多个策略信号, 返回最终交易信号或None."""
        self.s1_weight = self._adapter.get_s1_weight()
        self.s2_weight = self._adapter.get_s2_weight()
        if not signals:
            return None
        if len(signals) == 1:
            return self._eval_single_signal(signals[0])
        return self._eval_multi_signal(signals)

    def _eval_single_signal(self, sig: Signal) -> Signal | None:
        """v7.7: 单一信号阈值检查（S2分级门槛 0.15/0.25, S1门槛 0.60）."""
        if sig.source == SignalSource.S2_RSI_DIVERGENCE:
            if sig.is_counter_trend:
                threshold = settings.s2_counter_trend_threshold
            else:
                threshold = settings.s2_solo_threshold
        else:
            threshold = settings.base_signal_threshold
        
        if sig.strength >= threshold:
            if sig.source == SignalSource.S2_RSI_DIVERGENCE:
                log.info(
                    f"S2独信号通过 ({'逆势' if sig.is_counter_trend else '顺势'})",
                    strength=f"{sig.strength:.2f}",
                    threshold=f"{threshold:.2f}",
                )
            return sig
        else:
            if sig.source == SignalSource.S2_RSI_DIVERGENCE:
                log.debug(
                    f"S2信号强度不足", 
                    strength=f"{sig.strength:.2f}", 
                    threshold=f"{threshold:.2f}",
                    counter_trend=sig.is_counter_trend,
                )
        return None

    def _eval_multi_signal(self, signals: list[Signal]) -> Signal | None:
        """v7.7: 多信号加权投票 + 冲突解决 + 最终信号构建."""
        long_score = 0.0
        short_score = 0.0
        best_signal = None

        for sig in signals:
            if sig.direction == Direction.LONG:
                long_score += sig.strength
            elif sig.direction == Direction.SHORT:
                short_score += sig.strength
            if best_signal is None or sig.strength > best_signal.strength:
                best_signal = sig

        total = long_score + short_score
        if total == 0:
            return None

        has_conflict = (long_score > 0 and short_score > 0)

        if has_conflict:
            direction_diff = abs(long_score - short_score) / total
            if direction_diff < settings.direction_diff_min:
                log.info(
                    "聚合: 方向冲突且差异不足, 放弃信号",
                    long=f"{long_score:.2f}",
                    short=f"{short_score:.2f}",
                    diff=f"{direction_diff:.1%}",
                )
                return None
            threshold = settings.conflict_signal_threshold
        else:
            threshold = settings.base_signal_threshold

        if long_score > short_score:
            final_direction = Direction.LONG
            final_strength = long_score / total
        else:
            final_direction = Direction.SHORT
            final_strength = short_score / total

        if final_strength < threshold:
            log.info(
                "聚合: 信号强度不足",
                direction=final_direction.value,
                strength=f"{final_strength:.2f}",
                threshold=f"{threshold:.2f}",
            )
            return None

        if best_signal.risk_reward < settings.min_risk_reward:
            log.info("聚合: 盈亏比不足", rr=f"{best_signal.risk_reward:.2f}")
            return None

        final_signal = Signal(
            source=best_signal.source,
            symbol=best_signal.symbol,
            direction=final_direction,
            strength=final_strength,
            entry_price=best_signal.entry_price,
            stop_loss=best_signal.stop_loss,
            take_profit=best_signal.take_profit,
            atr_value=best_signal.atr_value,
            risk_reward=best_signal.risk_reward,
            direction_judgment=best_signal.direction_judgment,
            is_counter_trend=any(s.is_counter_trend for s in signals),
            details=self._build_details(signals, final_direction, final_strength, has_conflict),
        )

        conflict_tag = " [冲突已解决]" if has_conflict else ""
        log.info(
            f"✅ 信号聚合完成{conflict_tag}",
            direction=final_direction.value,
            strength=f"{final_strength:.2f}",
            sources=[s.source.value for s in signals],
            rr=f"{final_signal.risk_reward:.1f}",
        )
        return final_signal
    def _build_details(self, signals: list[Signal], direction: Direction,
                       strength: float, has_conflict: bool) -> str:
        parts = [f"聚合→{direction.value}({strength:.0%})"]
        for s in signals:
            tag = "逆" if s.is_counter_trend else "顺"
            parts.append(f"{s.source.value}[{tag}]={s.strength:.2f}")
        if has_conflict:
            parts.append("冲突门槛0.7")
        return " | ".join(parts)
