"""Crypto DCA Bot - Entry point."""

import logging
import os
import sys

from src.binance_client import BinanceAPIError, BinanceClient
from src.cli import normalize_symbol, parse_args, validate_args
from src.dca_executor import DCAExecutor, OrderConfig


def main() -> int:
    args = parse_args()

    logger = logging.getLogger("crypto-dca")
    logger.setLevel(getattr(logging, args.log_level))
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(handler)

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

    logger.debug(
        f"Symbol: {symbol} | Spend: {args.spend_eur} EUR | Multiplier: {args.price_multiplier}"
    )
    logger.info(
        f"Poll: {args.poll_interval}s | Reprice after: {args.intervals_before_reprice} | Max reprices: {args.max_reprices}"
    )
    logger.info(f"Dry run: {args.dry_run}")

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

        if result.filled:
            logger.info(f"SUCCESS: Order filled - {result.quantity} @ {result.price}")
        elif result.success:
            logger.info(f"COMPLETE: {result.message}")
        else:
            logger.error(f"FAILED: {result.message}")

        return 0 if result.success else 1

    except BinanceAPIError as e:
        logger.error(f"Binance API error: {e}")
        return 1
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
