"""v7.5.5 AI 客户端 — DeepSeek/OpenAI API 封装

设计原则:
- 统一接口，支持多提供商
- 内置重试 + 超时保护
- 连续失败自动降级
"""
import json
import time
import requests
from config.settings import settings
from utils.logger import log


class AIClient:
    """LLM API 客户端 — v7.5.5"""

    def __init__(self, api_key: str = None, provider: str = None,
                 model: str = None, base_url: str = None):
        self.provider = provider or settings.ai_provider
        self.api_key = api_key or settings.ai_api_key
        self.model = model or settings.ai_model
        self.base_url = base_url or settings.ai_api_base or self._default_base()
        self.timeout = settings.ai_timeout
        self.temperature = settings.ai_temperature
        self.fail_count = 0
        self.total_calls = 0
        self.total_success = 0
        self.total_fail = 0

        if not self.api_key:
            log.warning("AI: API key 未配置，AI 功能已禁用")
            self.enabled = False
        else:
            self.enabled = True

    def _default_base(self) -> str:
        if self.provider == "deepseek":
            return "https://api.deepseek.com/v1"
        elif self.provider == "openai":
            return "https://api.openai.com/v1"
        raise ValueError(f"Unknown provider: {self.provider}")

    def is_available(self) -> bool:
        return self.enabled and self.fail_count < settings.ai_max_fail_count

    def chat(self, messages: list, temperature: float = None) -> dict:
        """发送 chat completion 请求，返回解析后的 JSON dict

        Args:
            messages: [{"role": "system/user", "content": "..."}]
            temperature: 覆盖默认温度

        Returns:
            解析后的 JSON dict

        Raises:
            Exception: API 调用失败时抛出（调用方负责降级处理）
        """
        if not self.enabled:
            raise RuntimeError("AI client disabled (no API key)")

        self.total_calls += 1
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature or self.temperature,
            "response_format": {"type": "json_object"},
        }

        for attempt in range(settings.ai_retry_attempts):
            try:
                resp = requests.post(
                    url, headers=headers, json=payload,
                    timeout=self.timeout,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    result = json.loads(content)
                    self.fail_count = 0
                    self.total_success += 1
                    return result
                elif resp.status_code == 429:
                    wait = min(2 ** attempt, 10)
                    log.warning(f"AI rate limited, retry in {wait}s (attempt {attempt+1})")
                    time.sleep(wait)
                    continue
                else:
                    log.error(f"AI API error {resp.status_code}: {resp.text[:200]}")
                    self.fail_count += 1
                    self.total_fail += 1
                    if attempt < settings.ai_retry_attempts - 1:
                        time.sleep(1)
                        continue
                    raise RuntimeError(f"AI API returned {resp.status_code}")

            except requests.Timeout:
                log.error(f"AI API timeout (attempt {attempt+1})")
                if attempt < settings.ai_retry_attempts - 1:
                    time.sleep(1)
                    continue
                self.fail_count += 1
                self.total_fail += 1
                raise RuntimeError("AI API timeout after retries")

            except json.JSONDecodeError as e:
                log.error(f"AI response parse error: {e}")
                self.fail_count += 1
                self.total_fail += 1
                raise

        self.fail_count += 1
        self.total_fail += 1
        raise RuntimeError("AI API failed after all retries")

    def stats(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "enabled": self.enabled,
            "total_calls": self.total_calls,
            "success": self.total_success,
            "fail": self.total_fail,
            "consecutive_fails": self.fail_count,
            "degraded": self.fail_count >= settings.ai_max_fail_count,
        }
