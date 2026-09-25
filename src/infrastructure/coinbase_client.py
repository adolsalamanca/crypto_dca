"""Coinbase Advanced Trade (CDP) API client with JWT authentication.

Targets the Advanced Trade REST API at https://api.coinbase.com/api/v3/brokerage.

This is *not* the Coinbase Exchange API. Exchange keys are a key/secret/passphrase triple
signed with base64 HMAC; CDP keys are a key id plus an Ed25519 or ECDSA private key, and
every request carries a short-lived JWT bound to its own method and path. Retail accounts
can only issue CDP keys, which is why this client targets Advanced Trade.

There is no useful sandbox: the Advanced Trade sandbox returns canned responses and
serves no market data, so it cannot drive this bot. Verification means a small real order.
"""

import base64
import json
import logging
import secrets
import time
import uuid
from decimal import Decimal
from typing import Any

import jwt
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.domain.models import OrderSnapshot, OrderStatus, PlacedOrder, SymbolRules
from src.infrastructure.exchange import ExchangeInterface

API_HOST = "api.coinbase.com"
PRODUCTION_URL = f"https://{API_HOST}"
API_PREFIX = "/api/v3/brokerage"

JWT_TTL_SECS = 120

# batch_cancel reasons that mean "it is already gone", not "the cancel failed".
_ALREADY_GONE = {
    "UNKNOWN_CANCEL_ORDER",
    "INVALID_CANCEL_REQUEST",
    "DUPLICATE_CANCEL_REQUEST",
}


class CoinbaseAPIError(Exception):
    """Raised when the Coinbase API returns an error."""

    def __init__(self, status_code: int, code: str | None, msg: str):
        self.status_code = status_code
        self.code = code
        self.msg = msg
        super().__init__(f"Coinbase API error {status_code}: [{code}] {msg}")


def _load_signing_key(api_secret: str) -> tuple[Any, str]:
    """
    Return (key, JWT algorithm) for either CDP key type.

    Ed25519 secrets arrive base64-encoded, 64 bytes of seed||public_key, of which only the
    first 32 are the seed. ECDSA secrets arrive as a PEM block and are used as-is.
    """
    if "BEGIN" in api_secret:
        key = serialization.load_pem_private_key(api_secret.encode(), password=None)
        return key, "ES256"

    raw = base64.b64decode(api_secret)
    if len(raw) not in (32, 64):
        raise ValueError(
            f"Unrecognised COINBASE_API_SECRET: expected a PEM block or a base64 Ed25519 "
            f"key of 32 or 64 bytes, got {len(raw)} bytes"
        )
    return Ed25519PrivateKey.from_private_bytes(raw[:32]), "EdDSA"


