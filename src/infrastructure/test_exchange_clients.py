"""Contract tests run against every ExchangeInterface implementation.

No network and no credentials: each client's transport is stubbed, so what is under test
is the mapping layer — symbol rendering, venue payload -> SymbolRules, venue status
strings -> OrderStatus. That mapping is where the two venues actually differ, and where a
regression would silently place or abandon real orders.

Payloads are recorded verbatim from the live APIs.
"""

from decimal import Decimal

import pytest

from src.domain.models import OrderStatus
from src.infrastructure.binance_client import BinanceAPIError, BinanceClient
from src.infrastructure.coinbase_client import CoinbaseAPIError, CoinbaseClient

# ── recorded payloads ────────────────────────────────────────────────────────

BINANCE_EXCHANGE_INFO = {
    "symbols": [
        {
            "symbol": "BTCEUR",
            "filters": [
                {"filterType": "PRICE_FILTER", "tickSize": "0.01000000"},
                {
                    "filterType": "LOT_SIZE",
                    "stepSize": "0.00001000",
                    "minQty": "0.00001000",
                    "maxQty": "9000.00000000",
                },
                {"filterType": "NOTIONAL", "minNotional": "5.00000000"},
            ],
        }
    ]
}

COINBASE_PRODUCT = {
    "id": "BTC-EUR",
    "base_currency": "BTC",
    "quote_currency": "EUR",
    "quote_increment": "0.01",
    "base_increment": "0.00000001",
    "min_market_funds": "0.84",
    "status": "online",
    "trading_disabled": False,
}


@pytest.fixture
def binance() -> BinanceClient:
    return BinanceClient(api_key="k", api_secret="s")


@pytest.fixture
def coinbase() -> CoinbaseClient:
    # Secret must be valid base64: Coinbase decodes it before signing.
    return CoinbaseClient(api_key="k", api_secret="c2VjcmV0", passphrase="p")


# ── symbol rendering ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "given,expected",
    [("BTC-EUR", "BTCEUR"), ("btc/eur", "BTCEUR"), ("BTC_EUR", "BTCEUR")],
)
def test_binance_renders_pair_without_separator(binance, given, expected):
    assert binance.format_symbol(given) == expected


@pytest.mark.parametrize(
    "given,expected",
    [
        ("BTC-EUR", "BTC-EUR"),
        ("btc/eur", "BTC-EUR"),
        ("link-usdc", "LINK-USDC"),
    ],
)
def test_coinbase_passes_canonical_pair_through(coinbase, given, expected):
    """Coinbase product ids are the canonical form, so nothing is reconstructed."""
    assert coinbase.format_symbol(given) == expected


# ── venue payload -> SymbolRules ─────────────────────────────────────────────


def test_binance_rules_mapping(binance, monkeypatch):
    monkeypatch.setattr(binance, "_request", lambda *a, **k: BINANCE_EXCHANGE_INFO)
    rules = binance.get_symbol_rules("BTCEUR")

    assert rules.tick_size == Decimal("0.01")
    assert rules.step_size == Decimal("0.00001")
    assert rules.min_qty == Decimal("0.00001")
    assert rules.max_qty == Decimal("9000")
    assert rules.min_notional == Decimal("5")


def test_coinbase_rules_mapping(coinbase, monkeypatch):
    monkeypatch.setattr(coinbase, "_request", lambda *a, **k: COINBASE_PRODUCT)
    rules = coinbase.get_symbol_rules("BTCEUR")

    assert rules.tick_size == Decimal("0.01")
    assert rules.step_size == Decimal("0.00000001")
    assert rules.min_notional == Decimal("0.84")
    # Coinbase publishes no per-order maximum; validation must skip the check.
    assert rules.max_qty is None


def test_coinbase_refuses_disabled_product(coinbase, monkeypatch):
    payload = COINBASE_PRODUCT | {"trading_disabled": True}
    monkeypatch.setattr(coinbase, "_request", lambda *a, **k: payload)

    with pytest.raises(CoinbaseAPIError):
        coinbase.get_symbol_rules("BTCEUR")


# ── venue status -> OrderStatus ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("NEW", OrderStatus.NEW),
        ("PARTIALLY_FILLED", OrderStatus.PARTIALLY_FILLED),
        ("FILLED", OrderStatus.FILLED),
        ("CANCELED", OrderStatus.CANCELLED),
        ("EXPIRED", OrderStatus.CANCELLED),
        ("REJECTED", OrderStatus.FAILED),
        ("SOMETHING_NEW", OrderStatus.FAILED),
    ],
)
def test_binance_status_mapping(raw, expected):
    assert BinanceClient._map_status(raw) is expected


