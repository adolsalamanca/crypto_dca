"""
Crypto DCA Bot - Automated Binance Spot limit buy orders.

Entry point that wires up dependencies and executes the DCA order.
"""

import os
import sys

from src.binance_client import BinanceAPIError, BinanceClient
from src.dca_executor import DCAExecutor, OrderConfig
from src.utils import (
    create_logger,
    log_config,
    normalize_symbol,
    parse_args,
    validate_args,
)


def main() -> int:
    args = parse_args()
    logger = create_logger("crypto-dca", args.log_level)

    try:
        validate_args(args)
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        return 1

    api_key = os.environ.get("BINANCE_API_KEY")
    api_secret = os.environ.get("BINANCE_API_SECRET")

    if not api_key or not api_secret:
        logger.error(
            "BINANCE_API_KEY and BINANCE_API_SECRET environment variables required"
        )
        return 1

    symbol = normalize_symbol(args.symbol)
    log_config(logger, args, symbol)

    client = BinanceClient(
        api_key=api_key,
        api_secret=api_secret,
        base_url=args.base_url,
        recv_window=args.recv_window,
        logger=logger,
    )

    config = OrderConfig(
        symbol=symbol,
        spend_quote=args.spend_eur,
        price_multiplier=args.price_multiplier,
        time_in_force=args.time_in_force,
        poll_interval=args.poll_interval,
        intervals_before_reprice=args.intervals_before_reprice,
        max_reprices=args.max_reprices,
    )

    try:
        executor = DCAExecutor(client, logger)
        result = executor.execute(config, dry_run=args.dry_run)

        logger.info("=" * 60)
        if result.filled:
            logger.info(f"SUCCESS: Order filled - {result.quantity} @ {result.price}")
        elif result.success:
            logger.info(f"COMPLETE: {result.message}")
        else:
            logger.error(f"FAILED: {result.message}")
        logger.info("=" * 60)

        return 0 if result.success else 1

    except BinanceAPIError as e:
        logger.error(f"Binance API error: {e}")
        return 1
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
