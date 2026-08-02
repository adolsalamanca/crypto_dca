import logging
import time
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any


class ExchangeInterface(ABC):
    """Base class for exchange API clients."""

    _logger: logging.Logger | None = None

    def _log(self, level: int, msg: str) -> None:
        """Log a message if logger is configured."""
        if self._logger:
            self._logger.log(level, msg)

    def _get_timestamp(self) -> int:
        """Get current timestamp in milliseconds."""
        return int(time.time() * 1000)

    @abstractmethod
    def _sign(self, params: dict[str, Any]) -> str:
       pass

    @abstractmethod
    def _request(
            self,
            method: str,
            endpoint: str,
            params: dict[str, Any] | None = None,
            signed: bool = False,
    ) -> dict[str, Any]:
       pass

    @abstractmethod
    def get_exchange_info(self, symbol: str) -> dict[str, Any]:
        pass

    @abstractmethod
    def get_best_ask(self, symbol: str) -> Decimal:
        pass

    @abstractmethod
    def place_limit_order(
            self,
            symbol: str,
            side: str,
            quantity: Decimal,
            price: Decimal,
            time_in_force: str = "GTC",
    ) -> dict[str, Any]:
        pass

    @abstractmethod
    def get_order(self, symbol: str, order_id: int) -> dict[str, Any]:
        pass

    @abstractmethod
    def cancel_order(self, symbol: str, order_id: int) -> dict[str, Any]:
        pass