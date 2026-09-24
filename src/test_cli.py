"""Tests for CLI parsing and validation."""

import pytest

from src.cli import normalize_symbol


@pytest.mark.parametrize(
    "given,expected",
    [
        ("BTC-EUR", "BTC-EUR"),
        ("btc-eur", "BTC-EUR"),
        ("BTC/EUR", "BTC-EUR"),
        ("BTC_EUR", "BTC-EUR"),
        ("  BTC-EUR  ", "BTC-EUR"),
        ("LINK-USDC", "LINK-USDC"),
    ],
)
def test_canonical_form_keeps_the_separator(given, expected):
    assert normalize_symbol(given) == expected


@pytest.mark.parametrize(
    "given",
    [
        "BTCEUR",  # the old form: ambiguous, must be rejected rather than guessed
        "BTC-EUR-X",
        "-EUR",
        "BTC-",
        "",
    ],
)
def test_ambiguous_or_malformed_symbols_are_rejected(given):
    """Splitting an unseparated pair needs a quote-asset table; refusing is the fix."""
    with pytest.raises(ValueError, match="BASE-QUOTE"):
        normalize_symbol(given)
