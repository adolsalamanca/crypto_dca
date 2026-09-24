"""Tests for DCA execution, focused on outcomes that move money.

The cases that matter are the ones where the bot acquires coin but could fail to record
it: a partial fill, and a fill that lands between the final poll and the cancel.
"""

import logging
from decimal import Decimal

import pytest

from src.dca_executor import DCAExecutor, OrderConfig
from src.domain.models import OrderSnapshot, OrderStatus, PlacedOrder, SymbolRules
from src.infrastructure.exchange import ExchangeInterface

RULES = SymbolRules(
    tick_size=Decimal("0.01"),
    step_size=Decimal("0.00001"),
    min_qty=Decimal("0.00001"),
    min_notional=Decimal("5"),
    max_qty=Decimal("9000"),
)


class FakeExchange(ExchangeInterface):
    """Scripted exchange: `snapshots` is replayed one entry per poll."""

    def __init__(self, snapshots, ask=Decimal("68521.08"), final=None):
        super().__init__(None)
        self._snapshots = list(snapshots)
        self._ask = ask
        self._final = final
        self.cancelled: list[str] = []
        self.placed: list[Decimal] = []

    def format_symbol(self, symbol: str) -> str:
        return symbol

    def get_symbol_rules(self, symbol: str) -> SymbolRules:
        return RULES

    def get_best_ask(self, symbol: str) -> Decimal:
        return self._ask

    def place_limit_order(self, symbol, side, quantity, price, time_in_force="GTC"):
        self.placed.append(price)
        return PlacedOrder(id=f"order-{len(self.placed)}", status=OrderStatus.NEW)

    def get_order(self, symbol: str, order_id: str) -> OrderSnapshot:
        # Once the script runs out, replay the final state (post-cancel re-read).
        if self._snapshots:
            return self._snapshots.pop(0)
        assert self._final is not None, "unexpected extra get_order call"
        return self._final

    def cancel_order(self, symbol: str, order_id: str) -> None:
        self.cancelled.append(order_id)


def snap(status: OrderStatus, filled="0") -> OrderSnapshot:
    return OrderSnapshot(id="order-1", status=status, filled_qty=Decimal(filled))


@pytest.fixture
def config() -> OrderConfig:
    return OrderConfig(
        symbol="BTC-EUR",
        spend_quote=Decimal("50"),
        price_multiplier=Decimal("0.999"),
        time_in_force="GTC",
        poll_interval=0,
        intervals_before_reprice=1,
        max_reprices=0,
    )


@pytest.fixture
def logger() -> logging.Logger:
    log = logging.getLogger("test")
    log.addHandler(logging.NullHandler())
    return log


def test_gives_up_cleanly_when_nothing_filled(config, logger):
    client = FakeExchange([snap(OrderStatus.NEW)], final=snap(OrderStatus.CANCELLED))
    result = DCAExecutor(client, logger).execute(config)

    assert result.status == "CANCELLED"
    assert result.filled is False
    assert result.partial is False
    assert client.cancelled == ["order-1"]


def test_partial_fill_is_recorded_as_a_purchase(config, logger):
    """The order was cancelled at max reprices, but 0.0004 BTC was actually bought."""
    client = FakeExchange(
        [snap(OrderStatus.PARTIALLY_FILLED, "0.0004")],
        final=snap(OrderStatus.PARTIALLY_FILLED, "0.0004"),
    )
    result = DCAExecutor(client, logger).execute(config)

    assert result.filled is True
    assert result.partial is True
    assert result.status == "PARTIALLY_FILLED"
    # Requested and executed are recorded separately.
    assert result.filled_quantity == Decimal("0.0004")
    assert result.quantity == Decimal("0.00073")
    assert client.cancelled == ["order-1"]


def test_partially_filled_order_is_never_repriced(config, logger):
    """Repricing a partial fill would strand the executed portion."""
    config.max_reprices = 3
    client = FakeExchange(
        [snap(OrderStatus.PARTIALLY_FILLED, "0.0004")],
        final=snap(OrderStatus.PARTIALLY_FILLED, "0.0004"),
    )
    result = DCAExecutor(client, logger).execute(config)

    assert result.partial is True
    assert len(client.placed) == 1, "must not place a replacement order"


def test_fill_landing_during_cancellation_is_not_lost(config, logger):
    """A fill between the last poll and the cancel is caught by the post-cancel re-read."""
    client = FakeExchange(
        [snap(OrderStatus.NEW)],
        final=snap(OrderStatus.FILLED, "0.00073"),
    )
    result = DCAExecutor(client, logger).execute(config)

    assert result.filled is True
    assert result.filled_quantity == Decimal("0.00073")
    # Filled the full requested size, so it is a complete fill, not a partial one.
    assert result.status == "FILLED"
    assert result.partial is False


def test_externally_cancelled_with_partial_fill_is_settled(config, logger):
    """The venue cancelled the order on us, but part of it had already executed."""
    client = FakeExchange([snap(OrderStatus.CANCELLED, "0.0002")])
    result = DCAExecutor(client, logger).execute(config)

    assert result.filled is True
    assert result.partial is True
    assert result.status == "PARTIALLY_FILLED"
    assert result.filled_quantity == Decimal("0.0002")


def test_cancelled_order_records_no_acquisition(config, logger):
    client = FakeExchange([snap(OrderStatus.NEW)], final=snap(OrderStatus.CANCELLED))
    result = DCAExecutor(client, logger).execute(config)

    assert result.filled_quantity == Decimal(0)
    assert result.status == "CANCELLED"


def test_rejected_order_with_no_fill_is_a_failure(config, logger):
    client = FakeExchange([snap(OrderStatus.FAILED)])
    result = DCAExecutor(client, logger).execute(config)

    assert result.success is False
    assert result.status == "FAILED"


def test_immediate_fill_needs_no_monitoring(config, logger):
    client = FakeExchange([])
    client.place_limit_order = lambda *a, **k: PlacedOrder(  # type: ignore[method-assign]
        id="order-1", status=OrderStatus.FILLED
    )
    result = DCAExecutor(client, logger).execute(config)

    assert result.filled is True
    assert result.reprices == 0
    assert client.cancelled == []


def test_validation_skips_max_qty_when_venue_publishes_none(config, logger):
    """Coinbase has no max_qty; validation must not reject on a missing bound."""
    client = FakeExchange([], final=snap(OrderStatus.FILLED, "0.00073"))
    client.get_symbol_rules = lambda symbol: SymbolRules(  # type: ignore[method-assign]
        tick_size=Decimal("0.01"),
        step_size=Decimal("0.00000001"),
        min_qty=Decimal("0.00000001"),
        min_notional=Decimal("0.84"),
        max_qty=None,
    )
    result = DCAExecutor(client, logger).execute(config, dry_run=True)

    assert result.success is True
    assert "Dry run" in result.message
