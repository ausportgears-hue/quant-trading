"""v7.7 自适应参数适配器 — 完整正确版本

三大自适应能力:
1. 凯利参数自校准
2. 策略权重自适应
3. 波动率自适应止损
4. v7.6.5: 时段感知参数覆盖
"""
import json
from datetime import datetime
from pathlib import Path
from core.models import Trade, SignalSource
from config.settings import settings
from utils.logger import log


class AdaptiveParameterAdapter:
    """自适应参数适配器 — v7.6.5"""

    MIN_TRADES_FOR_ADAPT = 20
    MAX_WIN_RATE_CHANGE = 0.05
    MAX_RR_CHANGE = 0.3
    MAX_WEIGHT_CHANGE = 0.10
    MAX_ATR_MULT_CHANGE = 0.2
    WIN_RATE_FLOOR = 0.30
    WIN_RATE_CEIL = 0.70
    RR_FLOOR = 1.0
    RR_CEIL = 4.0
    WEIGHT_FLOOR = 0.10
    WEIGHT_CEIL = 0.90
    STOP_ATR_FLOOR = 0.8
    STOP_ATR_CEIL = 2.0
    TP_ATR_FLOOR = 2.0
    TP_ATR_CEIL = 5.0
    EMA_ALPHA = 0.15

    def __init__(self):
        self.state_file = Path("data/adaptive_state.json")
        self.state = self._load_state()
        log.info(
            "🔧 自适应参数初始化",
            kelly_wr=f"{self.state['kelly_win_rate']:.1%}",
            kelly_rr=f"{self.state['kelly_rr']:.2f}",
            s1_weight=f"{self.state['s1_weight']:.0%}",
            s2_weight=f"{self.state['s2_weight']:.0%}",
            stop_atr=f"{self.state['stop_atr_mult']:.2f}",
            tp_atr=f"{self.state['tp_atr_mult']:.2f}",
            total_trades=self.state['total_trades'],
        )

    def _load_state(self) -> dict:
        default = {
            "kelly_win_rate": settings.kelly_win_rate,
            "kelly_rr": settings.kelly_rr,
            "s1_weight": settings.s1_weight,
            "s2_weight": settings.s2_weight,
            "stop_atr_mult": settings.s1_stop_atr_mult,
            "tp_atr_mult": settings.s1_tp_atr_mult,
            "total_trades": 0,
            "s1_trades": 0, "s2_trades": 0,
            "s1_wins": 0, "s2_wins": 0,
            "ema_win_rate": settings.kelly_win_rate,
            "ema_rr": settings.kelly_rr,
            "s1_ema_win_rate": settings.kelly_win_rate,
            "s2_ema_win_rate": settings.kelly_win_rate,
            "volatility_regime": "normal",
            "recent_atr_ratios": [],
            "trade_history": [],
            "last_updated": None,
        }
        if self.state_file.exists():
            try:
                saved = json.loads(self.state_file.read_text())
                for k, v in default.items():
                    if k not in saved:
                        saved[k] = v
                # v7.6.4: sanitize None values
                for k in list(saved.keys()):
                    if saved[k] is None:
                        saved[k] = 0.0
                return saved
            except Exception:
                return default
        return default

    def _save_state(self):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state["last_updated"] = datetime.utcnow().isoformat()
        self.state["trade_history"] = self.state["trade_history"][-50:]
        self.state["recent_atr_ratios"] = self.state["recent_atr_ratios"][-30:]
        # v7.6.4: sanitize None values before save
        for k in list(self.state.keys()):
            if self.state[k] is None:
                self.state[k] = 0.0
        self.state_file.write_text(json.dumps(self.state, indent=2))

    # ── v7.6.5: 时段感知参数覆盖 ─────────────────────

    def _is_weekend(self) -> bool:
        """周末 = 周五20:00 UTC 至 周一20:00 UTC"""
        now = datetime.utcnow()
        wd = now.weekday()  # 0=Monday
        h = now.hour
        if wd == 4 and h >= 20:
            return True
        if wd in (5, 6):
            return True
        if wd == 0 and h < 20:
            return True
        return False

    def _get_session_params(self) -> dict:
        """根据UTC时间返回时段专属参数覆盖"""
        if self._is_weekend():
            return {
                "s1_weight": settings.weekend_s1_weight,
                "s2_weight": settings.weekend_s2_weight,
                "s2_volume_confirm": settings.weekend_s2_volume_confirm,
                "max_position_ratio": settings.weekend_max_position_ratio,
            }
        h = datetime.utcnow().hour
        if h < settings.session_asian_end:
            return {
                "s1_weight": settings.asian_s1_weight,
                "s2_weight": settings.asian_s2_weight,
                "s2_volume_confirm": settings.asian_s2_volume_confirm,
                "max_position_ratio": settings.asian_max_position_ratio,
            }
        elif h < settings.session_european_end:
            return {
                "s1_weight": settings.european_s1_weight,
                "s2_weight": settings.european_s2_weight,
                "s2_volume_confirm": settings.european_s2_volume_confirm,
                "max_position_ratio": settings.european_max_position_ratio,
            }
        else:
            return {
                "s1_weight": settings.us_s1_weight,
                "s2_weight": settings.us_s2_weight,
                "s2_volume_confirm": settings.us_s2_volume_confirm,
                "max_position_ratio": settings.us_max_position_ratio,
            }

    def get_s1_weight(self) -> float:
        if settings.session_aware_enabled:
            return self._get_session_params()["s1_weight"]
        return self.state["s1_weight"]

    def get_s2_weight(self) -> float:
        if settings.session_aware_enabled:
            return self._get_session_params()["s2_weight"]
        return self.state["s2_weight"]

    def get_s2_volume_confirm(self) -> float:
        """时段感知 s2_volume_confirm"""
        if settings.session_aware_enabled:
            return self._get_session_params()["s2_volume_confirm"]
        return settings.s2_volume_confirm

    def get_max_position_ratio(self) -> float:
        """时段感知 max_position_ratio"""
        if settings.session_aware_enabled:
            return self._get_session_params()["max_position_ratio"]
        return settings.max_position_ratio

    # ── 凯利参数自适应 ─────────────────────────────

    def get_kelly_win_rate(self) -> float:
        return self.state["kelly_win_rate"]

    def get_kelly_rr(self) -> float:
        return self.state["kelly_rr"]

    def _update_kelly_params(self, trade: Trade):
        self.state["total_trades"] += 1
        is_win = trade.pnl > 0
        alpha = self.EMA_ALPHA

        # EMA胜率
        win_val = 1.0 if is_win else 0.0
        old_wr = self.state["ema_win_rate"]
        new_wr = alpha * win_val + (1 - alpha) * old_wr
        self.state["ema_win_rate"] = new_wr

        # EMA盈亏比
        new_rr = None
        if trade.close_reason == "take_profit":
            actual_rr = self.state["tp_atr_mult"] / self.state["stop_atr_mult"]
            old_rr = self.state["ema_rr"]
            new_rr = alpha * actual_rr + (1 - alpha) * old_rr
            self.state["ema_rr"] = new_rr
        elif trade.close_reason == "stop_loss":
            actual_rr = 0.0
            old_rr = self.state["ema_rr"]
            new_rr = alpha * actual_rr + (1 - alpha) * old_rr
            self.state["ema_rr"] = new_rr
        else:
            pass

        if self.state["total_trades"] >= self.MIN_TRADES_FOR_ADAPT:
            wr_change = new_wr - self.state["kelly_win_rate"]
            wr_change = max(-self.MAX_WIN_RATE_CHANGE, min(self.MAX_WIN_RATE_CHANGE, wr_change))
            self.state["kelly_win_rate"] = self._clamp(
                self.state["kelly_win_rate"] + wr_change,
                self.WIN_RATE_FLOOR, self.WIN_RATE_CEIL
            )
            rr_change = (new_rr if new_rr is not None else self.state["ema_rr"]) - self.state["kelly_rr"]
            rr_change = max(-self.MAX_RR_CHANGE, min(self.MAX_RR_CHANGE, rr_change))
            self.state["kelly_rr"] = self._clamp(
                self.state["kelly_rr"] + rr_change,
                self.RR_FLOOR, self.RR_CEIL
            )

    # ── 策略权重自适应 ─────────────────────────────

    def _update_strategy_weights(self, trade: Trade):
        alpha = self.EMA_ALPHA
        is_win = trade.pnl > 0
        win_val = 1.0 if is_win else 0.0

        if trade.source == SignalSource.S1_MOMENTUM:
            self.state["s1_trades"] += 1
            if is_win:
                self.state["s1_wins"] += 1
            self.state["s1_ema_win_rate"] = (
                alpha * win_val + (1 - alpha) * self.state["s1_ema_win_rate"]
            )
        elif trade.source == SignalSource.S2_RSI_DIVERGENCE:
            self.state["s2_trades"] += 1
            if is_win:
                self.state["s2_wins"] += 1
            self.state["s2_ema_win_rate"] = (
                alpha * win_val + (1 - alpha) * self.state["s2_ema_win_rate"]
            )

        if (self.state["s1_trades"] >= self.MIN_TRADES_FOR_ADAPT and
                self.state["s2_trades"] >= self.MIN_TRADES_FOR_ADAPT):
            s1_wr = self.state["s1_ema_win_rate"]
            s2_wr = self.state["s2_ema_win_rate"]
            base_s1 = settings.s1_weight
            base_s2 = settings.s2_weight
            s1_factor = s1_wr / max(settings.kelly_win_rate, 0.01)
            s2_factor = s2_wr / max(settings.kelly_win_rate, 0.01)
            denom = base_s1 * s1_factor + base_s2 * s2_factor + 1e-9
            new_s1 = base_s1 * 0.6 + (base_s1 * s1_factor / denom) * 0.4
            s1_change = new_s1 - self.state["s1_weight"]
            s1_change = max(-self.MAX_WEIGHT_CHANGE, min(self.MAX_WEIGHT_CHANGE, s1_change))
            self.state["s1_weight"] = self._clamp(
                self.state["s1_weight"] + s1_change,
                self.WEIGHT_FLOOR, self.WEIGHT_CEIL
            )
            self.state["s2_weight"] = 1.0 - self.state["s1_weight"]

    # ── 波动率自适应止损 ─────────────────────────────

    def get_stop_atr_mult(self) -> float:
        return self.state["stop_atr_mult"]

    def get_tp_atr_mult(self) -> float:
        return self.state["tp_atr_mult"]

    def update_volatility_regime(self, current_atr_ratio: float):
        self.state["recent_atr_ratios"].append(current_atr_ratio)
        if len(self.state["recent_atr_ratios"]) < 10:
            return
        avg_ratio = sum(self.state["recent_atr_ratios"][-10:]) / 10
        old_regime = self.state["volatility_regime"]
        if avg_ratio > 1.5:
            self.state["volatility_regime"] = "high"
        elif avg_ratio < 0.7:
            self.state["volatility_regime"] = "low"
        else:
            self.state["volatility_regime"] = "normal"
        if old_regime != self.state["volatility_regime"]:
            self._adjust_stop_tp_for_regime()

    def _adjust_stop_tp_for_regime(self):
        regime = self.state["volatility_regime"]
        base_stop = settings.s1_stop_atr_mult
        base_tp = settings.s1_tp_atr_mult
        if regime == "high":
            target_stop = base_stop * 1.4
            target_tp = base_tp * 1.4
        elif regime == "low":
            target_stop = base_stop * 0.85
            target_tp = base_tp * 0.9
        else:
            target_stop = base_stop
            target_tp = base_tp
        stop_change = target_stop - self.state["stop_atr_mult"]
        stop_change = max(-self.MAX_ATR_MULT_CHANGE, min(self.MAX_ATR_MULT_CHANGE, stop_change))
        self.state["stop_atr_mult"] = self._clamp(
            self.state["stop_atr_mult"] + stop_change,
            self.STOP_ATR_FLOOR, self.STOP_ATR_CEIL
        )
        tp_change = target_tp - self.state["tp_atr_mult"]
        tp_change = max(-self.MAX_ATR_MULT_CHANGE, min(self.MAX_ATR_MULT_CHANGE, tp_change))
        self.state["tp_atr_mult"] = self._clamp(
            self.state["tp_atr_mult"] + tp_change,
            self.TP_ATR_FLOOR, self.TP_ATR_CEIL
        )
        log.info(
            f"🌊 波动率环境切换 → {regime}",
            stop_atr=f"{self.state['stop_atr_mult']:.2f}",
            tp_atr=f"{self.state['tp_atr_mult']:.2f}",
            rr=f"{self.state['tp_atr_mult'] / self.state['stop_atr_mult']:.1f}",
        )

    # ── 统一更新入口 ─────────────────────────────

    def on_trade_completed(self, trade: Trade):
        self._update_kelly_params(trade)
        self._update_strategy_weights(trade)
        self.state["trade_history"].append({
            "source": trade.source.value,
            "pnl": trade.pnl,
            "close_reason": trade.close_reason,
            "timestamp": trade.closed_at.isoformat() if trade.closed_at else "",
        })
        self._save_state()
        log.info(
            "🔧 自适应参数更新",
            kelly_wr=f"{self.state['kelly_win_rate']:.1%}",
            kelly_rr=f"{self.state['kelly_rr']:.2f}",
            s1_w=f"{self.state['s1_weight']:.0%}",
            s2_w=f"{self.state['s2_weight']:.0%}",
            stop=f"{self.state['stop_atr_mult']:.2f}x",
            tp=f"{self.state['tp_atr_mult']:.2f}x",
            vol_regime=self.state["volatility_regime"],
            trades=self.state["total_trades"],
        )

    def get_report(self) -> dict:
        return {
            "kelly_win_rate": self.state["kelly_win_rate"],
            "kelly_rr": self.state["kelly_rr"],
            "s1_weight": self.state["s1_weight"],
            "s2_weight": self.state["s2_weight"],
            "stop_atr_mult": self.state["stop_atr_mult"],
            "tp_atr_mult": self.state["tp_atr_mult"],
            "volatility_regime": self.state["volatility_regime"],
            "total_trades": self.state["total_trades"],
            "s1_trades": self.state["s1_trades"],
            "s2_trades": self.state["s2_trades"],
            "s1_win_rate": self.state["s1_wins"] / max(self.state["s1_trades"], 1),
            "s2_win_rate": self.state["s2_wins"] / max(self.state["s2_trades"], 1),
            "is_adapting": self.state["total_trades"] >= self.MIN_TRADES_FOR_ADAPT,
        }

    @staticmethod
    def _clamp(value: float, floor: float, ceil: float) -> float:
        return max(floor, min(ceil, value))
