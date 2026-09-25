"""Contract tests run against every ExchangeInterface implementation.

No network and no credentials: each client's transport is stubbed, so what is under test
is the mapping layer — symbol rendering, venue payload -> SymbolRules, venue status
strings -> OrderStatus. That mapping is where the two venues actually differ, and where a
regression would silently place or abandon real orders.

Payloads are recorded verbatim from the live APIs.
"""

import base64
from decimal import Decimal

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

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
    "product_id": "BTC-EUR",
    "price": "81115.38",
    "quote_increment": "0.01",
    "base_increment": "0.00000001",
    "quote_min_size": "1",
    "base_min_size": "0.000016",
    "base_max_size": "1500",
    "status": "online",
    "trading_disabled": False,
}

# Any 32 bytes is a valid Ed25519 seed; fixed so the tests stay deterministic.
ED25519_SECRET = base64.b64encode(bytes(range(32))).decode()


@pytest.fixture
def binance() -> BinanceClient:
    return BinanceClient(api_key="k", api_secret="s")


@pytest.fixture
def coinbase() -> CoinbaseClient:
    return CoinbaseClient(api_key="k", api_secret=ED25519_SECRET)


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
    assert rules.max_qty == Decimal(9000)
    assert rules.min_notional == Decimal(5)


def test_coinbase_rules_mapping(coinbase, monkeypatch):
    monkeypatch.setattr(coinbase, "_request", lambda *a, **k: COINBASE_PRODUCT)
    rules = coinbase.get_symbol_rules("BTCEUR")

    assert rules.tick_size == Decimal("0.01")
    assert rules.step_size == Decimal("0.00000001")
    assert rules.min_qty == Decimal("0.000016")
    assert rules.max_qty == Decimal(1500)
    assert rules.min_notional == Decimal(1)


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
        # PENDING and QUEUED are *earlier* than OPEN, not partial fills.
        ({"status": "PENDING", "filled_size": "0"}, OrderStatus.NEW),
        ({"status": "QUEUED", "filled_size": "0"}, OrderStatus.NEW),
        ({"status": "OPEN", "filled_size": "0"}, OrderStatus.NEW),
        # A partial fill is filled_size on an open order, not a status of its own.
        ({"status": "OPEN", "filled_size": "0.0003"}, OrderStatus.PARTIALLY_FILLED),
        ({"status": "FILLED", "filled_size": "0.00073"}, OrderStatus.FILLED),
        # Cancelled after partially filling: a real purchase, must not be discarded.
        ({"status": "CANCELLED", "filled_size": "0.0004"}, OrderStatus.PARTIALLY_FILLED),
        ({"status": "CANCELLED", "filled_size": "0"}, OrderStatus.CANCELLED),
        ({"status": "EXPIRED", "filled_size": "0.0004"}, OrderStatus.PARTIALLY_FILLED),
        ({"status": "FAILED", "filled_size": "0"}, OrderStatus.FAILED),
        ({"status": "UNKNOWN_ORDER_STATUS", "filled_size": "0"}, OrderStatus.FAILED),
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
        coinbase,
        "_request",
        lambda *a, **k: {"success": True, "success_response": {"order_id": uuid}},
    )
    placed = coinbase.place_limit_order(
        "BTC-EUR", "BUY", Decimal("0.00073"), Decimal("68452.55")
    )

    assert placed.id == uuid
    assert placed.status is OrderStatus.NEW


def test_coinbase_place_raises_on_rejection_in_a_200_body(coinbase, monkeypatch):
    """Advanced Trade reports rejection with HTTP 200, so success must be checked."""
    monkeypatch.setattr(
        coinbase,
        "_request",
        lambda *a, **k: {
            "success": False,
            "error_response": {
                "message": "The order configuration was invalid",
                "error_details": "limit price too far from market",
                "new_order_failure_reason": "INVALID_LIMIT_PRICE",
            },
        },
    )

    with pytest.raises(CoinbaseAPIError) as excinfo:
        coinbase.place_limit_order(
            "BTC-EUR", "BUY", Decimal("0.00073"), Decimal("1.00")
        )

    assert excinfo.value.code == "INVALID_LIMIT_PRICE"


