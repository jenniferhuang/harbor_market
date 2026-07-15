# Harbor Market mock-first WeChat payment requirements

## Scope

This phase creates the payment boundary before Harbor Market has a WeChat Pay merchant account.
It must be safe to merge and deploy disabled, useful for local development, and replaceable by a
live WeChat Pay v3 adapter without changing payment state or database ownership rules.

## Requirements

1. Payment, order, and refund lifecycles remain separate. This phase implements payment attempts
   only; it does not infer an order or refund state.
2. Money is integer fen (`amount_cents`) and currently restricted to positive `CNY` values.
3. No customer API may provide an amount. Until the order module exists, attempt creation is an
   administrator-only mock control guarded by cookie authentication, admin authorization, and
   same-origin checks.
4. Every creation requires `X-Idempotency-Key`. Reusing the key with the same request returns the
   existing attempt; reusing it with different data returns `409`.
5. A single order reference normally has at most one active attempt. A closed or failed attempt
   may be retried with a new merchant order number only when the stored owner, amount, and currency
   remain unchanged. If provider truth reveals multiple successful attempts, all successes must be
   recorded and flagged for operational review rather than rejected by a uniqueness constraint.
6. Only a verified provider notification or server-side provider query can produce `succeeded`.
   Browser/mini-program return values are never payment authority.
7. Callback processing must reject bodies larger than 1.25 MiB (1,310,720 bytes), leaving bounded
   JSON-envelope room around WeChat's maximum 1 MiB ciphertext field; it must verify signature
   freshness and integrity, validate local immutable amount/currency/order invariants, and
   deduplicate `(provider, provider_event_id)` in the DB.
8. Close must query first, then close the unpaid provider transaction, and only persist `closed`
   after the provider confirms it. A provider success observed during the race wins.
9. All applied and ignored state observations are recorded as append-only state events. Every valid
   decoded callback, including one that does not match a local attempt, must have a durable inbox row
   containing its normalized provider AppID, merchant ID, merchant order, state, transaction/success
   identity, and amount/currency. Store the raw-body hash rather than raw plaintext so the envelope
   remains replay/investigation capable without retaining the callback body.
10. Mock provider transaction state must be stored in the DB so it survives process restarts and is
    consistent across backend workers. Guarded scenario control may inject only `NOTPAY`, `SUCCESS`,
    or `CLOSED`; it must not expose `PAYERROR` injection.
11. Mock controls and mock cryptography are impossible to enable when `ENVIRONMENT=production`.
    Production defaults to payments disabled.
12. Secrets must not appear in API responses, logs, committed `.env` files, payment rows, or state
    event details.
13. The adapter boundary must expose create-prepay, query, close, and verified notification decode
    operations. Live credentials and RSA/AES-GCM behavior remain outside this phase.
14. A live adapter cannot be enabled until outbound provider operations use claim/network/re-lock:
    commit a short idempotent claim, make the network call with no payment/order row locks held,
    then re-lock, revalidate, and apply the result idempotently.

## Explicit non-goals

- Customer checkout and order-item snapshots
- Inventory reservation/consumption
- WeChat login and trusted `openid` binding
- Live `/v3/pay/transactions/jsapi` network calls
- Refund requests, partial-refund accounting, and refund notifications
- Fulfillment and WeChat order-delivery reporting
