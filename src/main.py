"""
Crypto DCA Bot - Automated Binance Spot limit buy orders.

This script places a limit BUY order at a price slightly below the current
best ask price, implementing a dollar-cost averaging strategy.
"""

import argparse
import logging
import os
import sys
import time

from src.binance_client import (
    BinanceAPIError,
    BinanceClient,
    round_down,
    round_to_tick,
)
from src.utils import normalize_symbol, create_logger, log_config, parse_args, validate_args


def main() -> int:
    args = parse_args()
    logger = create_logger("crypto-dca", args.log_level)

    try:
        validate_args(args)
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        return 1

    # Get API credentials from environment
    api_key = os.environ.get("BINANCE_API_KEY")
    api_secret = os.environ.get("BINANCE_API_SECRET")

    if not api_key or not api_secret:
        logger.error(
            "BINANCE_API_KEY and BINANCE_API_SECRET environment variables are required"
        )
        return 1

    try:
        return run(args, logger, api_key, api_secret)
    except BinanceAPIError as e:
        logger.error(f"Binance API error: {e}")
        return 1
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return 1


def run(
    args: argparse.Namespace,
    logger: logging.Logger,
    api_key: str,
    api_secret: str,
) -> int:
    """Execute the DCA bot logic."""
    symbol = normalize_symbol(args.symbol)
    log_config(logger, args, symbol)

    # Initialize client with injected logger
    client = BinanceClient(
        api_key=api_key,
        api_secret=api_secret,
        base_url=args.base_url,
        recv_window=args.recv_window,
        logger=logger,
    )

    # Get exchange info
    logger.info(f"Fetching exchange info for {symbol}...")
    filters = client.get_exchange_info(symbol)
    logger.info(
        f"Filters: tick_size={filters['tick_size']}, "
        f"step_size={filters['step_size']}, "
        f"min_notional={filters['min_notional']}, "
        f"min_qty={filters['min_qty']}"
    )

    # Get best ask price
    logger.info(f"Fetching best ask price for {symbol}...")
    best_ask = client.get_best_ask(symbol)
    logger.info(f"Best ask price: {best_ask}")

    # Calculate limit price
    raw_limit_price = best_ask * args.price_multiplier
    limit_price = round_to_tick(raw_limit_price, filters["tick_size"])
    logger.info(
        f"Limit price: {best_ask} * {args.price_multiplier} = {raw_limit_price} -> {limit_price} (rounded to tick)"
    )

    # Calculate quantity
    raw_qty = args.spend_eur / limit_price
    quantity = round_down(raw_qty, filters["step_size"])
    logger.info(
        f"Quantity: {args.spend_eur} / {limit_price} = {raw_qty} -> {quantity} (rounded to step)"
    )

    # Validate against filters
    if quantity < filters["min_qty"]:
        logger.error(f"Quantity {quantity} is below minimum {filters['min_qty']}")
        return 1

    if quantity > filters["max_qty"]:
        logger.error(f"Quantity {quantity} exceeds maximum {filters['max_qty']}")
        return 1

    # Check min notional
    notional = quantity * limit_price
    if notional < filters["min_notional"]:
        logger.error(
            f"Order value {notional} is below minimum notional {filters['min_notional']}. "
            f"Increase --spend-eur."
        )
        return 1

    logger.info(f"Order notional value: {notional}")

    # Place order
    if args.dry_run:
        logger.info("=" * 60)
        logger.info("DRY RUN - Order would be placed with:")
        logger.info(f"  Symbol: {symbol}")
        logger.info("  Side: BUY")
        logger.info("  Type: LIMIT")
        logger.info(f"  Time in force: {args.time_in_force}")
        logger.info(f"  Quantity: {quantity}")
        logger.info(f"  Price: {limit_price}")
        logger.info(f"  Notional: {notional}")
        logger.info("=" * 60)
        logger.info("DRY RUN complete - no order placed")
        return 0

    # Place the limit order
    logger.info("Placing limit order...")
    order_response = client.place_limit_order(
        symbol=symbol,
        side="BUY",
        quantity=quantity,
        price=limit_price,
        time_in_force=args.time_in_force,
    )

    order_id = order_response.get("orderId")
    status = order_response.get("status")
    logger.info(f"Order placed successfully: orderId={order_id}, status={status}")

    # Handle timeout if configured
    if args.order_timeout > 0 and status in ("NEW", "PARTIALLY_FILLED"):
        logger.info(f"Waiting {args.order_timeout}s to check order status...")
        time.sleep(args.order_timeout)

        order_status = client.get_order(symbol, order_id)
        current_status = order_status.get("status")
        logger.info(f"Order status after timeout: {current_status}")

        if current_status in ("NEW", "PARTIALLY_FILLED"):
            logger.info("Order not fully filled, cancelling...")
            cancel_response = client.cancel_order(symbol, order_id)
            logger.info(f"Order cancelled: {cancel_response.get('status')}")

    logger.info("=" * 60)
    logger.info("Order execution complete")
    logger.info("=" * 60)
    return 0

if __name__ == "__main__":
    sys.exit(main())
