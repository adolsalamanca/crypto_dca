"""DCA order execution with adaptive repricing."""

import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from src.binance_client import BinanceClient, round_down, round_to_tick

# Type alias for exchange filters
Filters = dict[str, Any]


@dataclass
class OrderConfig:
    """Configuration for a DCA order."""

    symbol: str
    spend_quote: Decimal
    price_multiplier: Decimal
    time_in_force: str
    poll_interval: int
    intervals_before_reprice: int
    max_reprices: int


@dataclass
class OrderResult:
    """Result of a DCA order execution."""

    success: bool
    filled: bool
    order_id: int | None = None
    quantity: Decimal | None = None
    price: Decimal | None = None
    message: str = ""


class DCAExecutor:
    """Executes DCA orders with monitoring and adaptive repricing."""

    def __init__(self, client: BinanceClient, logger: logging.Logger):
        self._client = client
        self._logger = logger

    def execute(self, config: OrderConfig, dry_run: bool = False) -> OrderResult:
        """
        Execute a DCA buy order.

        Fetches market data, places a limit order below the ask,
        monitors for fill, and reprices if the market moves away.
        """
        self._logger.info(f"Fetching exchange info for {config.symbol}...")
        filters = self._client.get_exchange_info(config.symbol)
        self._log_filters(filters)

        best_ask = self._client.get_best_ask(config.symbol)
        self._logger.info(f"Best ask: {best_ask}")

        limit_price = self._calculate_limit_price(
            best_ask, config.price_multiplier, filters
        )
        quantity = self._calculate_quantity(config.spend_quote, limit_price, filters)

        if error := self._validate_order(quantity, limit_price, filters):
            return OrderResult(success=False, filled=False, message=error)

        notional = quantity * limit_price
        self._logger.info(f"Order: {quantity} @ {limit_price} = {notional} notional")

        if dry_run:
            self._log_dry_run(
                config.symbol, quantity, limit_price, config.time_in_force
            )
            return OrderResult(
                success=True,
                filled=False,
                quantity=quantity,
                price=limit_price,
                message="Dry run - no order placed",
            )

        return self._place_and_monitor(config, quantity, limit_price, filters)

    def _calculate_limit_price(
        self, best_ask: Decimal, multiplier: Decimal, filters: Filters
    ) -> Decimal:
        """Calculate limit price from best ask."""
        raw_price = best_ask * multiplier
        limit_price = round_to_tick(raw_price, filters["tick_size"])
        self._logger.info(
            f"Limit price: {best_ask} * {multiplier} = {raw_price} -> {limit_price}"
        )
        return limit_price

    def _calculate_quantity(
        self, spend: Decimal, price: Decimal, filters: Filters
    ) -> Decimal:
        """Calculate order quantity from spend amount."""
        raw_qty = spend / price
        quantity = round_down(raw_qty, filters["step_size"])
        self._logger.info(f"Quantity: {spend} / {price} = {raw_qty} -> {quantity}")
        return quantity

    def _validate_order(
        self, quantity: Decimal, price: Decimal, filters: Filters
    ) -> str | None:
        """Validate order against exchange filters. Returns error message or None."""
        if quantity < filters["min_qty"]:
            return f"Quantity {quantity} below min {filters['min_qty']}"

        if quantity > filters["max_qty"]:
            return f"Quantity {quantity} exceeds max {filters['max_qty']}"

        notional = quantity * price
        if notional < filters["min_notional"]:
            return (
                f"Notional {notional} below min {filters['min_notional']}. "
                f"Increase --spend-eur."
            )

        return None

    def _place_and_monitor(
        self,
        config: OrderConfig,
        quantity: Decimal,
        limit_price: Decimal,
        filters: Filters,
    ) -> OrderResult:
        """Place order and monitor until filled or give up."""
        self._logger.info("Placing limit order...")
        response = self._client.place_limit_order(
            symbol=config.symbol,
            side="BUY",
            quantity=quantity,
            price=limit_price,
            time_in_force=config.time_in_force,
        )

        order_id: int = response["orderId"]
        status = response.get("status")
        self._logger.info(f"Order placed: id={order_id}, status={status}")

        if status == "FILLED":
            return OrderResult(
                success=True,
                filled=True,
                order_id=order_id,
                quantity=quantity,
                price=limit_price,
                message="Filled immediately",
            )

        return self._monitor_order(config, order_id, quantity, limit_price, filters)

    def _monitor_order(
        self,
        config: OrderConfig,
        order_id: int,
        quantity: Decimal,
        limit_price: Decimal,
        filters: Filters,
    ) -> OrderResult:
        """Monitor order and reprice if market moves away."""
        current_order_id = order_id
        current_price = limit_price
        reprice_count = 0
        intervals_above = 0
        check_num = 0

        self._logger.info(
            f"Monitoring (poll={config.poll_interval}s, "
            f"reprice after {config.intervals_before_reprice}, "
            f"max {config.max_reprices} reprices)"
        )
        self._logger.info("-" * 70)

        while True:
            time.sleep(config.poll_interval)
            check_num += 1

            order_status = self._client.get_order(config.symbol, current_order_id)
            status = order_status.get("status")
            current_ask = self._client.get_best_ask(config.symbol)

            if status == "FILLED":
                self._logger.info(f"[{check_num}] FILLED")
                return OrderResult(
                    success=True,
                    filled=True,
                    order_id=current_order_id,
                    quantity=quantity,
                    price=current_price,
                    message="Order filled",
                )

            if status not in ("NEW", "PARTIALLY_FILLED"):
                self._logger.warning(f"[{check_num}] Unexpected status: {status}")
                return OrderResult(
                    success=False,
                    filled=False,
                    order_id=current_order_id,
                    message=f"Unexpected status: {status}",
                )

            if current_ask > current_price:
                intervals_above += 1
                self._log_check(
                    check_num,
                    status,
                    current_price,
                    current_ask,
                    intervals_above,
                    config,
                )

                if intervals_above >= config.intervals_before_reprice:
                    if reprice_count >= config.max_reprices:
                        self._logger.info(
                            f"Max reprices ({config.max_reprices}) reached, giving up"
                        )
                        self._client.cancel_order(config.symbol, current_order_id)
                        return OrderResult(
                            success=True,
                            filled=False,
                            order_id=current_order_id,
                            message="Max reprices reached",
                        )

                    current_order_id, current_price = self._reprice_order(
                        config, current_order_id, quantity, current_ask, filters
                    )
                    reprice_count += 1
                    intervals_above = 0
                    self._logger.info(
                        f"New order {current_order_id} @ {current_price} "
                        f"(reprice {reprice_count}/{config.max_reprices})"
                    )
            else:
                reset = intervals_above > 0
                intervals_above = 0
                self._log_check(
                    check_num, status, current_price, current_ask, 0, config, reset
                )

    def _reprice_order(
        self,
        config: OrderConfig,
        old_order_id: int,
        quantity: Decimal,
        current_ask: Decimal,
        filters: Filters,
    ) -> tuple[int, Decimal]:
        """Cancel old order and place new one at current price."""
        self._client.cancel_order(config.symbol, old_order_id)

        new_price = round_to_tick(
            current_ask * config.price_multiplier, filters["tick_size"]
        )
        response = self._client.place_limit_order(
            symbol=config.symbol,
            side="BUY",
            quantity=quantity,
            price=new_price,
            time_in_force=config.time_in_force,
        )

        return response["orderId"], new_price

    def _log_filters(self, filters: Filters) -> None:
        """Log exchange filters."""
        self._logger.info(
            f"Filters: tick={filters['tick_size']}, "
            f"step={filters['step_size']}, "
            f"min_notional={filters['min_notional']}"
        )

    def _log_dry_run(
        self, symbol: str, quantity: Decimal, price: Decimal, tif: str
    ) -> None:
        """Log dry run order details."""
        self._logger.info("=" * 60)
        self._logger.info("DRY RUN - would place:")
        self._logger.info(f"  {symbol} BUY LIMIT {quantity} @ {price} ({tif})")
        self._logger.info(f"  Notional: {quantity * price}")
        self._logger.info("=" * 60)

    def _log_check(
        self,
        check_num: int,
        status: str | None,
        limit: Decimal,
        ask: Decimal,
        intervals_above: int,
        config: OrderConfig,
        reset: bool = False,
    ) -> None:
        """Log a status check."""
        if intervals_above > 0:
            suffix = f"Above ({intervals_above}/{config.intervals_before_reprice})"
            if intervals_above >= config.intervals_before_reprice:
                suffix += " -> Repricing"
        elif reset:
            suffix = "OK (reset)"
        else:
            suffix = "OK"

        self._logger.info(
            f"[{check_num}] {status} | Limit: {limit} | Ask: {ask} | {suffix}"
        )
