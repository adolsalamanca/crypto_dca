"""Tests for order persistence in the entry point."""

import logging
from decimal import Decimal
from uuid import UUID, uuid4

from src.dca_executor import OrderResult
from src.domain.models import Order, User
from src.infrastructure.repositories import Repository
from src.main import persist_order

USER_ID = UUID("0199a1f0-0000-7000-8000-000000000001")
MULTIPLIER = Decimal("0.999")


class FakeRepository(Repository):
    """In-memory repository; raises on add_order when `fail` is set."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.orders: list[Order] = []

    def add_user(self, user: User) -> UUID:
        return uuid4()

    def add_order(self, order: Order) -> UUID:
        if self.fail:
            raise RuntimeError("connection reset")
        self.orders.append(order)
        return uuid4()

    def get_last_order(self, user_id: UUID, symbol: str, side: str) -> Order | None:
        return self.orders[-1] if self.orders else None


def filled_result() -> OrderResult:
    return OrderResult(
        success=True,
        filled=True,
        order_id="abc-123",
        quantity=Decimal("0.001"),
        filled_quantity=Decimal("0.001"),
        price=Decimal("68000.00"),
        status="FILLED",
    )


def test_records_a_placed_order():
    repo = FakeRepository()

    assert persist_order(repo, USER_ID, "BTC-EUR", filled_result(), MULTIPLIER, logging.getLogger())

    assert len(repo.orders) == 1
    saved = repo.orders[0]
    assert saved.exchange_order_id == "abc-123"
    assert saved.symbol == "BTC-EUR"
    assert saved.filled_quantity == Decimal("0.001")
    assert saved.multiplier == MULTIPLIER


def test_reports_failure_when_the_write_fails():
    repo = FakeRepository(fail=True)

    assert not persist_order(
        repo, USER_ID, "BTC-EUR", filled_result(), MULTIPLIER, logging.getLogger()
    )


def test_reports_failure_when_a_placed_order_has_no_price():
    """An order the venue accepted but we cannot describe is still an unrecorded order."""
    result = OrderResult(success=True, filled=False, order_id="abc-123", status="CANCELLED")

    assert not persist_order(FakeRepository(), USER_ID, "BTC-EUR", result, MULTIPLIER, logging.getLogger())


def test_tolerates_an_order_that_never_reached_the_venue():
    """Nothing was placed, so there is nothing to record and no duplicate-buy risk."""
    result = OrderResult(success=False, filled=False, order_id=None, status="FAILED")

    assert persist_order(FakeRepository(), USER_ID, "BTC-EUR", result, MULTIPLIER, logging.getLogger())