class CoinbaseClient(ExchangeInterface):
    """Client for the Coinbase Advanced Trade API."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str = PRODUCTION_URL,
        logger: logging.Logger | None = None,
    ):
        super().__init__(logger)
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._host = self.base_url.split("://", 1)[-1]
        self._signing_key, self._algorithm = _load_signing_key(api_secret)
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    # ── transport ────────────────────────────────────────────────────────────

    def _jwt(self, method: str, path: str) -> str:
        """
        Mint a JWT for exactly one request.

        The `uri` claim binds the token to this method, host and path, so a token cannot
        be replayed against a different endpoint. Query strings are excluded from the
        claim; including them is rejected as a signature mismatch.
        """
        now = int(time.time())
        return jwt.encode(
            {
                "sub": self.api_key,
                "iss": "cdp",
                "nbf": now,
                "exp": now + JWT_TTL_SECS,
                "uri": f"{method.upper()} {self._host}{path}",
            },
            self._signing_key,
            algorithm=self._algorithm,
            headers={"kid": self.api_key, "nonce": secrets.token_hex(16)},
        )

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Make an authenticated request. Every Advanced Trade endpoint requires auth."""
        payload = json.dumps(body) if body is not None else ""
        headers = {"Authorization": f"Bearer {self._jwt(method, path)}"}

        self._log(logging.DEBUG, f"Request: {method} {path} body={body} params={params}")

        try:
            response = self.session.request(
                method,
                f"{self.base_url}{path}",
                data=payload or None,
                params=params,
                headers=headers,
                timeout=30,
            )
        except requests.RequestException as e:
            raise CoinbaseAPIError(0, None, f"Network error: {e}") from e

        # Auth failures and edge-proxy blocks come back as HTML, so a decode failure must
        # not be reported as a transport error: the status code is the useful part.
        try:
            data = response.json() if response.text else {}
        except ValueError:
            data = None

        if not isinstance(data, dict):
            if response.ok:
                raise CoinbaseAPIError(
                    response.status_code, None, f"Malformed response: {response.text[:200]}"
                )
            raise CoinbaseAPIError(response.status_code, None, response.text[:200])

        if not response.ok:
            msg = data.get("message") or data.get("error_details") or response.text
            raise CoinbaseAPIError(response.status_code, data.get("error"), str(msg))

        return data

    # ── domain operations ────────────────────────────────────────────────────

    def format_symbol(self, symbol: str) -> str:
        """Coinbase product ids are the canonical form already: BTC-EUR."""
        return symbol.upper().replace("/", "-").replace("_", "-")

    def get_symbol_rules(self, symbol: str) -> SymbolRules:
        """
        Read a Coinbase product into venue-agnostic rules.

        `quote_increment` is the price tick, `base_increment` the quantity step, and
        `quote_min_size` the notional floor.
        """
        product = self.format_symbol(symbol)
        data = self._request("GET", f"{API_PREFIX}/products/{product}")

        if data.get("trading_disabled") or data.get("status") != "online":
            raise CoinbaseAPIError(
                0, None, f"Product {product} is not tradable (status={data.get('status')})"
            )

        max_qty = data.get("base_max_size")
        return SymbolRules(
            tick_size=Decimal(data["quote_increment"]),
            step_size=Decimal(data["base_increment"]),
            min_qty=Decimal(data.get("base_min_size") or data["base_increment"]),
            max_qty=Decimal(max_qty) if max_qty else None,
            min_notional=Decimal(data.get("quote_min_size") or "0"),
        )

    def get_best_ask(self, symbol: str) -> Decimal:
        """Get the current best ask price for a product."""
        product = self.format_symbol(symbol)
        data = self._request(
            "GET", f"{API_PREFIX}/best_bid_ask", params={"product_ids": product}
        )

        books = data.get("pricebooks") or []
        asks = books[0].get("asks") if books else None
        if not asks:
            raise CoinbaseAPIError(404, None, f"No ask price found for {product}")

        return Decimal(asks[0]["price"])

    def place_limit_order(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        price: Decimal,
        time_in_force: str = "GTC",
    ) -> PlacedOrder:
        """Place a limit order. Only GTC is supported."""
        if time_in_force.upper() != "GTC":
            raise CoinbaseAPIError(
                0, None, f"Advanced Trade limit orders here are GTC only, got {time_in_force}"
            )

        product = self.format_symbol(symbol)
        body = {
            "client_order_id": str(uuid.uuid4()),
            "product_id": product,
            "side": side.upper(),
            "order_configuration": {
                "limit_limit_gtc": {
                    "base_size": str(quantity),
                    "limit_price": str(price),
                    "post_only": False,
                }
            },
        }

        self._log(
            logging.DEBUG,
            f"Placing {side} LIMIT order: {quantity} {product} @ {price} ({time_in_force})",
        )

        data = self._request("POST", f"{API_PREFIX}/orders", body)

        # Advanced Trade reports rejection in a 200 body, so success must be checked.
        if not data.get("success"):
            err = data.get("error_response") or {}
            raise CoinbaseAPIError(
                0,
                err.get("new_order_failure_reason"),
                err.get("error_details") or err.get("message") or "Order rejected",
            )

        return PlacedOrder(id=str(data["success_response"]["order_id"]), status=OrderStatus.NEW)

    def get_order(self, symbol: str, order_id: str) -> OrderSnapshot:
        """Get order state by order ID. `symbol` is unused: Coinbase ids are global."""
        data = self._request("GET", f"{API_PREFIX}/orders/historical/{order_id}")
        order = data.get("order") or {}
        return OrderSnapshot(
            id=str(order.get("order_id", order_id)),
            status=self._map_status(order),
            filled_qty=Decimal(str(order.get("filled_size") or "0")),
        )

    def cancel_order(self, symbol: str, order_id: str) -> None:
        """Cancel a resting order. Tolerates an order that is already gone."""
        self._log(logging.INFO, f"Cancelling order {order_id} for {symbol}")
        data = self._request(
            "POST", f"{API_PREFIX}/orders/batch_cancel", {"order_ids": [order_id]}
        )

        results = data.get("results") or []
        if not results or results[0].get("success"):
            return

        reason = str(results[0].get("failure_reason", ""))
        if reason in _ALREADY_GONE:
            self._log(logging.INFO, f"Order {order_id} already gone; nothing to cancel")
            return

        raise CoinbaseAPIError(0, reason, f"Could not cancel order {order_id}")

    @staticmethod
    def _map_status(order: dict[str, Any]) -> OrderStatus:
        """
        Translate an Advanced Trade order into the domain enum.

        Advanced Trade has no PARTIALLY_FILLED status: a partial fill is an order still
        OPEN with `filled_size > 0`. A CANCELLED or EXPIRED order can also carry
        `filled_size > 0` — that is a real purchase, reported as PARTIALLY_FILLED so the
        caller settles it rather than discarding it.
        """
        status = str(order.get("status", "")).upper()
        filled = Decimal(str(order.get("filled_size") or "0"))

        if status == "FILLED":
            return OrderStatus.FILLED
        if status in ("PENDING", "QUEUED", "OPEN", "CANCEL_QUEUED", "EDIT_QUEUED"):
            return OrderStatus.PARTIALLY_FILLED if filled > 0 else OrderStatus.NEW
        if status in ("CANCELLED", "EXPIRED"):
            return OrderStatus.PARTIALLY_FILLED if filled > 0 else OrderStatus.CANCELLED

        return OrderStatus.FAILED
