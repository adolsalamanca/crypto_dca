"""Command-line interface parsing and validation."""

import argparse
import os
import re
from decimal import Decimal
from uuid import UUID


def normalize_symbol(symbol: str) -> str:
    """
    Normalize a trading pair to the canonical BASE-QUOTE form (e.g. BTC/EUR -> BTC-EUR).

    The separator is kept deliberately. An unseparated pair like 'BTCEUR' cannot be split
    back into base and quote without a hardcoded table of quote assets, which silently
    mis-splits every asset missing from it. Each exchange client renders this canonical
    form into its own notation; that direction is always unambiguous.
    """
    normalized = re.sub(r"[/_]", "-", symbol.upper().strip())

    if normalized.count("-") != 1 or normalized.startswith("-") or normalized.endswith("-"):
        raise ValueError(
            f"Invalid symbol '{symbol}': expected BASE-QUOTE, for example BTC-EUR"
        )

    return normalized


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Automated Binance Spot DCA bot for scheduled limit buy orders",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--exchange",
        default=os.environ.get("EXCHANGE", "coinbase"),
        choices=["binance", "coinbase"],
        help="Which exchange to trade on",
    )

    parser.add_argument(
        "--base-url",
        default=os.environ.get("BASE_URL") or os.environ.get("BINANCE_BASE_URL"),
        help=(
            "Exchange API base URL. Defaults per exchange; use "
            "https://testnet.binance.vision (Binance testnet) or "
            "https://api-public.sandbox.exchange.coinbase.com (Coinbase sandbox)"
        ),
    )

    parser.add_argument(
        "--symbol",
        default=os.environ.get("SYMBOL", "BTC-EUR"),
        help="Trading pair symbol as BASE-QUOTE (e.g., BTC-EUR, BTC/EUR, BTC_EUR)",
    )

    parser.add_argument(
        "--spend-eur",
        type=Decimal,
        default=os.environ.get("SPEND_EUR"),
        help="Amount in quote asset (EUR) to spend",
    )

    parser.add_argument(
        "--price-multiplier",
        type=Decimal,
        default=Decimal(os.environ.get("PRICE_MULTIPLIER", "0.999")),
        help="Multiplier for limit price (e.g., 0.999 = 99.9%% of best ask)",
    )

    parser.add_argument(
        "--time-in-force",
        default=os.environ.get("TIME_IN_FORCE", "GTC"),
        choices=["GTC", "IOC", "FOK"],
        help="Order time in force",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=os.environ.get("DRY_RUN", "false").lower() == "true",
        help="Simulate order without actually placing it",
    )

    parser.add_argument(
        "--log-level",
        default=os.environ.get("LOG_LEVEL", "INFO"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )

    parser.add_argument(
        "--poll-interval",
        type=int,
        default=60,
        help="Seconds between order status checks",
    )

    parser.add_argument(
        "--intervals-before-reprice",
        type=int,
        default=5,
        help="Consecutive intervals price must be above limit before repricing",
    )

    parser.add_argument(
        "--max-reprices",
        type=int,
        default=3,
        help="Maximum reprice attempts before giving up",
    )

    parser.add_argument(
        "--recv-window",
        type=int,
        default=int(os.environ.get("RECV_WINDOW", "5000")),
        help="Binance API recvWindow parameter",
    )

    parser.add_argument(
        "--user-id",
        default=os.environ.get("USER_ID"),
        help="User UUID for weekly order tracking (required)",
    )

    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    """Validate command line arguments. Raises ValueError on invalid input."""
    # Raises on a symbol that cannot be split; checked here so main() reports it as a
    # configuration error rather than failing later with an unhandled exception.
    normalize_symbol(args.symbol)

    spend_eur = args.spend_eur

    if isinstance(spend_eur, str):
        spend_eur = Decimal(spend_eur) if spend_eur else None
        args.spend_eur = spend_eur

    if spend_eur is None:
        raise ValueError("--spend-eur is required")

    if spend_eur <= 0:
        raise ValueError(f"--spend-eur must be positive, got {spend_eur}")

    if not (Decimal(0) < args.price_multiplier < Decimal(1)):
        raise ValueError(
            f"--price-multiplier must be between 0 and 1, got {args.price_multiplier}"
        )

    # Validate user_id
    if not args.user_id:
        raise ValueError("--user-id is required")

    try:
        UUID(args.user_id)
    except ValueError:
        raise ValueError(f"Invalid user_id UUID format: {args.user_id}")
