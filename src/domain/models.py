"""Domain models for crypto DCA application."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID


@dataclass
class User:
    """User entity."""

    name: str
    id: UUID | None = None


@dataclass
class Order:
    """Order entity."""

    user_id: UUID
    symbol: str
    side: str
    price: Decimal
    quantity: Decimal
    multiplier: Decimal
    reprices: int
    status: str
    created_at: datetime
    id: UUID | None = None
