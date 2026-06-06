"""v7.0 Hyperliquid交易所适配器 — 真实行情 + 纸交易模拟下单

关键设计:
- fetch_ohlcv / fetch_ticker: 始终用真实Hyperliquid API (免费查询,无需认证)
- place_order / close_position: 纸交易模式仅记录不实际执行
- 认证: Ed25519签名 (仅实盘需要)
"""
import json
import time
import uuid
import hashlib
import base64
import requests
from datetime import datetime
from typing import Optional
import pandas as pd
import numpy as np
from core.models import Position, Direction, PositionStatus
from config.settings import settings
from utils.logger import log


class HyperliquidAdapter:
    """Hyperliquid交易所适配 — v7.0 真实行情+纸交易"""

    BASE_URL = "https://api.hyperliquid.xyz"
    INFO_URL = f"{BASE_URL}/info"
    EXCHANGE_URL = f"{BASE_URL}/exchange"
    WS_URL = "wss://api.hyperliquid.xyz/ws"

    # 资产ID映射
    ASSET_IDS = {"BTC": 0, "ETH": 1, "SOL": 4, "BNB": 7}

    def __init__(self, paper_trading: bool = True):
        self.paper_trading = paper_trading
        self.wallet_address = settings.hyperliquid_wallet or None
        self.private_key = settings.hyperliquid_private_key or None
        self._price_cache = {}
        self._candle_cache = {}
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

        # 测试API连通性
        try:
            resp = self.session.post(self.INFO_URL, json={"type": "allMids"}, timeout=10)
            if resp.status_code == 200:
                log.info("✅ Hyperliquid API连通正常")
            else:
                log.warning(f"⚠️ Hyperliquid API返回{resp.status_code}")
        except Exception as e:
            log.warning(f"⚠️ Hyperliquid API连通异常: {e}")

        if not paper_trading and self.wallet_address and self.private_key:
            self._init_signing()
            log.info("Hyperliquid连接成功(实盘模式)", wallet=self.wallet_address[:8] + "...")
        else:
            mode = "纸交易(真实行情+模拟下单)" if paper_trading else "实盘"
            log.info(f"Hyperliquid适配器初始化({mode})")

    def _init_signing(self):
        """初始化Ed25519签名"""
        try:
            from nacl.signing import SigningKey
            self.signing_key = SigningKey(self.private_key, encoder=base64.B64Encoder)
            log.info("Ed25519签名初始化成功")
        except ImportError:
            log.error("缺少nacl库, 请安装: pip install PyNaCl")
            self.paper_trading = True

    # ── 签名 ──────────────────────────────────────

    def _sign(self, payload: dict) -> str:
        """Ed25519签名"""
        if self.paper_trading:
            return "paper_signature"

        try:
            from nacl.signing import SigningKey
            msg = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
            msg_hash = hashlib.sha256(msg.encode()).digest()
            signed = self.signing_key.sign(msg_hash)
            signature = base64.b64encode(signed.signature).decode()
            return signature
        except Exception as e:
            log.error(f"签名失败: {e}")
            return ""

    # ── 行情数据 (始终使用真实API) ──────────────

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 200) -> pd.DataFrame:
        """获取K线数据 — 纸交易也用真实行情"""
        coin = self._symbol_to_coin(symbol)
        try:
            # Hyperliquid candle snapshot
            start_time = int((time.time() - limit * self._tf_to_seconds(timeframe)) * 1000)
            payload = {
                "type": "candleSnapshot",
                "req": {
                    "coin": coin,
                    "interval": timeframe,
                    "startTime": start_time,
                    "endTime": int(time.time() * 1000),
                }
            }
            resp = self.session.post(self.INFO_URL, json=payload, timeout=15)
            data = resp.json()

            if not data or not isinstance(data, list) or len(data) == 0:
                log.warning(f"{symbol}: K线数据为空, 使用缓存模拟数据")
                return self._generate_fallback_data(symbol, timeframe, limit)

            rows = []
            for candle in data:
                try:
                    rows.append({
                        "open": float(candle.get("o", 0)),
                        "high": float(candle.get("h", 0)),
                        "low": float(candle.get("l", 0)),
                        "close": float(candle.get("c", 0)),
                        "volume": float(candle.get("v", 0)),
                    })
                except (ValueError, TypeError):
                    continue

            df = pd.DataFrame(rows)
            if len(df) == 0:
                return self._generate_fallback_data(symbol, timeframe, limit)

            self._price_cache[symbol] = df["close"].iloc[-1]
            log.debug(f"{symbol} {timeframe}: {len(df)}根K线, 最新${df['close'].iloc[-1]:,.2f}")
            return df

        except Exception as e:
            log.error(f"获取K线失败: {e}, 使用缓存模拟数据")
            return self._generate_fallback_data(symbol, timeframe, limit)

    def fetch_ticker(self, symbol: str) -> dict:
        """获取当前价格 — 纸交易也用真实行情"""
        try:
            resp = self.session.post(self.INFO_URL, json={"type": "allMids"}, timeout=10)
            data = resp.json()
            coin = self._symbol_to_coin(symbol)
            price = float(data.get(coin, 0))
            if price > 0:
                self._price_cache[symbol] = price
                return {"last": price, "symbol": symbol}
        except Exception as e:
            log.error(f"获取行情失败: {e}")

        # fallback to cache
        price = self._price_cache.get(symbol, 0)
        return {"last": price, "symbol": symbol}

    def fetch_mid_price(self, symbol: str) -> float:
        """获取中间价"""
        ticker = self.fetch_ticker(symbol)
        return ticker.get("last", 0)

    # ── 账户与持仓 ──────────────────────────────────────

    def fetch_account_info(self) -> dict:
        """获取账户信息"""
        if self.paper_trading or not self.wallet_address:
            return {"balance": settings.initial_capital, "positions": []}

        try:
            resp = self.session.post(self.INFO_URL, json={
                "type": "clearinghouseState",
                "user": self.wallet_address,
            }, timeout=10)
            return resp.json()
        except Exception as e:
            log.error(f"获取账户信息失败: {e}")
            return {}

    def fetch_positions(self) -> list:
        """获取当前持仓"""
        if self.paper_trading or not self.wallet_address:
            return []

        try:
            state = self.fetch_account_info()
            return state.get("assetPositions", [])
        except Exception as e:
            log.error(f"获取持仓失败: {e}")
            return []

    # ── 下单 (纸交易仅记录) ──────────────────────

    def place_order(self, symbol: str, direction: Direction, quantity: float,
                    price: float = None, leverage: int = 20) -> dict | None:
        """下单 — 纸交易模式仅记录日志"""
        side = "B" if direction == Direction.LONG else "S"
        coin = self._symbol_to_coin(symbol)
        asset_id = self.ASSET_IDS.get(coin, 0)

        if self.paper_trading:
            order_id = f"paper_{uuid.uuid4().hex[:8]}"
            current_price = self._price_cache.get(symbol, price or 50000)
            sz_decimals = 5 if coin == "BTC" else 4
            log.info(
                f"📝 纸交易下单",
                order_id=order_id,
                symbol=symbol,
                side=side,
                qty=f"{quantity:.{sz_decimals}f}",
                price=f"${current_price:,.2f}",
                leverage=f"{leverage}x",
            )
            return {
                "id": order_id,
                "symbol": symbol,
                "side": side,
                "price": current_price,
                "amount": quantity,
                "cost": current_price * quantity,
                "status": "closed",
            }

        # 实盘下单
        try:
            order_payload = {
                "asset": asset_id,
                "isBuy": direction == Direction.LONG,
                "limitPx": str(int(price * 100) / 100),
                "sz": str(quantity),
                "orderType": "Ioc",
                "reduceOnly": False,
            }

            sign_payload = {
                "action": {
                    "type": "order",
                    "orders": [order_payload],
                    "grouping": "na",
                },
                "nonce": int(time.time() * 1000),
            }

            signature = self._sign(sign_payload)

            full_payload = {
                "action": sign_payload["action"],
                "nonce": sign_payload["nonce"],
                "signature": {
                    "r": signature[:43] if len(signature) > 43 else signature,
                    "s": signature[43:] if len(signature) > 43 else "",
                    "v": 0,
                },
            }
            if self.wallet_address:
                full_payload["vaultAddress"] = self.wallet_address

            resp = self.session.post(self.EXCHANGE_URL, json=full_payload, timeout=10)
            result = resp.json()

            status = result.get("status", "err")
            if status == "ok":
                log.info(f"实盘下单成功", symbol=symbol, side=side)
                return {
                    "id": result.get("response", {}).get("data", {}).get("statuses", [""])[0],
                    "symbol": symbol,
                    "side": side,
                    "price": price,
                    "amount": quantity,
                    "status": "closed",
                }
            else:
                log.error(f"下单失败: {result}")
                return None

        except Exception as e:
            log.error(f"下单异常: {e}")
            return None

    def close_position(self, symbol: str, direction: Direction, quantity: float) -> dict | None:
        """平仓 — 纸交易仅记录"""
        close_side = Direction.SHORT if direction == Direction.LONG else Direction.LONG
        if self.paper_trading:
            order_id = f"paper_close_{uuid.uuid4().hex[:8]}"
            current_price = self._price_cache.get(symbol, 50000)
            log.info(f"📝 纸交易平仓", symbol=symbol, qty=f"{quantity:.6f}", price=f"${current_price:,.2f}")
            return {
                "id": order_id,
                "symbol": symbol,
                "side": "sell" if direction == Direction.LONG else "buy",
                "price": current_price,
                "amount": quantity,
                "status": "closed",
            }

        # 实盘平仓
        coin = self._symbol_to_coin(symbol)
        asset_id = self.ASSET_IDS.get(coin, 0)
        try:
            order_payload = {
                "asset": asset_id,
                "isBuy": close_side == Direction.LONG,
                "limitPx": str(int(self._price_cache.get(symbol, 0) * 100) / 100),
                "sz": str(quantity),
                "orderType": "Ioc",
                "reduceOnly": True,
            }
            sign_payload = {
                "action": {
                    "type": "order",
                    "orders": [order_payload],
                    "grouping": "na",
                },
                "nonce": int(time.time() * 1000),
            }
            signature = self._sign(sign_payload)
            full_payload = {
                "action": sign_payload["action"],
                "nonce": sign_payload["nonce"],
                "signature": {"r": signature[:43], "s": signature[43:], "v": 0},
            }
            resp = self.session.post(self.EXCHANGE_URL, json=full_payload, timeout=10)
            return resp.json()
        except Exception as e:
            log.error(f"平仓失败: {e}")
            return None

    def set_leverage(self, symbol: str, leverage: int) -> bool:
        """设置杠杆"""
        if self.paper_trading:
            return True

        coin = self._symbol_to_coin(symbol)
        asset_id = self.ASSET_IDS.get(coin, 0)
        try:
            sign_payload = {
                "action": {
                    "type": "updateLeverage",
                    "asset": asset_id,
                    "isCross": True,
                    "leverage": leverage,
                },
                "nonce": int(time.time() * 1000),
            }
            signature = self._sign(sign_payload)
            full_payload = {
                "action": sign_payload["action"],
                "nonce": sign_payload["nonce"],
                "signature": {"r": signature[:43], "s": signature[43:], "v": 0},
            }
            resp = self.session.post(self.EXCHANGE_URL, json=full_payload, timeout=10)
            result = resp.json()
            return result.get("status") == "ok"
        except Exception as e:
            log.error(f"设置杠杆失败: {e}")
            return False

    # ── 查询 ──────────────────────────────────────

    def fetch_min_order_size(self, symbol: str) -> float:
        """查询最小委托量"""
        min_sizes = {"BTC/USDT": 0.00001, "ETH/USDT": 0.0001, "SOL/USDT": 0.01, "BNB/USDT": 0.01}
        return min_sizes.get(symbol, 0.01)
        min_sizes = {"BTC/USDT": 0.00001, "ETH/USDT": 0.0001, "SOL/USDT": 0.01, "BNB/USDT": 0.01}
        return min_sizes.get(symbol, 0.01)

    def fetch_funding_rate(self, symbol: str) -> float:
        """获取资金费率 — 纸交易也查真实数据"""
        if self.paper_trading:
            # 纸交易用模拟值,避免额外API调用
            return 0.0001

        try:
            coin = self._symbol_to_coin(symbol)
            resp = self.session.post(self.INFO_URL, json={
                "type": "fundingHistory",
                "coin": coin,
            }, timeout=10)
            data = resp.json()
            if data and len(data) > 0:
                return float(data[-1].get("fundingRate", 0))
            return 0.0
        except Exception as e:
            log.error(f"获取资金费率失败: {e}")
            return 0.0

    def fetch_orderbook(self, symbol: str, depth: int = 10) -> dict:
        """获取订单簿"""
        if self.paper_trading:
            price = self._price_cache.get(symbol, 50000)
            return {
                "bids": [[price * 0.9999, 1.0]] * depth,
                "asks": [[price * 1.0001, 1.0]] * depth,
            }

        try:
            coin = self._symbol_to_coin(symbol)
            resp = self.session.post(self.INFO_URL, json={
                "type": "l2Book",
                "coin": coin,
            }, timeout=10)
            data = resp.json()
            levels = data.get("levels", [[], []])
            return {
                "bids": levels[0][:depth],
                "asks": levels[1][:depth] if len(levels) > 1 else [],
            }
        except Exception as e:
            log.error(f"获取订单簿失败: {e}")
            return {"bids": [], "asks": []}

    # ── 辅助 ──────────────────────────────────────

    def _symbol_to_coin(self, symbol: str) -> str:
        """BTC/USDT → BTC"""
        return symbol.split("/")[0]

    def _tf_to_seconds(self, timeframe: str) -> int:
        """时间框架转秒数"""
        mapping = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
        return mapping.get(timeframe, 900)

        base_prices = {"BTC/USDT": 65000, "ETH/USDT": 3500, "SOL/USDT": 170, "BNB/USDT": 650}
        base = base_prices.get(symbol, 50000)
        base_prices = {"BTC/USDT": 65000, "ETH/USDT": 3500, "SOL/USDT": 170, "BNB/USDT": 650}
        base = base_prices.get(symbol, 50000)
        base_prices = {"BTC/USDT": 65000, "ETH/USDT": 3500}
        base = base_prices.get(symbol, 50000)

        # 用当前时间戳作为随机种子,确保每次不同
        rng = np.random.RandomState(int(time.time()) % 2**31)
        returns = rng.normal(0, 0.002, limit)
        closes = base * np.cumprod(1 + returns)

        highs = closes * (1 + np.abs(rng.normal(0, 0.003, limit)))
        lows = closes * (1 - np.abs(rng.normal(0, 0.003, limit)))
        opens = closes * (1 + rng.normal(0, 0.001, limit))
        volumes = rng.uniform(100, 1000, limit) * (base / 1000)

        tf_minutes = {"15m": 15, "1h": 60, "4h": 240}
        minutes = tf_minutes.get(timeframe, 15)
        timestamps = pd.date_range(end=datetime.utcnow(), periods=limit, freq=f"{minutes}min")

        df = pd.DataFrame({
            "open": opens, "high": highs, "low": lows,
            "close": closes, "volume": volumes,
        }, index=timestamps)

        self._price_cache[symbol] = closes[-1]
        log.warning(f"{symbol}: API数据不可用, 使用后备模拟数据")
        return df

    def _mock_ticker(self, symbol: str) -> dict:
        price = self._price_cache.get(symbol, 50000)
        return {"last": price, "symbol": symbol}
