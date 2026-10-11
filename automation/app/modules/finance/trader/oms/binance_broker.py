"""Binance Broker implementation for the Order Management System (OMS).

Supports:
  - Binance Spot API (/api/v3)
  - Binance USD(S)-M Futures API (/fapi/v1, /fapi/v2)
  - Both Testnet and Live exchange environments
  - HMAC-SHA256 authenticated order signing with millisecond timestamps and recvWindow
  - Order submission (Market & Limit), cancellation, status polling, and account info
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Optional

import requests

from .broker import AccountInfo, Broker, BrokerOrder

logger = logging.getLogger(__name__)


class BinanceBroker(Broker):
    """Binance execution broker supporting Spot and USD(S)-M Futures."""

    # Base URLs
    SPOT_LIVE_URL = "https://api.binance.com"
    SPOT_TESTNET_URL = "https://testnet.binance.vision"
    FUTURES_LIVE_URL = "https://fapi.binance.com"
    FUTURES_TESTNET_URL = "https://testnet.binancefuture.com"

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        testnet: bool = True,
        market_type: str = "spot",  # "spot" or "futures"
        recv_window: int = 5000,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.testnet = bool(testnet)
        self.market_type = market_type.lower().strip()
        if self.market_type not in ("spot", "futures"):
            raise ValueError(f"Invalid market_type '{market_type}'. Must be 'spot' or 'futures'.")

        self.recv_window = recv_window
        self.api_key = self._resolve_credential(
            api_key,
            env_candidates=[
                "BINANCE_TESTNET_API_KEY" if self.testnet else "BINANCE_API_KEY",
                "BINANCE_API_KEY",
            ],
        )
        self.api_secret = self._resolve_credential(
            api_secret,
            env_candidates=[
                "BINANCE_TESTNET_SECRET" if self.testnet else "BINANCE_SECRET",
                "BINANCE_API_SECRET",
                "BINANCE_SECRET",
            ],
        )

        self._session = session or requests.Session()
        self.name = f"binance_{'testnet' if self.testnet else 'live'}_{self.market_type}"

    @property
    def base_url(self) -> str:
        """Return the target base URL according to market type and testnet flag."""
        if self.market_type == "futures":
            return self.FUTURES_TESTNET_URL if self.testnet else self.FUTURES_LIVE_URL
        return self.SPOT_TESTNET_URL if self.testnet else self.SPOT_LIVE_URL

    @staticmethod
    def _resolve_credential(value: Optional[str], env_candidates: list[str]) -> str:
        """Resolve API credential from literal string or environment variable lookup."""
        if value:
            # If the value corresponds to an env variable name, read from env
            if value in os.environ:
                return os.environ[value].strip()
            return value.strip()
        for env_name in env_candidates:
            val = os.environ.get(env_name)
            if val:
                return val.strip()
        return ""

    @staticmethod
    def normalize_symbol(symbol: str) -> str:
        """Map watchlist/portfolio ticker (e.g. 'BTC-USD', 'ETH/USDT') to Binance symbol."""
        s = symbol.upper().replace("/", "").replace("-", "").strip()
        if s.endswith("USD") and not s.endswith("USDT"):
            s = s[:-3] + "USDT"
        if not any(s.endswith(quote) for quote in ("USDT", "BUSD", "FDUSD", "USDC", "BTC")):
            s = s + "USDT"
        return s

    # ── Signing & HTTP Transport ──────────────────────────────────────────────

    def _generate_signature(self, query_string: str) -> str:
        """Compute HMAC-SHA256 signature for Binance API authentication."""
        if not self.api_secret:
            raise ValueError("Binance API secret is required for signed requests.")
        return hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict[str, Any]] = None,
        signed: bool = False,
    ) -> dict[str, Any]:
        """Execute HTTP request against Binance REST API."""
        url = f"{self.base_url}{path}"
        headers = {
            "Accept": "application/json",
            "User-Agent": "basheer.net-OMS/1.0",
        }
        if self.api_key:
            headers["X-MBX-APIKEY"] = self.api_key

        payload = dict(params or {})

        if signed:
            if not self.api_key:
                raise ValueError("Binance API key is required for signed requests.")
            payload["timestamp"] = int(time.time() * 1000)
            payload["recvWindow"] = self.recv_window
            query_string = urllib.parse.urlencode(payload)
            signature = self._generate_signature(query_string)
            query_string = f"{query_string}&signature={signature}"
        else:
            query_string = urllib.parse.urlencode(payload) if payload else ""

        method_upper = method.upper()
        try:
            if method_upper == "GET":
                req_url = f"{url}?{query_string}" if query_string else url
                resp = self._session.get(req_url, headers=headers, timeout=10)
            elif method_upper in ("POST", "DELETE"):
                # Binance signed POST/DELETE can pass signed query string in URL or body
                req_url = f"{url}?{query_string}" if query_string else url
                if method_upper == "POST":
                    resp = self._session.post(req_url, headers=headers, timeout=10)
                else:
                    resp = self._session.delete(req_url, headers=headers, timeout=10)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            if not resp.ok:
                err_msg = f"Binance API HTTP {resp.status_code}: {resp.text}"
                logger.error("[oms.binance] %s", err_msg)
                try:
                    err_json = resp.json()
                    return {"error": True, "code": err_json.get("code"), "msg": err_json.get("msg", resp.text)}
                except Exception:
                    return {"error": True, "code": resp.status_code, "msg": resp.text}

            return resp.json()
        except Exception as e:
            logger.error("[oms.binance] Network request failed for %s: %s", path, e)
            return {"error": True, "msg": str(e)}

    # ── Status Normalization ──────────────────────────────────────────────────

    @staticmethod
    def _map_order_status(binance_status: str) -> str:
        """Map Binance order status string to standard BrokerOrder status."""
        status = binance_status.upper()
        if status in ("NEW", "PENDING_NEW"):
            return "PENDING"
        if status == "PARTIALLY_FILLED":
            return "PARTIALLY_FILLED"
        if status == "FILLED":
            return "FILLED"
        if status in ("CANCELED", "CANCELLED", "PENDING_CANCEL"):
            return "CANCELLED"
        if status in ("REJECTED", "EXPIRED"):
            return "REJECTED"
        return "PENDING"

    # ── Order Execution ───────────────────────────────────────────────────────

    def submit_order(
        self,
        ticker: str,
        side: str,
        order_type: str,
        qty: float,
        price: Optional[float] = None,
        poll_for_fill: bool = True,
    ) -> BrokerOrder:
        """Submit Spot or Futures order to Binance.

        Args:
            ticker: Asset ticker (e.g. "BTC-USD" or "BTCUSDT")
            side: "buy" or "sell"
            order_type: "market" or "limit"
            qty: Order quantity
            price: Limit price if applicable
            poll_for_fill: If True and market order is pending, poll for fill
        """
        symbol = self.normalize_symbol(ticker)
        side_upper = side.upper()
        type_upper = order_type.upper()

        params: dict[str, Any] = {
            "symbol": symbol,
            "side": side_upper,
            "type": type_upper,
            "quantity": qty,
        }

        if type_upper == "LIMIT":
            if price is None or price <= 0:
                return BrokerOrder(
                    id=f"binance_rej_{int(time.time())}",
                    ticker=ticker,
                    side=side,
                    order_type=order_type,
                    qty=qty,
                    status="REJECTED",
                    broker=self.name,
                    notes="Limit order requires valid price",
                )
            params["price"] = price
            params["timeInForce"] = "GTC"

        endpoint = "/fapi/v1/order" if self.market_type == "futures" else "/api/v3/order"
        res = self._request("POST", endpoint, params=params, signed=True)

        if res.get("error"):
            return BrokerOrder(
                id=f"binance_err_{int(time.time())}",
                ticker=ticker,
                side=side,
                order_type=order_type,
                qty=qty,
                price=price,
                status="REJECTED",
                broker=self.name,
                notes=f"Binance rejected: {res.get('msg')} (code {res.get('code')})",
            )

        order_id = str(res.get("orderId", ""))
        status = self._map_order_status(res.get("status", "NEW"))

        # Poll if market order is not immediately marked FILLED
        if poll_for_fill and status in ("PENDING", "PARTIALLY_FILLED") and type_upper == "MARKET" and order_id:
            for _ in range(3):
                time.sleep(0.1)
                polled = self.get_order_status(ticker, order_id)
                if polled.status in ("FILLED", "CANCELLED", "REJECTED"):
                    return polled

        # Determine executed price
        filled_price = None
        executed_qty = float(res.get("executedQty", 0.0) or 0.0)
        cummulative_quote = float(res.get("cummulativeQuoteQty", 0.0) or 0.0)

        if self.market_type == "futures":
            avg_px = res.get("avgPrice")
            if avg_px and float(avg_px) > 0:
                filled_price = float(avg_px)
        elif "fills" in res and res["fills"]:
            # Compute weighted average fill price from Spot fills array
            total_val = sum(float(f["price"]) * float(f["qty"]) for f in res["fills"])
            total_q = sum(float(f["qty"]) for f in res["fills"])
            if total_q > 0:
                filled_price = round(total_val / total_q, 6)

        if filled_price is None and executed_qty > 0 and cummulative_quote > 0:
            filled_price = round(cummulative_quote / executed_qty, 6)

        if filled_price is None and status == "FILLED":
            filled_price = price or self.get_current_price(ticker)

        now = datetime.now(timezone.utc)
        return BrokerOrder(
            id=order_id,
            ticker=ticker,
            side=side,
            order_type=order_type,
            qty=qty,
            price=price or filled_price,
            status=status,
            filled_price=filled_price,
            filled_at=now if status == "FILLED" else None,
            broker=self.name,
            notes=f"Binance {self.market_type} order status: {res.get('status')}",
        )

    def close_position(
        self,
        ticker: str,
        side: str,
        qty: float,
        exit_price: Optional[float] = None,
    ) -> BrokerOrder:
        """Close position by submitting opposing market order."""
        return self.submit_order(
            ticker=ticker,
            side=side,
            order_type="market",
            qty=qty,
            price=exit_price,
        )

    def cancel_order(self, ticker: str, broker_order_id: str) -> BrokerOrder:
        """Cancel an open order on Binance."""
        symbol = self.normalize_symbol(ticker)
        endpoint = "/fapi/v1/order" if self.market_type == "futures" else "/api/v3/order"
        params = {"symbol": symbol, "orderId": broker_order_id}

        res = self._request("DELETE", endpoint, params=params, signed=True)
        if res.get("error"):
            return BrokerOrder(
                id=broker_order_id,
                ticker=ticker,
                side="",
                order_type="",
                qty=0.0,
                status="REJECTED",
                broker=self.name,
                notes=f"Cancel failed: {res.get('msg')}",
            )

        return BrokerOrder(
            id=broker_order_id,
            ticker=ticker,
            side=res.get("side", "").lower(),
            order_type=res.get("type", "").lower(),
            qty=float(res.get("origQty", 0.0) or 0.0),
            status=self._map_order_status(res.get("status", "CANCELED")),
            broker=self.name,
            notes="Order cancelled on Binance",
        )

    def get_order_status(self, ticker: str, broker_order_id: str) -> BrokerOrder:
        """Query current status of an order from Binance."""
        symbol = self.normalize_symbol(ticker)
        endpoint = "/fapi/v1/order" if self.market_type == "futures" else "/api/v3/order"
        params = {"symbol": symbol, "orderId": broker_order_id}

        res = self._request("GET", endpoint, params=params, signed=True)
        if res.get("error"):
            return BrokerOrder(
                id=broker_order_id,
                ticker=ticker,
                side="",
                order_type="",
                qty=0.0,
                status="REJECTED",
                broker=self.name,
                notes=f"Query failed: {res.get('msg')}",
            )

        status = self._map_order_status(res.get("status", "NEW"))
        executed_qty = float(res.get("executedQty", 0.0) or 0.0)
        cummulative_quote = float(res.get("cummulativeQuoteQty", 0.0) or 0.0)
        filled_price = None

        if self.market_type == "futures":
            avg_px = res.get("avgPrice")
            if avg_px and float(avg_px) > 0:
                filled_price = float(avg_px)
        elif executed_qty > 0 and cummulative_quote > 0:
            filled_price = round(cummulative_quote / executed_qty, 6)

        return BrokerOrder(
            id=broker_order_id,
            ticker=ticker,
            side=res.get("side", "").lower(),
            order_type=res.get("type", "").lower(),
            qty=float(res.get("origQty", 0.0) or 0.0),
            price=float(res.get("price", 0.0) or 0.0),
            status=status,
            filled_price=filled_price,
            broker=self.name,
            notes=f"Binance status: {res.get('status')}",
        )

    # ── Market Data & Account ─────────────────────────────────────────────────

    def get_current_price(self, ticker: str, category: str = "crypto") -> float:
        """Fetch current market price via Binance ticker endpoint."""
        symbol = self.normalize_symbol(ticker)
        endpoint = "/fapi/v1/ticker/price" if self.market_type == "futures" else "/api/v3/ticker/price"
        res = self._request("GET", endpoint, params={"symbol": symbol}, signed=False)

        if not res.get("error") and "price" in res:
            try:
                return float(res["price"])
            except (ValueError, TypeError):
                pass

        # Fallback to BinanceProvider in data.py
        try:
            from ..data import BinanceProvider
            return BinanceProvider().get_current_price(ticker)
        except Exception:
            return 0.0

    def get_account_info(
        self,
        open_positions: Optional[list] = None,
        db=None,
    ) -> AccountInfo:
        """Fetch account balance, equity, and unrealised P&L from Binance."""
        if not self.api_key or not self.api_secret:
            logger.warning("[oms.binance] API credentials missing, returning zero balance")
            return AccountInfo(
                balance=0.0,
                equity=0.0,
                unrealised_pnl=0.0,
                currency="USDT",
                broker_name=self.name,
            )

        if self.market_type == "futures":
            # Futures Account Endpoint: /fapi/v2/account
            res = self._request("GET", "/fapi/v2/account", signed=True)
            if res.get("error"):
                logger.error("[oms.binance] Futures account info failed: %s", res.get("msg"))
                return AccountInfo(
                    balance=0.0,
                    equity=0.0,
                    unrealised_pnl=0.0,
                    currency="USDT",
                    broker_name=self.name,
                )

            wallet_balance = float(res.get("totalWalletBalance", 0.0) or 0.0)
            unrealised_pnl = float(res.get("totalUnrealizedProfit", 0.0) or 0.0)
            equity = float(res.get("totalMarginBalance", wallet_balance + unrealised_pnl) or 0.0)

            return AccountInfo(
                balance=round(wallet_balance, 2),
                equity=round(equity, 2),
                unrealised_pnl=round(unrealised_pnl, 2),
                currency="USDT",
                broker_name=self.name,
            )

        # Spot Account Endpoint: /api/v3/account
        res = self._request("GET", "/api/v3/account", signed=True)
        if res.get("error"):
            logger.error("[oms.binance] Spot account info failed: %s", res.get("msg"))
            return AccountInfo(
                balance=0.0,
                equity=0.0,
                unrealised_pnl=0.0,
                currency="USDT",
                broker_name=self.name,
            )

        balances = res.get("balances", [])
        usdt_cash = 0.0
        total_equity = 0.0

        for b in balances:
            asset = b.get("asset", "")
            free = float(b.get("free", 0.0) or 0.0)
            locked = float(b.get("locked", 0.0) or 0.0)
            total_qty = free + locked
            if total_qty <= 0:
                continue

            if asset in ("USDT", "USD", "BUSD", "FDUSD", "USDC"):
                usdt_cash += free
                total_equity += total_qty
            else:
                # Value other assets at current market price in USDT
                try:
                    px = self.get_current_price(f"{asset}USDT")
                    if px > 0:
                        total_equity += total_qty * px
                except Exception:
                    pass

        # Calculate unrealised PnL based on tracked open positions if provided
        unrealised_pnl = 0.0
        if open_positions:
            for pos in open_positions:
                try:
                    curr_px = self.get_current_price(pos.ticker)
                    if curr_px > 0:
                        direction = getattr(pos, "direction", "long")
                        entry_px = float(getattr(pos, "entry_price", 0.0))
                        qty = float(getattr(pos, "qty", 0.0) or 0.0)
                        if direction == "long":
                            unrealised_pnl += (curr_px - entry_px) * qty
                        else:
                            unrealised_pnl += (entry_px - curr_px) * qty
                except Exception:
                    pass

        return AccountInfo(
            balance=round(usdt_cash, 2),
            equity=round(total_equity if total_equity > 0 else (usdt_cash + unrealised_pnl), 2),
            unrealised_pnl=round(unrealised_pnl, 2),
            currency="USDT",
            broker_name=self.name,
        )
