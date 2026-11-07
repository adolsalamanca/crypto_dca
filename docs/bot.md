# How the bot works

Each run buys one coin with a limit order, then records the result in Postgres. A weekly check
on that record stops the bot from buying the same coin twice in one week.

## Placing an order

1. The bot reads the best ask price and multiplies it by `--price-multiplier` (default `0.999`).
   The result, rounded to the exchange's tick size, is the limit price.
2. It places a limit BUY for `--spend-eur` worth of the coin at that price.
3. It checks the order every `--poll-interval` seconds (default 60).
4. If the ask stays above the limit price for `--intervals-before-reprice` checks in a row
   (default 5), the bot cancels the order and places a new one at the multiplier times the
   new ask.
5. After `--max-reprices` new orders (default 3), it cancels the order and stops.

A partially filled order is never repriced. Cancelling it to chase the price would leave the
filled part unrecorded, so the bot cancels the rest and records the partial fill as a purchase.

`--spend-eur` is an amount in the quote asset of the pair. For `BTC-USDT` it spends USDT, not EUR.

## The weekly check

Before it places an order, the bot looks for a purchase of the same coin in the current ISO week
(Monday to Sunday, UTC). Each coin is independent. If it finds a purchase, the run exits without
an order.

The bot saves every order it places, but only an order that bought coin closes the week:

- A `FILLED` or `PARTIALLY_FILLED` order closes the week. A partial fill buys less than the
  weekly spend, and the bot does not buy the remainder.
- A `CANCELLED` order, for example after the maximum number of reprices, leaves the week open.
  The next daily run tries again.

If the bot cannot read the database, it places no order and exits with a non-zero code, because
it cannot tell whether this week already bought. The next daily run tries again.

## When a run fails on purpose

The bot exits with a non-zero code instead of risking a duplicate purchase when:

- an order reached the exchange but could not be saved, because the weekly check reads the saved
  row and would otherwise buy again on a later run;
- `USER_ID` does not match a row in `crypto_dca.users`.

## Symbols

Symbols are written `BASE-QUOTE`, for example `BTC-EUR` or `LINK-USDC`, and the dash is
required. Without it, splitting a pair into base and quote would need a table of quote assets,
and that table would silently split any asset missing from it the wrong way. Each exchange
client converts the canonical form to its own notation: the Binance client drops the dash, the
Coinbase client keeps it.
