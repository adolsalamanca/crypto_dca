"""Domain models for crypto DCA application."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID


class OrderStatus(StrEnum):
    """
    Exchange-side order state, normalized across brokers.

    This is deliberately separate from the status persisted on `Order`, which uses the
    narrower vocabulary the database CHECK constraint allows
    ('PENDING', 'FILLED', 'CANCELLED', 'FAILED'). Each exchange client maps its own
    strings into this enum so nothing downstream speaks a broker's dialect.
    """

    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class SymbolRules:
    """
    Trading constraints a venue enforces for one symbol.

    Replaces the untyped `dict[str, Any]` of exchange filters: every broker maps its own
    payload into these fields, so `round_step()` and order validation stay venue-agnostic.

    `max_qty` is None where a venue publishes no upper bound (Coinbase does not).
    """

    tick_size: Decimal
    step_size: Decimal
    min_qty: Decimal
    min_notional: Decimal
    max_qty: Decimal | None = None


@dataclass(frozen=True)
class PlacedOrder:
    """Result of submitting an order. `id` is opaque: int64 on Binance, UUID on Coinbase."""

    id: str
    status: OrderStatus


@dataclass(frozen=True)
class OrderSnapshot:
    """
    Point-in-time view of a resting order.

    `filled_qty` is the cumulative base amount executed so far. It is the field that makes
    partial fills visible: a venue may report an order as cancelled while `filled_qty > 0`,
    meaning a real purchase happened and must not be discarded.
    """

    id: str
    status: OrderStatus
    filled_qty: Decimal


@dataclass
class User:
    """User entity."""

    name: str
    id: UUID | None = None


@dataclass
class Order:
    """
    Order entity, as persisted.

    `quantity` is the size the order was placed at; `filled_quantity` is what the venue
    actually executed. They diverge on a partial fill. `exchange_order_id` is the broker's
    own opaque id, kept so a run can be reconciled against the venue after a crash.
    """

    user_id: UUID
    symbol: str
    side: str
    price: Decimal
    quantity: Decimal
    multiplier: Decimal
    reprices: int
    status: str
    created_at: datetime
    filled_quantity: Decimal = Decimal(0)
    exchange_order_id: str | None = None
    id: UUID | None = None