def test_coinbase_place_refuses_unsupported_time_in_force(coinbase):
    with pytest.raises(CoinbaseAPIError):
        coinbase.place_limit_order(
            "BTC-EUR", "BUY", Decimal("0.001"), Decimal(68000), time_in_force="IOC"
        )


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
        lambda *a, **k: {
            "order": {"order_id": "x", "status": "OPEN", "filled_size": "0.00031"}
        },
    )
    snap = coinbase.get_order("BTC-EUR", "x")

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
    """batch_cancel reports an already-gone order as a failure result, not an HTTP error."""
    monkeypatch.setattr(
        coinbase,
        "_request",
        lambda *a, **k: {
            "results": [{"success": False, "failure_reason": "UNKNOWN_CANCEL_ORDER"}]
        },
    )
    coinbase.cancel_order("BTC-EUR", "x")  # must not raise


def test_coinbase_cancel_still_raises_on_real_errors(coinbase, monkeypatch):
    monkeypatch.setattr(
        coinbase,
        "_request",
        lambda *a, **k: {
            "results": [{"success": False, "failure_reason": "COMMANDER_REJECTED_CANCEL_ORDER"}]
        },
    )
    with pytest.raises(CoinbaseAPIError):
        coinbase.cancel_order("BTC-EUR", "x")


# ── JWT authentication ───────────────────────────────────────────────────────


def test_coinbase_jwt_binds_method_host_and_path(coinbase):
    """The uri claim is what stops a token being replayed against another endpoint."""
    token = coinbase._jwt("POST", "/api/v3/brokerage/orders")

    header = jwt.get_unverified_header(token)
    claims = jwt.decode(token, options={"verify_signature": False})

    assert header["alg"] == "EdDSA"
    assert header["kid"] == "k"
    assert claims["sub"] == "k"
    assert claims["iss"] == "cdp"
    assert claims["uri"] == "POST api.coinbase.com/api/v3/brokerage/orders"
    assert claims["exp"] - claims["nbf"] == 120


def test_coinbase_jwt_differs_per_request(coinbase):
    a = coinbase._jwt("GET", "/api/v3/brokerage/products/BTC-EUR")
    b = coinbase._jwt("POST", "/api/v3/brokerage/orders")
    assert a != b

    # The nonce makes even two identical requests distinct.
    assert coinbase._jwt("GET", "/x") != coinbase._jwt("GET", "/x")


def test_coinbase_jwt_signature_verifies_against_the_public_key(coinbase):
    """A malformed token would be rejected by Coinbase, not by us: verify it here."""
    public_key = Ed25519PrivateKey.from_private_bytes(
        base64.b64decode(ED25519_SECRET)
    ).public_key()
    token = coinbase._jwt("GET", "/api/v3/brokerage/accounts")

    claims = jwt.decode(token, public_key, algorithms=["EdDSA"])

    assert claims["uri"] == "GET api.coinbase.com/api/v3/brokerage/accounts"


def test_coinbase_accepts_a_64_byte_ed25519_secret():
    """The CDP portal hands out seed||public_key; only the first 32 bytes are the seed."""
    seed = bytes(range(32))
    public = Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw()
    client = CoinbaseClient(
        api_key="k", api_secret=base64.b64encode(seed + public).decode()
    )

    assert jwt.get_unverified_header(client._jwt("GET", "/x"))["alg"] == "EdDSA"


def test_coinbase_accepts_a_legacy_ecdsa_pem_secret():
    from cryptography.hazmat.primitives.asymmetric import ec

    pem = (
        ec.generate_private_key(ec.SECP256R1())
        .private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        .decode()
    )
    client = CoinbaseClient(api_key="k", api_secret=pem)

    assert jwt.get_unverified_header(client._jwt("GET", "/x"))["alg"] == "ES256"


def test_coinbase_rejects_an_unusable_secret():
    with pytest.raises(ValueError):
        CoinbaseClient(api_key="k", api_secret=base64.b64encode(b"too-short").decode())


class _NonJsonResponse:
    """An HTML error page, as returned by auth failures and edge-proxy blocks."""

    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text
        self.ok = 200 <= status_code < 300

    def json(self):
        raise ValueError("not json")


def test_coinbase_reports_status_for_a_non_json_error_body(coinbase, monkeypatch):
    """A decode failure must not be reported as a transport error: the status matters."""
    monkeypatch.setattr(
        coinbase.session,
        "request",
        lambda *a, **k: _NonJsonResponse(401, "<html>Unauthorized</html>"),
    )

    with pytest.raises(CoinbaseAPIError) as excinfo:
        coinbase.get_symbol_rules("BTC-EUR")

    assert excinfo.value.status_code == 401
