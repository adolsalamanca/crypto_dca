"""Utility functions for the crypto DCA application."""

from datetime import UTC, datetime

from src.domain.models import Order


def is_same_week(order: Order | None) -> bool:
    """
    Check if an order was created in the current ISO week.

    ISO weeks start on Monday and are numbered 1-53.
    True if order was created in current week, False otherwise
    or if the order is None.
    """
    if order is None:
        return False

    current_week = datetime.now(UTC).isocalendar()[:2]  # (year, week_number)
    order_week = order.created_at.isocalendar()[:2]

    return current_week == order_week
