"""Crypto DCA Bot - Entry point."""

import logging
import os
import sys
from datetime import UTC, datetime
from uuid import UUID

from psycopg import Connection
from psycopg.rows import TupleRow
from psycopg_pool import ConnectionPool

from src.binance_client import BinanceAPIError, BinanceClient
from src.cli import normalize_symbol, parse_args, validate_args
from src.dca_executor import DCAExecutor, OrderConfig
from src.domain.models import Order
from src.infrastructure.repositories import PostgresRepository
from src.utils import is_same_week


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
    db_url = os.environ.get("DATABASE_URL")

    if not api_key or not api_secret:
        logger.error(
            "BINANCE_API_KEY and BINANCE_API_SECRET environment variables required"
        )
        return 1

    if not db_url:
        logger.error("DATABASE_URL environment variable required")
        return 1

    symbol = normalize_symbol(args.symbol)

    logger.debug(
        f"Symbol: {symbol} | Spend: {args.spend_eur} EUR | Multiplier: {args.price_multiplier}"
    )
    logger.info(
        f"Poll: {args.poll_interval}s | Reprice after: {args.intervals_before_reprice} | Max reprices: {args.max_reprices}"
    )
    logger.info(f"Dry run: {args.dry_run}")

    # Initialize database connection pool
    pool: ConnectionPool[Connection[TupleRow]] = ConnectionPool(db_url)
    repo = PostgresRepository(pool)
    user_uuid = UUID(args.user_id)

    try:
        # Weekly check - early exit if order already placed this week
        try:
            last_order = repo.get_last_order(user_uuid, symbol, "BUY")

            if is_same_week(last_order):
                logger.info("Nothing to do this week - order already placed")
                return 0

            logger.debug("Weekly check passed - proceeding with order")
        except Exception as e:
            logger.warning(f"Weekly check failed: {e}. Proceeding with order.")

        # Execute DCA order
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

        executor = DCAExecutor(client, logger)
        result = executor.execute(config, dry_run=args.dry_run)

        # Save order to database (all results except dry-run)
        if not args.dry_run:
            try:
                # Ensure price and quantity are not None
                if result.price is None or result.quantity is None:
                    logger.warning("Order result missing price or quantity, cannot save to database")
                else:
                    order_id = repo.add_order(
                        Order(
                            user_id=user_uuid,
                            symbol=symbol,
                            side="BUY",
                            price=result.price,
                            quantity=result.quantity,
                            multiplier=args.price_multiplier,
                            reprices=result.reprices,
                            status=result.status,
                            created_at=datetime.now(UTC),
                        )
                    )
                    logger.info(f"Order saved to database: {order_id} (status: {result.status})")
            except Exception as e:
                logger.error(f"Failed to save order to database: {e}")
                # Don't fail the main flow

        # Log result
        if result.filled:
            logger.info(f"SUCCESS: Order filled - {result.price}")
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
    finally:
        pool.close()


if __name__ == "__main__":
    sys.exit(main())
