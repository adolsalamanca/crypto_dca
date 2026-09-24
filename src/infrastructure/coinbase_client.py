"""Coinbase Exchange API client with base64 HMAC SHA256 authentication.

Targets the Coinbase *Exchange* API (https://api.exchange.coinbase.com), not Advanced
Trade. Exchange is the one with a usable sandbox: it hosts a subset of the real order
books, so resting orders genuinely rest and fill. The Advanced Trade sandbox returns
static canned responses and serves no market data at all, which cannot drive this bot.

Sandbox:  https://api-public.sandbox.exchange.coinbase.com
Keys for the sandbox are issued separately at https://public.sandbox.exchange.coinbase.com
and are not interchangeable with production credentials.
"""

import base64
import hashlib
import hmac
import json
import logging
import time
from decimal import Decimal
from typing import Any

import requests

from src.domain.models import OrderSnapshot, OrderStatus, PlacedOrder, SymbolRules
from src.infrastructure.exchange import ExchangeInterface

SANDBOX_URL = "https://api-public.sandbox.exchange.coinbase.com"
PRODUCTION_URL = "https://api.exchange.coinbase.com"


class CoinbaseAPIError(Exception):
    """Raised when Coinbase API returns an error."""

    def __init__(self, status_code: int, code: int | None, msg: str):
        self.status_code = status_code
        self.code = code
        self.msg = msg
        super().__init__(f"Coinbase API error {status_code}: [{code}] {msg}")


