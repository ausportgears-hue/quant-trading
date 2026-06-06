"""v7.7 数据模型 — Signal, Position, Trade, DirectionJudgment"""
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"
    NEUTRAL = "neutral"


class SignalSource(str, Enum):
    S1_MOMENTUM = "S1_momentum"
    S2_RSI_DIVERGENCE = "S2_rsi_divergence"


class PositionStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    LIQUIDATED = "liquidated"


@dataclass
class DirectionJudgment:
    """4H方向判定结果"""
    score: int                    # 0-4, ≥3做多, ≤1做空, 2空仓
    direction: Direction
    ema_aligned: bool             # EMA20>50>200
    macd_bullish: bool            # MACD>0且>信号线
    adx_strong: bool              # ADX>25
    price_above_ema50: bool
    adx_value: float = 0.0
    details: str = ""


@dataclass
class Signal:
    """交易信号"""
    source: SignalSource
    symbol: str
    direction: Direction
    strength: float               # 0.0-1.0
    entry_price: float
    stop_loss: float
    take_profit: float
    atr_value: float
    risk_reward: float
    timestamp: datetime = field(default_factory=datetime.utcnow)
    direction_judgment: Optional[DirectionJudgment] = None
    is_counter_trend: bool = False  # 是否逆4H方向
    details: str = ""


@dataclass
class Position:
    """持仓"""
    id: str
    symbol: str
    direction: Direction
    entry_price: float
    quantity: float               # BTC/ETH数量
    notional_value: float         # 名义价值(USDT)
    margin: float                 # 保证金(USDT)
    leverage: int
    stop_loss: float
    take_profit: float
    liquidation_price: float
    source: SignalSource
    is_counter_trend: bool = False
    partial_closed: bool = False  # 是否已部分止盈
    opened_at: datetime = field(default_factory=datetime.utcnow)
    status: PositionStatus = PositionStatus.OPEN

    @property
    def hold_hours(self) -> float:
        now = datetime.now(self.opened_at.tzinfo) if self.opened_at.tzinfo else datetime.utcnow()
        return (now - self.opened_at).total_seconds() / 3600

    @property
    def liquidation_distance_pct(self) -> float:
        """距爆仓价的百分比(绝对值)"""
        if self.direction == Direction.LONG:
            return (self.entry_price - self.liquidation_price) / self.entry_price
        else:
            return (self.liquidation_price - self.entry_price) / self.entry_price


@dataclass
class Trade:
    """已完成的交易记录"""
    position_id: str
    symbol: str
    direction: Direction
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float                    # 盈亏(USDT)
    pnl_pct: float                # 盈亏百分比
    fee: float                    # 手续费
    source: SignalSource
    opened_at: datetime
    closed_at: datetime
    close_reason: str             # stop_loss/take_profit/trailing/breakeven/partial/timeout/extreme/liquidation
    is_counter_trend: bool = False


@dataclass
class RiskState:
    """风控状态跟踪"""
    daily_pnl: float = 0.0
    weekly_pnl: float = 0.0
    total_pnl: float = 0.0
    daily_trades: int = 0
    consecutive_losses: int = 0
    peak_equity: float = 0.0
    current_equity: float = 0.0
    last_daily_reset: Optional[datetime] = None
    last_weekly_reset: Optional[datetime] = None
    is_paused: bool = False
    pause_reason: str = ""
