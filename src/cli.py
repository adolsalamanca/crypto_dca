"""Command-line interface parsing and validation."""

import argparse
import os
import re
from decimal import Decimal


def normalize_symbol(symbol: str) -> str:
    """Normalize trading pair symbol to Binance format (e.g., BTC/EUR -> BTCEUR)."""
    return re.sub(r"[-/_]", "", symbol.upper())


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Automated Binance Spot DCA bot for scheduled limit buy orders",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--base-url",
        default=os.environ.get("BINANCE_BASE_URL", "https://api.binance.com"),
        help="Binance API base URL (use https://testnet.binance.vision for testnet)",
    )

    parser.add_argument(
        "--symbol",
        default=os.environ.get("SYMBOL", "BTCEUR"),
        help="Trading pair symbol (e.g., BTCEUR, BTC/EUR, BTC-EUR)",
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

    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    """Validate command line arguments. Raises ValueError on invalid input."""
    spend_eur = args.spend_eur

    if isinstance(spend_eur, str):
        spend_eur = Decimal(spend_eur) if spend_eur else None
        args.spend_eur = spend_eur

    if spend_eur is None:
        raise ValueError("--spend-eur is required")

    if spend_eur <= 0:
        raise ValueError(f"--spend-eur must be positive, got {spend_eur}")

    if not (Decimal("0") < args.price_multiplier < Decimal("1")):
        raise ValueError(
            f"--price-multiplier must be between 0 and 1, got {args.price_multiplier}"
        )