class CoinbaseClient(ExchangeInterface):
    """Client for the Coinbase Exchange API."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        passphrase: str,
        base_url: str = PRODUCTION_URL,
        logger: logging.Logger | None = None,
    ):
        super().__init__(logger)
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    # ── transport ────────────────────────────────────────────────────────────

    def _sign(self, timestamp: str, method: str, path: str, body: str) -> str:
        """
        Coinbase signs `timestamp + method + requestPath + body`, HMAC-SHA256 with the
        base64-*decoded* secret, and returns the base64-encoded digest. Note this covers
        the body and path, which is why signing cannot be shared with Binance.
        """
        message = f"{timestamp}{method.upper()}{path}{body}".encode()
        key = base64.b64decode(self.api_secret)
        signature = hmac.new(key, message, hashlib.sha256).digest()
        return base64.b64encode(signature).decode()

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        signed: bool = False,
    ) -> Any:
        """Make an HTTP request to the Coinbase Exchange API."""
        url = f"{self.base_url}{path}"
        payload = json.dumps(body) if body else ""
        headers: dict[str, str] = {}

        if signed:
            timestamp = str(time.time())
            headers = {
                "CB-ACCESS-KEY": self.api_key,
                "CB-ACCESS-SIGN": self._sign(timestamp, method, path, payload),
                "CB-ACCESS-TIMESTAMP": timestamp,
                "CB-ACCESS-PASSPHRASE": self.passphrase,
            }

        self._log(logging.DEBUG, f"Request: {method} {path} body={body}")

        try:
            response = self.session.request(
                method,
                url,
                data=payload if payload else None,
                headers=headers,
                timeout=30,
            )
            data = response.json() if response.text else {}

            if not response.ok:
                msg = (
                    data.get("message", response.text)
                    if isinstance(data, dict)
                    else response.text
                )
                raise CoinbaseAPIError(response.status_code, None, msg)

            return data

        except requests.RequestException as e:
            raise CoinbaseAPIError(0, None, f"Network error: {e}") from e

    # ── domain operations ────────────────────────────────────────────────────

    def format_symbol(self, symbol: str) -> str:
        """
        Coinbase product ids are the canonical form already: BTC-EUR.

        Nothing to reconstruct, so nothing can be reconstructed wrongly.
        """
        return symbol.upper().replace("/", "-").replace("_", "-")

    def get_symbol_rules(self, symbol: str) -> SymbolRules:
        """
        Read a Coinbase product into venue-agnostic rules.

        `quote_increment` is the price tick, `base_increment` the quantity step. Coinbase
        publishes no per-order maximum, so `max_qty` is left unset. `min_market_funds` is
        documented for market orders; it is the closest published notional floor and is
        used conservatively here.
        """
        product = self.format_symbol(symbol)
        data = self._request("GET", f"/products/{product}")

        if data.get("trading_disabled") or data.get("status") != "online":
            raise CoinbaseAPIError(
                0, None, f"Product {product} is not tradable (status={data.get('status')})"
            )

        base_increment = Decimal(data["base_increment"])
        return SymbolRules(
            tick_size=Decimal(data["quote_increment"]),
            step_size=base_increment,
            min_qty=base_increment,
            max_qty=None,
            min_notional=Decimal(data.get("min_market_funds", "0")),
        )

    def get_best_ask(self, symbol: str) -> Decimal:
        """Get the current best ask price for a product."""
        product = self.format_symbol(symbol)
        data = self._request("GET", f"/products/{product}/ticker")
        ask = data.get("ask")

        if not ask:
            raise CoinbaseAPIError(404, None, f"No ask price found for {product}")

        return Decimal(ask)

    def place_limit_order(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        price: Decimal,
        time_in_force: str = "GTC",
    ) -> PlacedOrder:
        """Place a limit order."""
        product = self.format_symbol(symbol)
        body = {
            "type": "limit",
            "side": side.lower(),
            "product_id": product,
            "price": str(price),
            "size": str(quantity),
            "time_in_force": time_in_force.upper(),
        }

        self._log(
            logging.DEBUG,
            f"Placing {side} LIMIT order: {quantity} {product} @ {price} ({time_in_force})",
        )

        data = self._request("POST", "/orders", body, signed=True)
        return PlacedOrder(id=str(data["id"]), status=self._map_status(data))

    def get_order(self, symbol: str, order_id: str) -> OrderSnapshot:
        """Get order state by order ID. `symbol` is unused: Coinbase ids are global."""
        data = self._request("GET", f"/orders/{order_id}", signed=True)
        return OrderSnapshot(
            id=str(data["id"]),
            status=self._map_status(data),
            filled_qty=Decimal(data.get("filled_size", "0")),
        )

    def cancel_order(self, symbol: str, order_id: str) -> None:
        """Cancel a resting order. Tolerates an order that is already gone."""
        self._log(logging.INFO, f"Cancelling order {order_id} for {symbol}")
        try:
            self._request("DELETE", f"/orders/{order_id}", signed=True)
        except CoinbaseAPIError as e:
            if e.status_code == 404:
                self._log(logging.INFO, f"Order {order_id} already gone; nothing to cancel")
                return
            raise

    @staticmethod
    def _map_status(order: dict[str, Any]) -> OrderStatus:
        """
        Translate a Coinbase order payload into the domain enum.

        Coinbase has no PARTIALLY_FILLED status: a partial fill is an order still `open`
        with `filled_size > 0`. `pending` means received but not yet resting on the book —
        it is *earlier* than `open`, not a partial fill.

        A `done` order whose `done_reason` is a cancel can still carry `filled_size > 0`;
        that is a real purchase and is reported as PARTIALLY_FILLED so the caller settles
        it rather than discarding it.
        """
        status = str(order.get("status", "")).lower()
        filled = Decimal(str(order.get("filled_size", "0") or "0"))

        if status in ("pending", "received", "active"):
            return OrderStatus.NEW
        if status == "open":
            return OrderStatus.PARTIALLY_FILLED if filled > 0 else OrderStatus.NEW
        if status in ("done", "settled"):
            reason = str(order.get("done_reason", "")).lower()
            if reason == "filled":
                return OrderStatus.FILLED
            if filled > 0:
                return OrderStatus.PARTIALLY_FILLED
            return OrderStatus.CANCELLED
        if status == "rejected":
            return OrderStatus.FAILED

        return OrderStatus.FAILED
