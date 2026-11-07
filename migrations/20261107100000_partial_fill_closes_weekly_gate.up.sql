BEGIN;

-- A partial fill is a real purchase. Until now only FILLED reached last_orders, so a
-- PARTIALLY_FILLED week stayed open and the next daily run bought the full amount again.
DROP TRIGGER IF EXISTS trigger_upsert_last_order ON crypto_dca.orders;

CREATE TRIGGER trigger_upsert_last_order
    AFTER INSERT ON crypto_dca.orders
    FOR EACH ROW
    WHEN (NEW.status IN ('FILLED', 'PARTIALLY_FILLED'))
    EXECUTE FUNCTION crypto_dca.upsert_last_order();

-- Backfill partial fills that the old trigger skipped, so a partial fill placed before
-- this migration still closes its week. Only a newer order replaces the current row.
INSERT INTO crypto_dca.last_orders (
    user_id, symbol, side, order_id, exchange_order_id,
    price, quantity, filled_quantity, multiplier, reprices,
    status, created_at, updated_at
)
SELECT DISTINCT ON (user_id, symbol, side)
       user_id, symbol, side, id, exchange_order_id,
       price, quantity, filled_quantity, multiplier, reprices,
       status, created_at, now()
  FROM crypto_dca.orders
 WHERE status IN ('FILLED', 'PARTIALLY_FILLED')
 ORDER BY user_id, symbol, side, created_at DESC
ON CONFLICT (user_id, symbol, side)
DO UPDATE SET
    order_id = EXCLUDED.order_id,
    exchange_order_id = EXCLUDED.exchange_order_id,
    price = EXCLUDED.price,
    quantity = EXCLUDED.quantity,
    filled_quantity = EXCLUDED.filled_quantity,
    multiplier = EXCLUDED.multiplier,
    reprices = EXCLUDED.reprices,
    status = EXCLUDED.status,
    created_at = EXCLUDED.created_at,
    updated_at = now()
WHERE crypto_dca.last_orders.created_at < EXCLUDED.created_at;

COMMIT;
