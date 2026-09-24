"""Binance Spot API client with HMAC SHA256 authentication."""

import hashlib
import hmac
import logging
import re
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import requests

from src.domain.models import OrderSnapshot, OrderStatus, PlacedOrder, SymbolRules
from src.infrastructure.exchange import ExchangeInterface

# Binance's own vocabulary, mapped into the domain. Anything unlisted is treated as a
# failure rather than silently assumed to be resting.
_STATUS_MAP: dict[str, OrderStatus] = {
    "NEW": OrderStatus.NEW,
    "PENDING_NEW": OrderStatus.NEW,
    "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
    "FILLED": OrderStatus.FILLED,
    "CANCELED": OrderStatus.CANCELLED,
    "PENDING_CANCEL": OrderStatus.CANCELLED,
    "EXPIRED": OrderStatus.CANCELLED,
    "REJECTED": OrderStatus.FAILED,
    "EXPIRED_IN_MATCH": OrderStatus.FAILED,
}

# Binance error code for an order that no longer exists (already filled or cancelled).
_UNKNOWN_ORDER = -2011


class BinanceAPIError(Exception):
    """Raised when Binance API returns an error."""

    def __init__(self, status_code: int, code: int | None, msg: str):
        self.status_code = status_code
        self.code = code
        self.msg = msg
        super().__init__(f"Binance API error {status_code}: [{code}] {msg}")


class BinanceClient(ExchangeInterface):
    """Client for Binance Spot API with signed request support."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str = "https://api.binance.com",
        recv_window: int = 5000,
        logger: logging.Logger | None = None,
    ):
        super().__init__(logger)
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.recv_window = recv_window
        self.session = requests.Session()
        self.session.headers.update({"X-MBX-APIKEY": self.api_key})

    # ── transport ────────────────────────────────────────────────────────────

    def _sign(self, params: dict[str, Any]) -> str:
        """Generate HMAC SHA256 signature for request parameters."""
        query_string = urlencode(params)
        return hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        signed: bool = False,
    ) -> Any:
        """Make HTTP request to Binance API."""
        url = f"{self.base_url}{endpoint}"
        params = params or {}

        if signed:
            params["timestamp"] = self._get_timestamp_ms()
            params["recvWindow"] = self.recv_window
            params["signature"] = self._sign(params)

        safe_params = {k: v for k, v in params.items() if k != "signature"}
        self._log(logging.DEBUG, f"Request: {method} {endpoint} params={safe_params}")

        try:
            if method == "GET":
                response = self.session.get(url, params=params, timeout=30)
            elif method == "POST":
                response = self.session.post(url, params=params, timeout=30)
            elif method == "DELETE":
                response = self.session.delete(url, params=params, timeout=30)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            data = response.json() if response.text else {}

            if response.status_code != 200:
                error_code = data.get("code") if isinstance(data, dict) else None
                error_msg = (
                    data.get("msg", response.text)
                    if isinstance(data, dict)
                    else response.text
                )
                raise BinanceAPIError(response.status_code, error_code, error_msg)

            return data

        except requests.RequestException as e:
            raise BinanceAPIError(0, None, f"Network error: {e}") from e

    # ── domain operations ────────────────────────────────────────────────────

    def format_symbol(self, symbol: str) -> str:
        """Binance uses an unseparated pair: BTC-EUR -> BTCEUR."""
        return re.sub(r"[-/_]", "", symbol.upper())

    def get_symbol_rules(self, symbol: str) -> SymbolRules:
        """Read PRICE_FILTER / LOT_SIZE / NOTIONAL into venue-agnostic rules."""
        pair = self.format_symbol(symbol)
        data = self._request("GET", "/api/v3/exchangeInfo", {"symbol": pair})

        for s in data.get("symbols", []):
            if s["symbol"] == pair:
                filters = {f["filterType"]: f for f in s["filters"]}
                price_filter = filters.get("PRICE_FILTER", {})
                lot_size = filters.get("LOT_SIZE", {})
                notional = filters.get("NOTIONAL", filters.get("MIN_NOTIONAL", {}))

                return SymbolRules(
                    tick_size=Decimal(price_filter.get("tickSize", "0.01")),
                    step_size=Decimal(lot_size.get("stepSize", "0.00001")),
                    min_qty=Decimal(lot_size.get("minQty", "0")),
                    max_qty=Decimal(lot_size.get("maxQty", "9999999")),
                    min_notional=Decimal(notional.get("minNotional", "10")),
                )

        raise BinanceAPIError(404, None, f"Symbol {pair} not found in exchange info")

    def get_best_ask(self, symbol: str) -> Decimal:
        """Get the current best ask price for a symbol."""
        pair = self.format_symbol(symbol)
        data = self._request("GET", "/api/v3/ticker/bookTicker", {"symbol": pair})
        ask_price = data.get("askPrice")

        if not ask_price:
            raise BinanceAPIError(404, None, f"No ask price found for {pair}")

        return Decimal(ask_price)

    def place_limit_order(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        price: Decimal,
        time_in_force: str = "GTC",
    ) -> PlacedOrder:
        """Place a limit order."""
        pair = self.format_symbol(symbol)
        params = {
            "symbol": pair,
            "side": side.upper(),
            "type": "LIMIT",
            "timeInForce": time_in_force,
            "quantity": str(quantity),
            "price": str(price),
        }

        self._log(
            logging.DEBUG,
            f"Placing {side} LIMIT order: {quantity} {pair} @ {price} ({time_in_force})",
        )

        data = self._request("POST", "/api/v3/order", params, signed=True)
        return PlacedOrder(
            id=str(data["orderId"]),
            status=self._map_status(data.get("status")),
        )

    def get_order(self, symbol: str, order_id: str) -> OrderSnapshot:
        """Get order status by order ID."""
        params = {"symbol": self.format_symbol(symbol), "orderId": order_id}
        data = self._request("GET", "/api/v3/order", params, signed=True)
        return OrderSnapshot(
            id=str(data["orderId"]),
            status=self._map_status(data.get("status")),
            filled_qty=Decimal(data.get("executedQty", "0")),
        )

    def cancel_order(self, symbol: str, order_id: str) -> None:
        """Cancel an open order. Tolerates an order that is already gone."""
        params = {"symbol": self.format_symbol(symbol), "orderId": order_id}
        self._log(logging.INFO, f"Cancelling order {order_id} for {symbol}")
        try:
            self._request("DELETE", "/api/v3/order", params, signed=True)
        except BinanceAPIError as e:
            if e.code == _UNKNOWN_ORDER:
                self._log(logging.INFO, f"Order {order_id} already gone; nothing to cancel")
                return
            raise

    @staticmethod
    def _map_status(raw: str | None) -> OrderStatus:
        """Translate a Binance status string into the domain enum."""
        return _STATUS_MAP.get(raw or "", OrderStatus.FAILED)
