"""DCA order execution with adaptive repricing."""

import logging
import time
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from src.domain.models import OrderSnapshot, OrderStatus, SymbolRules
from src.infrastructure.exchange import ExchangeInterface


def round_step(value: Decimal, step: Decimal) -> Decimal:
    """Round a value down to the nearest step size."""
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


# Progressive multipliers for repricing (more aggressive each time)
REPRICE_MULTIPLIERS = (Decimal("0.9991"), Decimal("0.9993"), Decimal("0.9996"))


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
    """
    Result of a DCA order execution.

    `quantity` is the size the order was placed at; `filled_quantity` is what the venue
    actually executed. `status` uses the persisted vocabulary the database CHECK
    constraint permits: PENDING, FILLED, PARTIALLY_FILLED, CANCELLED, FAILED.
    """

    success: bool
    filled: bool
    order_id: str | None = None
    quantity: Decimal | None = None
    filled_quantity: Decimal = Decimal(0)
    price: Decimal | None = None
    message: str = ""
    reprices: int = 0
    status: str = "PENDING"
    partial: bool = False


class DCAExecutor:
    """Executes DCA orders with monitoring and adaptive repricing."""

    def __init__(self, client: ExchangeInterface, logger: logging.Logger):
        self._client = client
        self._logger = logger

    def execute(self, config: OrderConfig, dry_run: bool = False) -> OrderResult:
        """
        Execute a DCA buy order.

        Fetches market data, places a limit order below the ask,
        monitors for fill, and reprices if the market moves away.
        """
        self._logger.info(f"Fetching symbol rules for {config.symbol}...")
        rules = self._client.get_symbol_rules(config.symbol)
        self._log_rules(rules)

        best_ask = self._client.get_best_ask(config.symbol)
        self._logger.info(f"Best ask: {best_ask}")

        limit_price = self._calculate_limit_price(
            best_ask, config.price_multiplier, rules
        )
        quantity = self._calculate_quantity(config.spend_quote, limit_price, rules)

        if error := self._validate_order(quantity, limit_price, rules):
            return OrderResult(
                success=False,
                filled=False,
                quantity=quantity,
                price=limit_price,
                message=error,
                status="FAILED",
            )

        notional = quantity * limit_price
        self._logger.debug(f"Order: {quantity} @ {limit_price} = {notional} notional")

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

        return self._place_and_monitor(config, quantity, limit_price, rules)

    def _calculate_limit_price(
        self, best_ask: Decimal, multiplier: Decimal, rules: SymbolRules
    ) -> Decimal:
        """Calculate limit price from best ask."""
        raw_price = best_ask * multiplier
        limit_price = round_step(raw_price, rules.tick_size)
        self._logger.info(
            f"Limit price: {best_ask} * {multiplier} = {raw_price} -> {limit_price}"
        )
        return limit_price

    def _calculate_quantity(
        self, spend: Decimal, price: Decimal, rules: SymbolRules
    ) -> Decimal:
        """Calculate order quantity from spend amount."""
        raw_qty = spend / price
        quantity = round_step(raw_qty, rules.step_size)
        self._logger.debug(f"Quantity: {spend} / {price} = {raw_qty} -> {quantity}")
        return quantity

    def _validate_order(
        self, quantity: Decimal, price: Decimal, rules: SymbolRules
    ) -> str | None:
        """Validate order against venue rules. Returns error message or None."""
        if quantity < rules.min_qty:
            return f"Quantity {quantity} below min {rules.min_qty}"

        if rules.max_qty is not None and quantity > rules.max_qty:
            return f"Quantity {quantity} exceeds max {rules.max_qty}"

        notional = quantity * price
        if notional < rules.min_notional:
            return (
                f"Notional {notional} below min {rules.min_notional}. "
                f"Increase --spend-eur."
            )

        return None

    def _place_and_monitor(
        self,
        config: OrderConfig,
        quantity: Decimal,
        limit_price: Decimal,
        rules: SymbolRules,
    ) -> OrderResult:
        """Place order and monitor until filled or give up."""
        self._logger.info("Placing limit order...")
        placed = self._client.place_limit_order(
            symbol=config.symbol,
            side="BUY",
            quantity=quantity,
            price=limit_price,
            time_in_force=config.time_in_force,
        )

        self._logger.info(f"Order placed: id={placed.id}, status={placed.status}")

        if placed.status is OrderStatus.FILLED:
            return OrderResult(
                success=True,
                filled=True,
                order_id=placed.id,
                quantity=quantity,
                price=limit_price,
                message="Filled immediately",
                reprices=0,
                status="FILLED",
                filled_quantity=quantity,
            )

        return self._monitor_order(config, placed.id, quantity, limit_price, rules)

    def _monitor_order(
        self,
        config: OrderConfig,
        order_id: str,
        quantity: Decimal,
        limit_price: Decimal,
        rules: SymbolRules,
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

            snapshot = self._client.get_order(config.symbol, current_order_id)
            current_ask = self._client.get_best_ask(config.symbol)

            if snapshot.status is OrderStatus.FILLED:
                self._logger.info(f"[{check_num}] FILLED")
                return OrderResult(
                    success=True,
                    filled=True,
                    order_id=current_order_id,
                    quantity=quantity,
                    price=current_price,
                    message="Order filled",
                    reprices=reprice_count,
                    status="FILLED",
                    filled_quantity=quantity,
                )

            # Terminal states reached without us asking: the venue cancelled or rejected
            # the order. A cancel can still carry a partial fill, which is a real purchase.
            if snapshot.status in (OrderStatus.CANCELLED, OrderStatus.FAILED):
                if snapshot.filled_qty > 0:
                    return self._settle_partial(
                        current_order_id,
                        quantity,
                        snapshot.filled_qty,
                        current_price,
                        reprice_count,
                        "Order ended early with a partial fill",
                    )
                self._logger.warning(f"[{check_num}] Unexpected status: {snapshot.status}")
                return OrderResult(
                    success=False,
                    filled=False,
                    order_id=current_order_id,
                    quantity=quantity,
                    price=current_price,
                    message=f"Unexpected status: {snapshot.status}",
                    reprices=reprice_count,
                    status="FAILED",
                )

            if current_ask > current_price:
                intervals_above += 1
                self._log_check(
                    check_num,
                    snapshot.status,
                    current_price,
                    current_ask,
                    intervals_above,
                    config,
                )

                if intervals_above >= config.intervals_before_reprice:
                    # Never reprice a partially filled order: cancelling it to chase the
                    # market would leave the executed portion stranded and unrecorded.
                    if snapshot.filled_qty > 0:
                        self._logger.info(
                            f"[{check_num}] Partially filled ({snapshot.filled_qty}), "
                            f"settling instead of repricing"
                        )
                        self._client.cancel_order(config.symbol, current_order_id)
                        final = self._final_snapshot(config.symbol, current_order_id, snapshot)
                        return self._settle_partial(
                            current_order_id,
                            quantity,
                            final.filled_qty,
                            current_price,
                            reprice_count,
                            "Partial fill settled",
                        )

                    if reprice_count >= config.max_reprices:
                        self._logger.info(
                            f"Max reprices ({config.max_reprices}) reached, giving up"
                        )
                        self._client.cancel_order(config.symbol, current_order_id)

                        # A fill can land between the last poll and the cancel; re-read
                        # before declaring that nothing was bought.
                        final = self._final_snapshot(config.symbol, current_order_id, snapshot)
                        if final.filled_qty > 0:
                            return self._settle_partial(
                                current_order_id,
                                quantity,
                                final.filled_qty,
                                current_price,
                                reprice_count,
                                "Filled during cancellation",
                            )

                        return OrderResult(
                            success=True,
                            filled=False,
                            order_id=current_order_id,
                            quantity=quantity,
                            price=current_price,
                            message="Max reprices reached",
                            reprices=reprice_count,
                            status="CANCELLED",
                        )

                    multiplier = REPRICE_MULTIPLIERS[
                        min(reprice_count, len(REPRICE_MULTIPLIERS) - 1)
                    ]
                    new_limit = round_step(current_ask * multiplier, rules.tick_size)
                    if new_limit <= current_price:
                        self._logger.info(
                            f"[{check_num}] Skipping reprice - price trending down "
                            f"(new {new_limit} <= current {current_price})"
                        )
                        intervals_above = 0
                        continue

                    current_order_id, current_price = self._reprice_order(
                        config,
                        current_order_id,
                        quantity,
                        current_ask,
                        multiplier,
                        rules,
                    )
                    reprice_count += 1
                    intervals_above = 0
                    self._logger.info(
                        f"New order {current_order_id} @ {current_price} "
                        f"(reprice {reprice_count}/{config.max_reprices}, multiplier {multiplier})"
                    )
            else:
                reset = intervals_above > 0
                intervals_above = 0
                self._log_check(
                    check_num, snapshot.status, current_price, current_ask, 0, config, reset
                )

    def _final_snapshot(
        self, symbol: str, order_id: str, fallback: OrderSnapshot
    ) -> OrderSnapshot:
        """Re-read an order after cancelling, falling back if the venue has forgotten it."""
        try:
            return self._client.get_order(symbol, order_id)
        except Exception as e:  # noqa: BLE001
            self._logger.warning(f"Could not re-read order {order_id} after cancel: {e}")
            return fallback

    def _settle_partial(
        self,
        order_id: str,
        requested_qty: Decimal,
        filled_qty: Decimal,
        price: Decimal,
        reprice_count: int,
        message: str,
    ) -> OrderResult:
        """
        Record a partial fill as the purchase it is.

        A fill that completed the order is reported as FILLED; anything short of that as
        PARTIALLY_FILLED. Both close the weekly gate, because both mean coin was acquired.
        """
        complete = filled_qty >= requested_qty
        self._logger.info(f"Acquired {filled_qty}/{requested_qty} @ {price}")
        return OrderResult(
            success=True,
            filled=True,
            order_id=order_id,
            quantity=requested_qty,
            filled_quantity=filled_qty,
            price=price,
            message=message,
            reprices=reprice_count,
            status="FILLED" if complete else "PARTIALLY_FILLED",
            partial=not complete,
        )

    def _reprice_order(
        self,
        config: OrderConfig,
        old_order_id: str,
        quantity: Decimal,
        current_ask: Decimal,
        multiplier: Decimal,
        rules: SymbolRules,
    ) -> tuple[str, Decimal]:
        """Cancel old order and place new one at current price."""
        self._client.cancel_order(config.symbol, old_order_id)

        new_price = round_step(current_ask * multiplier, rules.tick_size)
        placed = self._client.place_limit_order(
            symbol=config.symbol,
            side="BUY",
            quantity=quantity,
            price=new_price,
            time_in_force=config.time_in_force,
        )

        return placed.id, new_price

    def _log_rules(self, rules: SymbolRules) -> None:
        """Log venue trading rules."""
        self._logger.info(
            f"Rules: tick={rules.tick_size}, "
            f"step={rules.step_size}, "
            f"min_notional={rules.min_notional}"
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
        status: OrderStatus,
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
