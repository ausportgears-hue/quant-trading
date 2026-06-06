"""v7.0 通知推送 — WxPusher微信通知"""
import requests
from typing import Optional
from utils.logger import log


class Notifier:
    """WxPusher交易信号推送"""

    def __init__(self):
        self.app_token = None
        self.uids = []
        self.enabled = False
        self._load_config()

    def _load_config(self):
        from config.settings import settings
        if hasattr(settings, 'wxpusher_app_token') and settings.wxpusher_app_token:
            self.app_token = settings.wxpusher_app_token
            self.uids = settings.wxpusher_uids or []
            self.enabled = bool(self.app_token and self.uids)
            if self.enabled:
                log.info("📢 WxPusher通知已启用", uids=len(self.uids))
            else:
                log.warning("📢 WxPusher配置不完整, 通知禁用")
        else:
            log.info("📢 WxPusher未配置, 通知禁用")

    def send(self, title: str, content: str, content_type: int = 1) -> bool:
        """发送WxPusher消息
        content_type: 1=文本, 2=HTML, 3=Markdown
        """
        if not self.enabled:
            return False
        try:
            payload = {
                "appToken": self.app_token,
                "content": content,
                "summary": title[:50],
                "contentType": content_type,
                "uids": self.uids,
            }
            resp = requests.post(
                "https://wxpusher.zjiecode.com/api/send/message",
                json=payload,
                timeout=10,
            )
            data = resp.json()
            if data.get("code") == 1000:
                log.debug(f"📢 推送成功: {title}")
                return True
            else:
                log.warning(f"📢 推送失败: {data.get('msg', 'unknown')}")
                return False
        except Exception as e:
            log.warning(f"📢 推送异常: {e}")
            return False

    def notify_trade_open(self, symbol: str, direction: str, price: float,
                          quantity: float, leverage: int, stop_loss: float,
                          take_profit: float, source: str, paper: bool = True):
        """开仓通知"""
        mode = "📝 纸交易" if paper else "🔴 实盘"
        content = f"""**{mode} 开仓信号** 🚀

**标的:** {symbol}
**方向:** {direction}
**入场价:** ${price:,.2f}
**数量:** {quantity:.6f}
**杠杆:** {leverage}x
**止损:** ${stop_loss:,.2f}
**止盈:** ${take_profit:,.2f}
**策略:** {source}"""
        title = f"{mode} {symbol} {direction}"
        self.send(title, content, content_type=3)

    def notify_trade_close(self, symbol: str, direction: str,
                           entry_price: float, exit_price: float,
                           pnl: float, reason: str, paper: bool = True):
        """平仓通知"""
        mode = "📝 纸交易" if paper else "🔴 实盘"
        emoji = "✅" if pnl >= 0 else "❌"
        content = f"""**{mode} 平仓** {emoji}

**标的:** {symbol}
**方向:** {direction}
**入场:** ${entry_price:,.2f}
**出场:** ${exit_price:,.2f}
**盈亏:** ${pnl:+.2f}
**原因:** {reason}"""
        title = f"{mode} {symbol} 平仓 {emoji} ${pnl:+.2f}"
        self.send(title, content, content_type=3)

    def notify_emergency(self, reason: str, positions: list, paper: bool = True):
        """紧急事件通知"""
        mode = "📝 纸交易" if paper else "🔴 实盘"
        pos_info = ""
        for p in positions:
            pos_info += f"\n- {p.symbol} {p.direction.value}"
        content = f"""**🚨 紧急事件** 

**模式:** {mode}
**原因:** {reason}
**持仓:** {pos_info or '无'}"""
        self.send(f"🚨 紧急: {reason}", content, content_type=3)

    def notify_risk_alert(self, alert_type: str, details: str):
        """风控预警"""
        content = f"""**⚠️ 风控预警**

**类型:** {alert_type}
**详情:** {details}"""
        self.send(f"⚠️ 风控: {alert_type}", content, content_type=3)

    def notify_daily_report(self, equity: float, daily_pnl: float,
                            trades: int, win_rate: float, drawdown: float,
                            positions: list, paper: bool = True,
                            total_fee: float = 0, net_pnl: float = 0):
        """每日报告 — v7.5: 增加手续费/净盈亏/含浮盈权益"""
        mode = "📝 纸交易" if paper else "🔴 实盘"
        pos_info = ""
        for p in positions:
            pos_info += f"\n- {p.symbol} {p.direction.value} entry=${p.entry_price:,.2f}"
        content = f"""**📊 每日交易报告** — {mode}

**权益(含浮盈):** ${equity:,.2f}
**毛盈亏:** ${daily_pnl:+.2f}
**手续费:** ${total_fee:,.2f}
**净盈亏:** ${net_pnl:+.2f}
**今日交易:** {trades}笔
**胜率:** {win_rate:.0%}
**回撤:** {drawdown:.1%}
**持仓:** {pos_info or '空仓'}"""
        self.send(f"📊 日报 | 权益${equity:,.0f} | 净PnL ${net_pnl:+.0f}", content, content_type=3)


    def notify_health_check(self, status, details, is_alert=False):
        """v7.5.2: Health check notification"""
        if is_alert:
            content = f"""**Health check alert**

**Status:** {status}
**Details:** {details}"""
            self.send(f"System Alert | {status}", content, content_type=3)
        else:
            log.debug(f"Health check: {status} - {details}")

notifier = Notifier()
