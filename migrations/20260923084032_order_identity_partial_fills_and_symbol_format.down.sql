BEGIN;

-- Symbols back to the unseparated form.
UPDATE crypto_dca.orders      SET symbol = replace(symbol, '-', '');
UPDATE crypto_dca.last_orders SET symbol = replace(symbol, '-', '');

-- Restore the upsert function without the new columns.
DROP TRIGGER IF EXISTS trigger_upsert_last_order ON crypto_dca.orders;

CREATE OR REPLACE FUNCTION crypto_dca.upsert_last_order()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO crypto_dca.last_orders (
        user_id, symbol, side, order_id,
        price, quantity, multiplier, reprices,
        status, created_at, updated_at
    ) VALUES (
        NEW.user_id, NEW.symbol, NEW.side, NEW.id,
        NEW.price, NEW.quantity, NEW.multiplier, NEW.reprices,
        NEW.status, NEW.created_at, now()
    )
    ON CONFLICT (user_id, symbol, side)
    DO UPDATE SET
        order_id = EXCLUDED.order_id,
        price = EXCLUDED.price,
        quantity = EXCLUDED.quantity,
        multiplier = EXCLUDED.multiplier,
        reprices = EXCLUDED.reprices,
        status = EXCLUDED.status,
        created_at = EXCLUDED.created_at,
        updated_at = now();

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_upsert_last_order
    AFTER INSERT ON crypto_dca.orders
    FOR EACH ROW
    WHEN (NEW.status = 'FILLED')
    EXECUTE FUNCTION crypto_dca.upsert_last_order();

-- Collapse PARTIALLY_FILLED or the narrower CHECK cannot be re-added. Lossy:
-- partial and complete fills become indistinguishable once filled_quantity goes.
UPDATE crypto_dca.orders      SET status = 'FILLED' WHERE status = 'PARTIALLY_FILLED';
UPDATE crypto_dca.last_orders SET status = 'FILLED' WHERE status = 'PARTIALLY_FILLED';

ALTER TABLE crypto_dca.orders DROP CONSTRAINT IF EXISTS orders_status_check;
ALTER TABLE crypto_dca.orders
    ADD CONSTRAINT orders_status_check
    CHECK (status IN ('PENDING', 'FILLED', 'CANCELLED', 'FAILED'));

ALTER TABLE crypto_dca.orders DROP CONSTRAINT IF EXISTS orders_filled_quantity_check;

DROP INDEX IF EXISTS crypto_dca.idx_orders_exchange_order_id;

ALTER TABLE crypto_dca.orders      DROP COLUMN IF EXISTS filled_quantity;
ALTER TABLE crypto_dca.last_orders DROP COLUMN IF EXISTS filled_quantity;
ALTER TABLE crypto_dca.orders      DROP COLUMN IF EXISTS exchange_order_id;
ALTER TABLE crypto_dca.last_orders DROP COLUMN IF EXISTS exchange_order_id;

COMMIT;
