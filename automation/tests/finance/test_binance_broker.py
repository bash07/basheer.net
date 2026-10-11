"""Unit tests for BinanceBroker and OMS Broker Factory (Roadmap Plan 1)."""
from __future__ import annotations

import hmac
import hashlib
import json
import os
import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from modules.finance.trader.oms.binance_broker import BinanceBroker
from modules.finance.trader.oms.broker import AccountInfo, BrokerOrder
from modules.finance.trader.oms.paper_broker import PaperBroker
from modules.finance.trader.oms import create_broker, get_broker, set_broker


class DummyResponse:
    """Mock requests.Response object."""

    def __init__(self, status_code: int = 200, json_data: dict | list = None, text: str = ""):
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}
        self.text = text or json.dumps(self._json_data)
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._json_data


class TestBinanceBrokerInit:
    """Test broker initialization and configuration routing."""

    def test_default_init(self):
        broker = BinanceBroker(api_key="my_key", api_secret="my_secret")
        assert broker.testnet is True
        assert broker.market_type == "spot"
        assert broker.base_url == BinanceBroker.SPOT_TESTNET_URL
        assert broker.name == "binance_testnet_spot"
        assert broker.api_key == "my_key"
        assert broker.api_secret == "my_secret"

    def test_futures_testnet_init(self):
        broker = BinanceBroker(
            api_key="k", api_secret="s", testnet=True, market_type="futures"
        )
        assert broker.testnet is True
        assert broker.market_type == "futures"
        assert broker.base_url == BinanceBroker.FUTURES_TESTNET_URL
        assert broker.name == "binance_testnet_futures"

    def test_spot_live_init(self):
        broker = BinanceBroker(
            api_key="k", api_secret="s", testnet=False, market_type="spot"
        )
        assert broker.testnet is False
        assert broker.market_type == "spot"
        assert broker.base_url == BinanceBroker.SPOT_LIVE_URL
        assert broker.name == "binance_live_spot"

    def test_futures_live_init(self):
        broker = BinanceBroker(
            api_key="k", api_secret="s", testnet=False, market_type="futures"
        )
        assert broker.testnet is False
        assert broker.market_type == "futures"
        assert broker.base_url == BinanceBroker.FUTURES_LIVE_URL
        assert broker.name == "binance_live_futures"

    def test_invalid_market_type_raises(self):
        with pytest.raises(ValueError, match="Invalid market_type"):
            BinanceBroker(market_type="options")

    def test_resolve_credential_from_env_name(self, monkeypatch):
        monkeypatch.setenv("CUSTOM_BINANCE_KEY", "secret_env_key")
        broker = BinanceBroker(api_key="CUSTOM_BINANCE_KEY", api_secret="s")
        assert broker.api_key == "secret_env_key"

    def test_resolve_credential_fallback_env(self, monkeypatch):
        monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "env_testnet_key")
        monkeypatch.setenv("BINANCE_TESTNET_SECRET", "env_testnet_secret")
        broker = BinanceBroker(testnet=True)
        assert broker.api_key == "env_testnet_key"
        assert broker.api_secret == "env_testnet_secret"


class TestBinanceSymbolNormalization:
    """Test ticker string normalization to Binance USDT pairs."""

    def test_symbol_normalization(self):
        assert BinanceBroker.normalize_symbol("BTC-USD") == "BTCUSDT"
        assert BinanceBroker.normalize_symbol("eth/usdt") == "ETHUSDT"
        assert BinanceBroker.normalize_symbol("SOL-USD") == "SOLUSDT"
        assert BinanceBroker.normalize_symbol("DOGE") == "DOGEUSDT"
        assert BinanceBroker.normalize_symbol("BTCUSDT") == "BTCUSDT"
        assert BinanceBroker.normalize_symbol("ETHBTC") == "ETHBTC"


