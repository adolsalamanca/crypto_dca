BEGIN;

CREATE SCHEMA crypto_dca;

CREATE TABLE crypto_dca.users (
    id UUID DEFAULT uuidv7() PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE crypto_dca.orders (
    id UUID DEFAULT uuidv7() PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES crypto_dca.users(id),
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    price NUMERIC(32, 18) NOT NULL CHECK (price > 0),
    quantity NUMERIC(32, 18) NOT NULL CHECK (quantity > 0),
    -- Multiplier to buy at a discount (0.999 = 0.1% discount)
    multiplier NUMERIC(10, 6) NOT NULL DEFAULT 0.999 CHECK (multiplier > 0 AND multiplier <= 1),
    -- Number of reprices the app had to go through before storing the order
    reprices INTEGER DEFAULT 0 NOT NULL CHECK (reprices >= 0),
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'FILLED', 'CANCELLED', 'FAILED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_orders_user_id ON crypto_dca.orders(user_id);
CREATE INDEX idx_orders_user_id_status ON crypto_dca.orders(user_id, status, created_at);
CREATE INDEX idx_orders_user_symbol_side ON crypto_dca.orders(user_id, symbol, side);

-- Last orders table tracks most recent order per (user_id, symbol, side)
CREATE TABLE crypto_dca.last_orders (
    user_id UUID NOT NULL REFERENCES crypto_dca.users(id),
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    order_id UUID NOT NULL REFERENCES crypto_dca.orders(id),
    price NUMERIC(32, 18) NOT NULL,
    quantity NUMERIC(32, 18) NOT NULL,
    multiplier NUMERIC(10, 6) NOT NULL,
    reprices INTEGER NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, symbol, side)
);

-- Create function to upsert into last_orders
CREATE OR REPLACE FUNCTION crypto_dca.upsert_last_order()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO crypto_dca.last_orders (
        user_id,
        symbol,
        side,
        order_id,
        price,
        quantity,
        multiplier,
        reprices,
        status,
        created_at,
        updated_at
    ) VALUES (
        NEW.user_id,
        NEW.symbol,
        NEW.side,
        NEW.id,
        NEW.price,
        NEW.quantity,
        NEW.multiplier,
        NEW.reprices,
        NEW.status,
        NEW.created_at,
        now()
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

-- Create trigger to automatically maintain last_orders
CREATE TRIGGER trigger_upsert_last_order
    AFTER INSERT ON crypto_dca.orders
    FOR EACH ROW
    WHEN (NEW.status = 'FILLED')
    EXECUTE FUNCTION crypto_dca.upsert_last_order();

COMMIT;
