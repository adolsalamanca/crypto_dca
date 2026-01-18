"""Repository interfaces and implementations for persistence."""

from abc import ABC, abstractmethod
from typing import Optional
from uuid import UUID

from psycopg import Connection
from psycopg.rows import TupleRow
from psycopg_pool import ConnectionPool

from src.domain.models import Order, User


class Repository(ABC):
    """Abstract repository interface."""

    @abstractmethod
    def add_user(self, user: User) -> UUID:
        """Add a new user. Returns the generated user ID."""
        ...

    @abstractmethod
    def add_order(self, order: Order) -> UUID:
        """Add a new order. Returns the generated order ID."""
        ...

    @abstractmethod
    def get_last_order(
        self, user_id: UUID, symbol: str, side: str
    ) -> Optional[Order]:
        """Get the last order for a user/symbol/side combination."""
        ...


class PostgresRepository(Repository):
    """PostgreSQL implementation of the repository."""

    def __init__(self, pool: ConnectionPool[Connection[TupleRow]]):
        self._pool = pool

    def add_user(self, user: User) -> UUID:
        """Add a new user to the database. Returns the generated user ID."""
        with self._pool.connection() as conn:
            result = conn.execute(
                """
                INSERT INTO crypto_dca.users (name)
                VALUES (%s)
                RETURNING id
                """,
                (user.name,),
            )
            row = result.fetchone()
            if row is None:
                raise RuntimeError("Failed to insert user")
            return row[0]

    def add_order(self, order: Order) -> UUID:
        """Add a new order to the database. Returns the generated order ID."""
        with self._pool.connection() as conn:
            result = conn.execute(
                """
                INSERT INTO crypto_dca.orders
                (user_id, symbol, side, price, quantity, multiplier, reprices, status, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    order.user_id,
                    order.symbol,
                    order.side,
                    order.price,
                    order.quantity,
                    order.multiplier,
                    order.reprices,
                    order.status,
                    order.created_at,
                ),
            )
            row = result.fetchone()
            if row is None:
                raise RuntimeError("Failed to insert order")
            return row[0]

    def get_last_order(
        self, user_id: UUID, symbol: str, side: str
    ) -> Optional[Order]:
        """
        Get the last order for a user/symbol/side combination.

        Reads from the last_orders table which is automatically maintained
        by the database trigger.
        """
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT order_id, user_id, symbol, side, price, quantity,
                           multiplier, reprices, status, created_at
                    FROM crypto_dca.last_orders
                    WHERE user_id = %s AND symbol = %s AND side = %s
                    """,
                    (user_id, symbol, side),
                )
                row = cur.fetchone()

                if row is None:
                    return None

                return Order(
                    id=row[0],
                    user_id=row[1],
                    symbol=row[2],
                    side=row[3],
                    price=row[4],
                    quantity=row[5],
                    multiplier=row[6],
                    reprices=row[7],
                    status=row[8],
                    created_at=row[9],
                )
