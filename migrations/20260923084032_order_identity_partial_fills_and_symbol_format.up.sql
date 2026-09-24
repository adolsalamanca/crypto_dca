BEGIN;

-- Broker's own order id, so a run can be reconciled against the exchange after a
-- crash. TEXT because it is an int64 on Binance and a UUID on Coinbase.
ALTER TABLE crypto_dca.orders      ADD COLUMN IF NOT EXISTS exchange_order_id TEXT;
ALTER TABLE crypto_dca.last_orders ADD COLUMN IF NOT EXISTS exchange_order_id TEXT;

CREATE INDEX IF NOT EXISTS idx_orders_exchange_order_id
    ON crypto_dca.orders (exchange_order_id)
    WHERE exchange_order_id IS NOT NULL;

-- What the venue actually executed, as opposed to the size we asked for. These
-- differ on a partial fill.
ALTER TABLE crypto_dca.orders      ADD COLUMN IF NOT EXISTS filled_quantity NUMERIC(32, 18);
ALTER TABLE crypto_dca.last_orders ADD COLUMN IF NOT EXISTS filled_quantity NUMERIC(32, 18);

-- Historical rows were all-or-nothing.
UPDATE crypto_dca.orders
   SET filled_quantity = CASE WHEN status = 'FILLED' THEN quantity ELSE 0 END
 WHERE filled_quantity IS NULL;

UPDATE crypto_dca.last_orders
   SET filled_quantity = CASE WHEN status = 'FILLED' THEN quantity ELSE 0 END
 WHERE filled_quantity IS NULL;

-- No CHECK (filled_quantity <= quantity) on purpose: main.py swallows insert
-- errors, so a failed constraint would silently discard a real purchase.
ALTER TABLE crypto_dca.orders
    ALTER COLUMN filled_quantity SET DEFAULT 0,
    ALTER COLUMN filled_quantity SET NOT NULL,
    ADD CONSTRAINT orders_filled_quantity_check CHECK (filled_quantity >= 0);

ALTER TABLE crypto_dca.last_orders
    ALTER COLUMN filled_quantity SET DEFAULT 0,
    ALTER COLUMN filled_quantity SET NOT NULL;

ALTER TABLE crypto_dca.orders DROP CONSTRAINT IF EXISTS orders_status_check;

ALTER TABLE crypto_dca.orders
    ADD CONSTRAINT orders_status_check
    CHECK (status IN ('PENDING', 'FILLED', 'PARTIALLY_FILLED', 'CANCELLED', 'FAILED'));

-- Symbols become BASE-QUOTE. 'BTCEUR' cannot be split back without a table of
-- quote assets, which mis-splits anything missing from it.
--
-- Both tables: last_orders is trigger-maintained on INSERT only, so converting
-- orders alone would leave the weekly lookup matching nothing and the bot would
-- buy again in a week it already bought.
UPDATE crypto_dca.orders
   SET symbol = regexp_replace(
           symbol, '^(.+)(USDC|USDT|EUR|GBP|USD|BTC|ETH|DAI)$', '\1-\2'
       )
 WHERE symbol NOT LIKE '%-%';

UPDATE crypto_dca.last_orders
   SET symbol = regexp_replace(
           symbol, '^(.+)(USDC|USDT|EUR|GBP|USD|BTC|ETH|DAI)$', '\1-\2'
       )
 WHERE symbol NOT LIKE '%-%';

-- Abort rather than leave a row the application can no longer find.
DO $$
DECLARE
    stragglers text;
BEGIN
    SELECT string_agg(DISTINCT symbol, ', ') INTO stragglers
      FROM (
          SELECT symbol FROM crypto_dca.orders      WHERE symbol NOT LIKE '%-%'
          UNION
          SELECT symbol FROM crypto_dca.last_orders WHERE symbol NOT LIKE '%-%'
      ) unconverted;

    IF stragglers IS NOT NULL THEN
        RAISE EXCEPTION
            'Symbols could not be split into BASE-QUOTE: %. Add the quote asset to this migration.',
            stragglers;
    END IF;
END $$;

-- Carry the new columns into last_orders.
CREATE OR REPLACE FUNCTION crypto_dca.upsert_last_order()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO crypto_dca.last_orders (
        user_id, symbol, side, order_id, exchange_order_id,
        price, quantity, filled_quantity, multiplier, reprices,
        status, created_at, updated_at
    ) VALUES (
        NEW.user_id, NEW.symbol, NEW.side, NEW.id, NEW.exchange_order_id,
        NEW.price, NEW.quantity, NEW.filled_quantity, NEW.multiplier, NEW.reprices,
        NEW.status, NEW.created_at, now()
    )
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
        updated_at = now();

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Recreated only to pick up the new columns; WHEN is unchanged, since this table
-- tracks the last *filled* order. A PARTIALLY_FILLED order therefore does not
-- close the weekly gate. WHEN cannot be altered in place.
DROP TRIGGER IF EXISTS trigger_upsert_last_order ON crypto_dca.orders;

CREATE TRIGGER trigger_upsert_last_order
    AFTER INSERT ON crypto_dca.orders
    FOR EACH ROW
    WHEN (NEW.status = 'FILLED')
    EXECUTE FUNCTION crypto_dca.upsert_last_order();

COMMIT;
