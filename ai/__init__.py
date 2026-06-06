"""AI 辅助决策模块 — v7.5.5

L1: 信号二次确认 (SignalAIFilter)
L2: 市场环境分类 (AIRegimeAnalyzer — 待实施)

设计原则:
- AI 是安全网(过滤)，不是信号源(生成)
- API 失败自动降级为纯规则模式
- 不确定时放行，只拦截明显有问题的信号
"""

from ai.ai_client import AIClient
from ai.ai_filter import SignalAIFilter

__all__ = ["AIClient", "SignalAIFilter"]
