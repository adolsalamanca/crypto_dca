BEGIN;

-- Restore the FILLED-only trigger. Rows the up migration backfilled stay in last_orders:
-- they record real purchases, and removing them could reopen a week that already bought.
DROP TRIGGER IF EXISTS trigger_upsert_last_order ON crypto_dca.orders;

CREATE TRIGGER trigger_upsert_last_order
    AFTER INSERT ON crypto_dca.orders
    FOR EACH ROW
    WHEN (NEW.status = 'FILLED')
    EXECUTE FUNCTION crypto_dca.upsert_last_order();

COMMIT;
