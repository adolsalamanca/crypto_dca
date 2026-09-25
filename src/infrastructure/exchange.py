"""Exchange client contract.

The interface exposes only domain operations. Transport concerns — how a request is
signed, which headers carry credentials, whether the payload is a query string or a JSON
body — are private to each implementation, because they genuinely differ: Binance signs a
urlencoded query string to hex with a shared secret, Coinbase mints a per-request JWT
signed with an Ed25519 private key. There is no shared signature worth declaring.
"""

import logging
import time
from abc import ABC, abstractmethod
from decimal import Decimal

from src.domain.models import OrderSnapshot, PlacedOrder, SymbolRules


class ExchangeInterface(ABC):
    """Base class for exchange API clients."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger

    def _log(self, level: int, msg: str) -> None:
        """Log a message if logger is configured."""
        if self._logger:
            self._logger.log(level, msg)

    def _get_timestamp_ms(self) -> int:
        """Current timestamp in milliseconds."""
        return int(time.time() * 1000)

    @abstractmethod
    def format_symbol(self, symbol: str) -> str:
        """
        Render a canonical symbol (e.g. 'BTCEUR') in this venue's notation.

        Canonical form is what the application stores and queries by; each venue formats
        it on the way out, so the persisted representation never depends on the broker.
        """

    @abstractmethod
    def get_symbol_rules(self, symbol: str) -> SymbolRules:
        """Fetch the venue's trading constraints for a symbol."""

    @abstractmethod
    def get_best_ask(self, symbol: str) -> Decimal:
        """Lowest price a seller is currently willing to accept."""

    @abstractmethod
    def place_limit_order(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        price: Decimal,
        time_in_force: str = "GTC",
    ) -> PlacedOrder:
        """Submit a limit order."""

    @abstractmethod
    def get_order(self, symbol: str, order_id: str) -> OrderSnapshot:
        """Current state of a previously placed order."""

    @abstractmethod
    def cancel_order(self, symbol: str, order_id: str) -> None:
        """Cancel a resting order. Must not raise if the order is already gone."""