@pytest.mark.parametrize(
    "payload,expected",
    [
        # pending is *earlier* than open, not a partial fill.
        ({"status": "pending", "filled_size": "0"}, OrderStatus.NEW),
        ({"status": "open", "filled_size": "0"}, OrderStatus.NEW),
        # A partial fill is filled_size on an open order, not a status of its own.
        ({"status": "open", "filled_size": "0.0003"}, OrderStatus.PARTIALLY_FILLED),
        (
            {"status": "done", "done_reason": "filled", "filled_size": "0.00073"},
            OrderStatus.FILLED,
        ),
        # Cancelled after partially filling: a real purchase, must not be discarded.
        (
            {"status": "done", "done_reason": "canceled", "filled_size": "0.0004"},
            OrderStatus.PARTIALLY_FILLED,
        ),
        (
            {"status": "done", "done_reason": "canceled", "filled_size": "0"},
            OrderStatus.CANCELLED,
        ),
        ({"status": "rejected", "filled_size": "0"}, OrderStatus.FAILED),
    ],
)
def test_coinbase_status_mapping(payload, expected):
    assert CoinbaseClient._map_status(payload) is expected


# ── order lifecycle returns domain types ─────────────────────────────────────


def test_binance_place_returns_string_id(binance, monkeypatch):
    monkeypatch.setattr(
        binance, "_request", lambda *a, **k: {"orderId": 123456789, "status": "NEW"}
    )
    placed = binance.place_limit_order(
        "BTCEUR", "BUY", Decimal("0.00073"), Decimal("68452.55")
    )

    assert placed.id == "123456789"
    assert isinstance(placed.id, str)
    assert placed.status is OrderStatus.NEW


def test_coinbase_place_returns_uuid_id(coinbase, monkeypatch):
    uuid = "a9f3c1e2-5b6d-4e7a-8c9f-0d1e2f3a4b5c"
    monkeypatch.setattr(
        coinbase, "_request", lambda *a, **k: {"id": uuid, "status": "pending"}
    )
    placed = coinbase.place_limit_order(
        "BTCEUR", "BUY", Decimal("0.00073"), Decimal("68452.55")
    )

    assert placed.id == uuid
    assert placed.status is OrderStatus.NEW


def test_binance_snapshot_exposes_filled_quantity(binance, monkeypatch):
    monkeypatch.setattr(
        binance,
        "_request",
        lambda *a, **k: {
            "orderId": 1,
            "status": "PARTIALLY_FILLED",
            "executedQty": "0.00031",
        },
    )
    snap = binance.get_order("BTCEUR", "1")

    assert snap.status is OrderStatus.PARTIALLY_FILLED
    assert snap.filled_qty == Decimal("0.00031")


def test_coinbase_snapshot_exposes_filled_quantity(coinbase, monkeypatch):
    monkeypatch.setattr(
        coinbase,
        "_request",
        lambda *a, **k: {"id": "x", "status": "open", "filled_size": "0.00031"},
    )
    snap = coinbase.get_order("BTCEUR", "x")

    assert snap.status is OrderStatus.PARTIALLY_FILLED
    assert snap.filled_qty == Decimal("0.00031")


# ── cancelling an order that is already gone must not raise ──────────────────


def test_binance_cancel_tolerates_unknown_order(binance, monkeypatch):
    def boom(*a, **k):
        raise BinanceAPIError(400, -2011, "Unknown order sent.")

    monkeypatch.setattr(binance, "_request", boom)
    binance.cancel_order("BTCEUR", "1")  # must not raise


def test_binance_cancel_still_raises_on_real_errors(binance, monkeypatch):
    def boom(*a, **k):
        raise BinanceAPIError(401, -1022, "Signature invalid.")

    monkeypatch.setattr(binance, "_request", boom)
    with pytest.raises(BinanceAPIError):
        binance.cancel_order("BTCEUR", "1")


def test_coinbase_cancel_tolerates_missing_order(coinbase, monkeypatch):
    def boom(*a, **k):
        raise CoinbaseAPIError(404, None, "order not found")

    monkeypatch.setattr(coinbase, "_request", boom)
    coinbase.cancel_order("BTCEUR", "x")  # must not raise


# ── signing ──────────────────────────────────────────────────────────────────


def test_coinbase_signature_is_base64_over_path_and_body(coinbase):
    sig = coinbase._sign("1700000000", "POST", "/orders", '{"size":"1"}')
    import base64

    assert base64.b64decode(sig)  # valid base64
    # Body is covered by the signature: changing it must change the digest.
    assert sig != coinbase._sign("1700000000", "POST", "/orders", '{"size":"2"}')