class TestBinanceBrokerSigningAndTransport:
    """Test HMAC-SHA256 signature calculation and HTTP request execution."""

    def test_signature_generation(self):
        broker = BinanceBroker(api_key="test_key", api_secret="test_secret")
        query = "symbol=BTCUSDT&timestamp=1600000000000"
        expected = hmac.new(
            b"test_secret", query.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        assert broker._generate_signature(query) == expected

    def test_missing_secret_raises_on_signature(self):
        broker = BinanceBroker(api_key="test_key", api_secret="")
        with pytest.raises(ValueError, match="API secret is required"):
            broker._generate_signature("symbol=BTCUSDT")

    def test_request_headers_and_signature(self):
        mock_session = MagicMock()
        mock_session.get.return_value = DummyResponse(200, {"serverTime": 1234567890})

        broker = BinanceBroker(
            api_key="test_key",
            api_secret="test_secret",
            session=mock_session,
        )

        res = broker._request("GET", "/api/v3/time", params={"param1": "val1"}, signed=True)
        assert res["serverTime"] == 1234567890

        call_args = mock_session.get.call_args
        called_url = call_args[0][0]
        called_headers = call_args[1]["headers"]

        assert "X-MBX-APIKEY" in called_headers
        assert called_headers["X-MBX-APIKEY"] == "test_key"
        assert "timestamp=" in called_url
        assert "signature=" in called_url
        assert "recvWindow=5000" in called_url


class TestBinanceBrokerOrders:
    """Test order submission, fills, status, cancellation."""

    def test_submit_spot_market_order_filled(self):
        mock_session = MagicMock()
        mock_session.post.return_value = DummyResponse(
            200,
            {
                "symbol": "BTCUSDT",
                "orderId": 987654321,
                "status": "FILLED",
                "executedQty": "0.05",
                "cummulativeQuoteQty": "3000.0",
                "fills": [
                    {"price": "60000.0", "qty": "0.05", "commission": "0.0", "commissionAsset": "USDT"}
                ],
            },
        )

        broker = BinanceBroker(
            api_key="k", api_secret="s", session=mock_session, market_type="spot"
        )
        order = broker.submit_order(
            ticker="BTC-USD", side="buy", order_type="market", qty=0.05
        )

        assert isinstance(order, BrokerOrder)
        assert order.id == "987654321"
        assert order.ticker == "BTC-USD"
        assert order.side == "buy"
        assert order.status == "FILLED"
        assert order.filled_price == 60000.0
        assert order.filled_at is not None

    def test_submit_limit_order_pending(self):
        mock_session = MagicMock()
        mock_session.post.return_value = DummyResponse(
            200,
            {
                "symbol": "BTCUSDT",
                "orderId": 12345,
                "status": "NEW",
                "price": "55000.0",
                "origQty": "0.1",
                "executedQty": "0.0",
            },
        )

        broker = BinanceBroker(
            api_key="k", api_secret="s", session=mock_session, market_type="spot"
        )
        order = broker.submit_order(
            ticker="BTC-USD", side="buy", order_type="limit", qty=0.1, price=55000.0, poll_for_fill=False
        )

        assert order.id == "12345"
        assert order.status == "PENDING"
        assert order.price == 55000.0

    def test_limit_order_missing_price_rejected(self):
        broker = BinanceBroker(api_key="k", api_secret="s")
        order = broker.submit_order(
            ticker="BTC-USD", side="buy", order_type="limit", qty=0.1, price=None
        )
        assert order.status == "REJECTED"
        assert "requires valid price" in order.notes

    def test_submit_futures_market_order(self):
        mock_session = MagicMock()
        mock_session.post.return_value = DummyResponse(
            200,
            {
                "symbol": "BTCUSDT",
                "orderId": 556677,
                "status": "FILLED",
                "executedQty": "0.2",
                "avgPrice": "62000.5",
            },
        )

        broker = BinanceBroker(
            api_key="k", api_secret="s", session=mock_session, market_type="futures"
        )
        order = broker.submit_order(
            ticker="BTC-USD", side="sell", order_type="market", qty=0.2
        )

        assert order.id == "556677"
        assert order.status == "FILLED"
        assert order.filled_price == 62000.5
        assert order.broker == "binance_testnet_futures"

        # Verify target endpoint was Futures
        called_url = mock_session.post.call_args[0][0]
        assert "/fapi/v1/order" in called_url

    def test_close_position(self):
        mock_session = MagicMock()
        mock_session.post.return_value = DummyResponse(
            200,
            {
                "symbol": "BTCUSDT",
                "orderId": 999111,
                "status": "FILLED",
                "executedQty": "0.1",
                "cummulativeQuoteQty": "6100.0",
            },
        )

        broker = BinanceBroker(api_key="k", api_secret="s", session=mock_session)
        order = broker.close_position(ticker="BTC-USD", side="sell", qty=0.1)

        assert order.id == "999111"
        assert order.status == "FILLED"
        assert order.filled_price == 61000.0

    def test_cancel_order(self):
        mock_session = MagicMock()
        mock_session.delete.return_value = DummyResponse(
            200,
            {
                "symbol": "BTCUSDT",
                "orderId": 12345,
                "status": "CANCELED",
                "origQty": "0.5",
                "side": "BUY",
                "type": "LIMIT",
            },
        )

        broker = BinanceBroker(api_key="k", api_secret="s", session=mock_session)
        res = broker.cancel_order(ticker="BTC-USD", broker_order_id="12345")

        assert res.id == "12345"
        assert res.status == "CANCELLED"

    def test_get_order_status(self):
        mock_session = MagicMock()
        mock_session.get.return_value = DummyResponse(
            200,
            {
                "symbol": "BTCUSDT",
                "orderId": 7788,
                "status": "FILLED",
                "executedQty": "1.0",
                "cummulativeQuoteQty": "60500.0",
                "side": "BUY",
                "type": "LIMIT",
            },
        )

        broker = BinanceBroker(api_key="k", api_secret="s", session=mock_session)
        res = broker.get_order_status(ticker="BTC-USD", broker_order_id="7788")

        assert res.id == "7788"
        assert res.status == "FILLED"
        assert res.filled_price == 60500.0


class TestBinanceAccountAndPrices:
    """Test account balance querying and current price."""

    def test_get_account_info_spot(self):
        mock_session = MagicMock()
        mock_session.get.return_value = DummyResponse(
            200,
            {
                "balances": [
                    {"asset": "USDT", "free": "5000.0", "locked": "500.0"},
                    {"asset": "BTC", "free": "0.0", "locked": "0.0"},
                ]
            },
        )

        broker = BinanceBroker(api_key="k", api_secret="s", session=mock_session, market_type="spot")
        acc = broker.get_account_info()

        assert isinstance(acc, AccountInfo)
        assert acc.balance == 5000.0
        assert acc.equity == 5500.0
        assert acc.currency == "USDT"

    def test_get_account_info_futures(self):
        mock_session = MagicMock()
        mock_session.get.return_value = DummyResponse(
            200,
            {
                "totalWalletBalance": "10000.0",
                "totalUnrealizedProfit": "350.5",
                "totalMarginBalance": "10350.5",
            },
        )

        broker = BinanceBroker(api_key="k", api_secret="s", session=mock_session, market_type="futures")
        acc = broker.get_account_info()

        assert acc.balance == 10000.0
        assert acc.equity == 10350.5
        assert acc.unrealised_pnl == 350.5

    def test_get_current_price(self):
        mock_session = MagicMock()
        mock_session.get.return_value = DummyResponse(200, {"symbol": "BTCUSDT", "price": "63450.25"})

        broker = BinanceBroker(session=mock_session)
        px = broker.get_current_price("BTC-USD")
        assert px == 63450.25


class TestBrokerFactory:
    """Test create_broker factory function and OMS integration."""

    def test_create_paper_broker(self):
        broker = create_broker("paper", initial_balance=5000.0)
        assert isinstance(broker, PaperBroker)
        assert broker.name == "paper"

    def test_create_binance_testnet_broker(self):
        broker = create_broker("binance_testnet", api_key="my_key", api_secret="my_sec")
        assert isinstance(broker, BinanceBroker)
        assert broker.testnet is True
        assert broker.market_type == "spot"

    def test_create_binance_futures_testnet_broker(self):
        broker = create_broker("binance_futures_testnet", api_key="k", api_secret="s")
        assert isinstance(broker, BinanceBroker)
        assert broker.testnet is True
        assert broker.market_type == "futures"

    def test_create_binance_live_broker(self):
        broker = create_broker("binance_live", api_key="k", api_secret="s", market_type="futures")
        assert isinstance(broker, BinanceBroker)
        assert broker.testnet is False
        assert broker.market_type == "futures"

    def test_set_and_get_broker(self):
        b = create_broker("paper", initial_balance=12345.0)
        set_broker(b)
        assert get_broker() is b
