"""v7.7 BTC/SOL量化交易系统 - 配置管理（v7.7: breakeven移止损+TrailingStop+TP=2.5x）"""
from pydantic_settings import BaseSettings
from typing import Optional, List
from pydantic import Field


class TradingSettings(BaseSettings):
    """交易系统核心配置 — v7.5参数（workbuddy审阅优化）"""

    # === 资金与杠杆 ===
    initial_capital: float = 500.0
    small_capital_mode: bool = True
    max_leverage: int = 20
    dynamic_leverage: bool = True            # v7.6: ATR动态杠杆（高波动降杠杆）
    leverage_min: int = 10                   # v7.6: 最低杠杆
    leverage_max: int = 30                   # v7.6: 最高杠杆
    leverage_atr_ref: float = 0.015          # v7.6: ATR参考值（1.5%为基准）
    maintenance_margin_rate: float = 0.005  # BTC约0.5%

    # === 单笔风控 ===
    single_trade_risk: float = 0.02          # 2% per trade
    max_position_ratio: float = 0.35         # v7.6.4: 35% 总仓位上限（原50%，降低30%防爆仓）
    min_order_value_usdt: float = 20.0       # v7.5: 最小名义价值（$12→$20，配合手续费门控收紧）
    slippage_buffer: float = 0.05            # 仓位×0.95
    min_risk_reward: float = 1.5             # 最低盈亏比

    # === 手续费 ===
    fee_rate_taker: float = 0.0005           # 0.05%/边 (Hyperliquid taker)
    fee_rate_total: float = 0.0006           # 0.06% 开平仓合计(含滑点)
    max_fee_ratio_of_risk: float = 0.30      # v7.5: 手续费≤30%风险预算（50%太宽松，压缩EV）

    # === 时间框架 ===
    direction_timeframe: str = "4h"          # 4H定方向
    entry_timeframe: str = "15m"             # 15m找入场 (v7.0核心)
    candle_limit: int = 200                  # 拉取K线数量

    # === 交易标的 ===
    symbols: List[str] = ["BTC/USDT", "SOL/USDT"]  # v7.5.6: 移除BNB（历史胜率42.9%，唯一亏损标的）

    # === 策略参数 ===
    # S1: 多TF动量趋势
    s1_weight: float = 0.70
    s1_min_confirm: int = 4                  # v7.3: 4/5入场（修复做空4项不可能过5的bug）
    s1_stop_atr_mult: float = 2.0           # v7.5.7: 1.5→2.0x ATR止损（ETH单笔亏-86止损过紧，与s2_stop_atr_mult统一）
    s1_tp_atr_mult: float = 2.5             # v7.7: 4.5x->2.5x (price only travels ~1%, TP too far)
    s1_breakeven_trigger: float = 0.008      # v7.6.2: 0.3%->0.8% (BTC/SOL优化)# v7.5.6: 0.5%→0.3%浮盈→保本（更快锁定利润）
    s1_trailing_stop: float = 0.006          # 0.6%跟踪止损
    s1_partial_tp_trigger: float = 0.010     # v7.6.2: 0.4%->1.0% (BTC/SOL优化)# v7.5.6: 0.6%→0.4%浮盈→减仓50%（更早锁利润）
    s1_partial_tp_ratio: float = 0.50        # 减仓比例

    # S2: RSI背离反转
    s2_weight: float = 0.30
    s2_volume_confirm: float = 0.05           # v7.6.4: 0.5x
    # v7.6.5: Session-aware parameters
    session_aware_enabled: bool = True
    session_asian_start: int = 0
    session_asian_end: int = 8
    session_european_start: int = 8
    session_european_end: int = 16
    session_us_start: int = 16
    session_us_end: int = 24
    asian_s1_weight: float = 0.60
    asian_s2_weight: float = 0.40
    asian_s2_volume_confirm: float = 0.05
    asian_max_position_ratio: float = 0.35
    european_s1_weight: float = 0.70
    european_s2_weight: float = 0.30
    european_s2_volume_confirm: float = 0.20
    european_max_position_ratio: float = 0.35
    us_s1_weight: float = 0.85
    us_s2_weight: float = 0.15
    us_s2_volume_confirm: float = 0.30
    us_max_position_ratio: float = 0.35
    weekend_s1_weight: float = 0.50
    weekend_s2_weight: float = 0.50
    weekend_s2_volume_confirm: float = 0.05
    weekend_max_position_ratio: float = 0.15
    s2_allow_counter_trend: bool = True       # 允许逆势
    s2_stop_atr_mult: float = 1.8            # v7.5.6: 1.2→1.8x ATR止损（ETH/BNB止损太紧，加宽防扫损）
    s2_tp_risk_mult: float = 2.5             # 2.5x风险止盈

    # === 4H方向判定 ===
    adx_threshold: int = 22                   # v7.3: ADX>22（28→22，28太严crypto趋势多数不够）
    direction_min_score: int = 3              # ≥3条做多, ≤1条做空
    direction_4h_adx_min: int = 0             # v7.3: 禁用（与adx_threshold重复过滤）

    # === 15m入场条件 ===
    atr_volatility_cap: float = 0.012         # BTC/ETH: ATR/价格<1.2%
    atr_volatility_cap_alt: float = 0.018       # v7.5: SOL/BNB: ATR/价格<1.8%（波动率是BTC 3-4倍）
    alt_symbols: list = ["SOL/USDT"]  # v7.5.6: 移除BNB，保留SOL
    rsi_overbought: float = 68                # RSI<68不追高
    atr_spike_mult: float = 2.5               # ATR突增2.5x过滤

    # === 信号聚合 ===
    base_signal_threshold: float = 0.6        # 基础门槛
    conflict_signal_threshold: float = 0.7    # 冲突门槛 (方向矛盾时提升)
    counter_trend_weight_factor: float = 0.5  # 逆势权重减半
    s2_counter_trend_threshold: float = 0.15  # v7.5.4: S2逆势独立门槛
    s2_solo_threshold: float = 0.25          # v7.6: S2顺向独立门槛（无S1时用）  # v7.5.4: S2逆势独立门槛（=0.5×0.30，解决S2逆势永远无法通过0.60聚合门槛的结构性问题）
    direction_diff_min: float = 0.30          # 方向差异>30%

    # === 组合风控 ===
    max_daily_loss: float = 0.12              # 12%
    max_weekly_loss: float = 0.15             # 15% ($75)
    max_drawdown: float = 0.20                # 20% 最大回撤止损
    consecutive_loss_pause: int = 3           # 连亏3次暂停
    consecutive_loss_reduce: int = 3          # 连亏3次降仓
    reduce_factor: float = 0.5                # 降仓系数
    max_daily_trades: int = 50                # v7.5.6: 每日最大50次（30次仍易打满，进一步放宽）
    max_hold_hours: int = 12                  # v7.5.7: 24→12h S1持仓上限（长时间持仓积累无谓风险）
    max_hold_hours_s2: int = 8                   # v7.5: S2反转策略最长8h（反转2-6h兑现，24h太长）
    stop_cooldown_minutes: int = 45          # v7.5.7: 30→45分钟冷却（ETH连续被扫，延长冷却防重复入场）
    early_stop_buffer: float = 1.5           # v7.5.7: 1.3→1.5宽缓冲（开仓30min内止损太容易被噪音扫）
    max_same_direction_positions: int = 2      # v7.2新增: 同方向最多2个头寸（相关性风控）

    # === 极端行情 ===
    extreme_move_pct: float = 0.025           # 15分钟2.5%→熔断
    extreme_spread_pct: float = 0.005         # 价差>0.5%→限价单
    limit_order_timeout: int = 90             # 限价单超时90秒
    api_fail_threshold: int = 3               # 连续3次API失败暂停
    crash_count_threshold: int = 5           # v7.6: 1小时内崩溃5次进入安全模式
    crash_window_minutes: int = 60           # v7.6: 崩溃计数窗口
    safe_mode_duration_minutes: int = 30     # v7.6: 安全模式持续时间

    # === 爆仓监控 ===
    liq_warning_distance: float = 0.03        # v7.5: <3%警告（20x杠杆爆仓距离约4.5%，4%预警一进场就亮）
    liq_defensive_distance: float = 0.025      # v7.6.4: 2.5%防御平仓（原2%，增加爆仓缓冲）

    # === 资金费率 ===
    funding_rate_threshold: float = 0.001      # 0.1%/8h

    # === 凯利仓位 ===
    kelly_fraction: float = 0.5               # 半凯利
    kelly_win_rate: float = 0.55              # v7.5.7: 0.45→0.40，仓位更保守（实际盈亏比0.70，ATR止损偏大）
    kelly_rr: float = 2.0                     # v7.5.7: 2.0→1.5，贴近实际盈亏比（实测0.70:1，偏保守缩小仓位）
    kelly_floor_pct: float = 0.02             # 地板保护2.0%

    # === 数据存储 ===
    db_path: str = "data/trading.db"
    equity_snapshot_interval: int = 3600     # v7.6: 权益快照间隔(秒)，默认1小时
    db_backup_keep_days: int = 7             # v7.6: DB备份保留天数
    db_backup_dir: str = "data/backups"      # v7.6: DB备份目录
    data_dir: str = "data/market"
    log_dir: str = "data/logs"

    # === 运行模式 ===
    paper_trading: bool = True                 # 默认纸交易
    dry_run: bool = False                      # 仅输出信号不下单

    # === Hyperliquid API (纸交易可不填) ===
    hyperliquid_wallet: Optional[str] = None       # 钱包地址
    hyperliquid_private_key: Optional[str] = None  # Ed25519私钥(base64)

    # === WxPusher通知 ===
    wxpusher_app_token: Optional[str] = None       # WxPusher App Token
    wxpusher_uids: List[str] = []                  # WxPusher UID列表
    notify_trade_open: bool = True           # v7.6: 开仓通知
    notify_trade_close: bool = True          # v7.6: 平仓通知
    notify_signal_block: bool = False        # v7.6: 信号拦截通知(噪音多)
    notify_daily_report: bool = True         # v7.6: 每日报告
    notify_health_alert: bool = True         # v7.6: 健康告警
    notify_risk_alert: bool = True           # v7.6: 风控告警


    # === AI 辅助决策 (v7.5.5) ===
    ai_enabled: bool = True                    # 总开关
    ai_signal_filter_enabled: bool = True       # L1: 信号二次确认
    ai_regime_analysis_enabled: bool = True     # v7.6: L2市场环境分类(规则版)    # L2: 市场环境分类(待实施)
    ai_provider: str = "deepseek"          # deepseek | openai
    ai_api_key: Optional[str] = None  # API Key (从.env或环境变量读取)
    ai_api_base: Optional[str] = None           # 自定义API地址(可选)
    ai_model: str = "deepseek-chat"         # 模型名称
    ai_temperature: float = 0.1                 # 低温度=更确定性
    ai_timeout: int = 10                        # API超时秒数
    ai_retry_attempts: int = 2                  # 失败重试次数
    ai_max_fail_count: int = 3                  # 连续失败降级阈值
    ai_fallback_approve: bool = True            # API不可用时默认放行
    ai_max_daily_calls: int = 200               # 日调用上限(防费用失控)
    ai_debug_log: bool = True                   # 输出AI详细日志
    signal_analysis_enabled: bool = True     # v7.6: 启用信号分析反馈
    signal_analysis_window: int = 100        # v7.6: 信号分析窗口(最近N个信号)                   # 输出AI详细日志
    # === v7.6: 资源监控 ===
    monitor_enabled: bool = True             # v7.6: 启用资源监控
    monitor_interval: int = 300              # v7.6: 监控检查间隔(秒)
    monitor_memory_warning_mb: int = 700     # v7.6: 内存预警阈值
    monitor_cpu_warning_pct: float = 80      # v7.6: CPU预警阈值
    monitor_disk_warning_pct: float = 85     # v7.6: 磁盘预警阈值

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = TradingSettings()
