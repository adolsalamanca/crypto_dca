"""Binance Spot API client with HMAC SHA256 authentication."""

import hashlib
import hmac
import logging
import time
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import requests


class BinanceAPIError(Exception):
    """Raised when Binance API returns an error."""

    def __init__(self, status_code: int, code: int | None, msg: str):
        self.status_code = status_code
        self.code = code
        self.msg = msg
        super().__init__(f"Binance API error {status_code}: [{code}] {msg}")


class BinanceClient:
    """Client for Binance Spot API with signed request support."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str = "https://api.binance.com",
        recv_window: int = 5000,
        logger: logging.Logger | None = None,
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.recv_window = recv_window
        self._logger = logger
        self.session = requests.Session()
        self.session.headers.update({"X-MBX-APIKEY": self.api_key})

    def _log(self, level: int, msg: str) -> None:
        """Log a message if logger is configured."""
        if self._logger:
            self._logger.log(level, msg)

    def _get_timestamp(self) -> int:
        """Get current timestamp in milliseconds."""
        return int(time.time() * 1000)

    def _sign(self, params: dict[str, Any]) -> str:
        """Generate HMAC SHA256 signature for request parameters."""
        query_string = urlencode(params)
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return signature

    def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        signed: bool = False,
    ) -> dict[str, Any]:
        """Make HTTP request to Binance API."""
        url = f"{self.base_url}{endpoint}"
        params = params or {}

        if signed:
            params["timestamp"] = self._get_timestamp()
            params["recvWindow"] = self.recv_window
            params["signature"] = self._sign(params)

        # Log request without sensitive data
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
                error_code = data.get("code")
                error_msg = data.get("msg", response.text)
                raise BinanceAPIError(response.status_code, error_code, error_msg)

            return data

        except requests.RequestException as e:
            raise BinanceAPIError(0, None, f"Network error: {e}") from e

    def get_exchange_info(self, symbol: str) -> dict[str, Any]:
        """
        Get exchange info and filters for a symbol.

        Returns dict with:
            - tick_size: Decimal (price precision)
            - step_size: Decimal (quantity precision)
            - min_notional: Decimal (minimum order value)
            - min_qty: Decimal (minimum quantity)
            - max_qty: Decimal (maximum quantity)
        """
        data = self._request("GET", "/api/v3/exchangeInfo", {"symbol": symbol})

        for s in data.get("symbols", []):
            if s["symbol"] == symbol:
                filters = {f["filterType"]: f for f in s["filters"]}

                price_filter = filters.get("PRICE_FILTER", {})
                lot_size = filters.get("LOT_SIZE", {})
                notional = filters.get("NOTIONAL", filters.get("MIN_NOTIONAL", {}))

                return {
                    "tick_size": Decimal(price_filter.get("tickSize", "0.01")),
                    "step_size": Decimal(lot_size.get("stepSize", "0.00001")),
                    "min_notional": Decimal(notional.get("minNotional", "10")),
                    "min_qty": Decimal(lot_size.get("minQty", "0")),
                    "max_qty": Decimal(lot_size.get("maxQty", "9999999")),
                }

        raise BinanceAPIError(404, None, f"Symbol {symbol} not found in exchange info")

    def get_best_ask(self, symbol: str) -> Decimal:
        """Get the current best ask price for a symbol."""
        data = self._request("GET", "/api/v3/ticker/bookTicker", {"symbol": symbol})
        ask_price = data.get("askPrice")

        if not ask_price:
            raise BinanceAPIError(404, None, f"No ask price found for {symbol}")

        return Decimal(ask_price)

    def place_limit_order(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        price: Decimal,
        time_in_force: str = "GTC",
    ) -> dict[str, Any]:
        """
        Place a limit order.

        Args:
            symbol: Trading pair (e.g., BTCEUR)
            side: BUY or SELL
            quantity: Amount to buy/sell
            price: Limit price
            time_in_force: GTC, IOC, or FOK

        Returns:
            Order response from Binance
        """
        params = {
            "symbol": symbol,
            "side": side,
            "type": "LIMIT",
            "timeInForce": time_in_force,
            "quantity": str(quantity),
            "price": str(price),
        }

        self._log(
            logging.DEBUG,
            f"Placing {side} LIMIT order: {quantity} {symbol} @ {price} ({time_in_force})",
        )

        return self._request("POST", "/api/v3/order", params, signed=True)

    def get_order(self, symbol: str, order_id: int) -> dict[str, Any]:
        """Get order status by order ID."""
        params = {"symbol": symbol, "orderId": order_id}
        return self._request("GET", "/api/v3/order", params, signed=True)

    def cancel_order(self, symbol: str, order_id: int) -> dict[str, Any]:
        """Cancel an open order."""
        params = {"symbol": symbol, "orderId": order_id}
        self._log(logging.INFO, f"Cancelling order {order_id} for {symbol}")
        return self._request("DELETE", "/api/v3/order", params, signed=True)
