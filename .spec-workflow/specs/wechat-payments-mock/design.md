# Harbor Market mock-first WeChat payment design

## Module boundary

```text
admin mock route / future order service
                 |
                 v
          PaymentService
          /      |      \
 state policy   DB      PaymentGateway
                         |       |
                       mock   future live v3
```

The service owns transitions and invariant checks. Provider-specific request fields, signatures,
callbacks, and raw trade states stay behind `PaymentGateway`. SQLAlchemy models do not contain API
keys, merchant private keys, API v3 keys, or Mini Program AppSecret values.

## State policy

| Current | Observation/action | Next | Authority |
|---|---|---|---|
| `created` | prepay created | `pending` | provider create result |
| `created`/`pending` | payment success | `succeeded` | verified callback or query |
| `pending` | close confirmed | `closed` | provider query + close result |
| `created`/`pending` | terminal provider error | `failed` | provider result/query |
| `closed`/`failed` | late payment success | `succeeded` | verified callback or query only |
| `succeeded` | any unpaid state | unchanged | success never regresses |

Same-state observations are idempotent. Stale/conflicting provider states are ignored and audited.
WeChat query state `REFUND` proves an earlier payment success but does not prove full refund; the
future refund aggregate will own partial/full refund state.

## Database

### `payment_attempts`

- Public UUID and internal identity key
- Owner, immutable business order reference, provider/mode/AppID/merchant ID
- Unique WeChat-style `merchant_order_no` (`out_trade_no`)
- Integer amount, CNY currency, description, request hash, API idempotency key
- Canonical and raw provider status
- Prepay ID/expiry and mock client parameters
- Provider transaction ID, terminal timestamps/reasons, optimistic version
- Partial unique index for one active attempt per order reference; multiple provider-confirmed
  successes are retained and flagged because database constraints must not erase financial truth

### `payment_state_events`

Append-only applied/ignored transition history: source, old/new status, reason, and sanitized
details. This is an audit trail and future integration source; it is not a refund ledger.

### `payment_provider_events`

Durable notification inbox keyed by `(provider, provider_event_id)`. Every valid decoded callback,
including one with no matching local attempt, stores a normalized envelope containing provider AppID
and merchant ID, merchant order number, provider state, transaction ID and success time, and
amount/currency. It also stores the raw-body SHA-256, verification and processing status, timestamps,
and a nullable matched attempt, but never the raw plaintext body. The normalized envelope supports
later replay and investigation. Exact replays are acknowledged; event-ID reuse with a different
body is rejected.

### `payment_mock_provider_records`

Durable development-provider state keyed by merchant order number: immutable request fingerprint,
amount/currency, current prepay generation and client parameters, raw trade state, and success
identity/timestamp. Keeping this state in PostgreSQL makes mock query, close, callback, and prepay
refresh behavior survive process restarts and remain consistent across backend workers.

## API in this phase

- `POST /api/v1/admin/payments` — create a mock attempt; requires admin and idempotency key
- `GET /api/v1/admin/payments/{public_id}` — inspect attempt and state history
- `POST /api/v1/admin/payments/{public_id}/reconcile` — query provider and reconcile
- `POST /api/v1/admin/payments/{public_id}/refresh-prepay` — refresh an expired prepay ID
- `POST /api/v1/admin/payments/{public_id}/close` — query-before-close
- `POST /api/v1/admin/payments/{public_id}/mock/provider-state` — guarded scenario control
- `POST /api/v1/payments/providers/wechat-pay/notify` — signed provider callback boundary

The callback route intentionally has no cookie authentication or browser CSRF dependency. Its
trust boundary is provider signature verification and local invariant validation. The application
rejects callback bodies larger than 1.25 MiB (1,310,720 bytes), leaving bounded JSON-envelope room
around WeChat's maximum 1 MiB ciphertext field.

## Mock fidelity and limitations

The stateful mock provides stable create idempotency, durable prepay generations, two-hour prepay
expiry metadata, query, close, signed success callbacks, callback loss/replay/tampering, and
close/pay race testing. Guarded scenario control supports `NOTPAY`, `SUCCESS`, and `CLOSED` only;
`PAYERROR` remains a provider-domain observation but cannot be injected by the mock control API.
Mock HMAC signatures and `MOCK-HMAC-SHA256` client parameters are intentionally unusable by
`wx.requestPayment`.

## Live-adapter gate

The mock gateway is local and DB-backed, but a live gateway introduces slow and fallible network
I/O. No live adapter may be enabled while service code holds payment or order row locks across a
provider call. Each outbound operation must instead:

1. lock briefly, validate current state, persist an idempotent operation claim, and commit;
2. perform the WeChat network request with no business-row locks held; and
3. re-lock the affected aggregate, revalidate state and claim ownership, then apply the provider
   result idempotently.

The live adapter must also add server-owned payer `openid`, outbound v3 signing, response signature
verification, public-key/certificate rotation, AES-256-GCM notification decryption, five-second
durable callback acknowledgement, retry/backoff/reconciliation, and secrets management.

Official contract references:

- JSAPI create: https://pay.wechatpay.cn/doc/v3/merchant/4012791856
- Mini Program client parameters: https://pay.wechatpay.cn/doc/v3/merchant/4012791898
- Query/close: https://pay.wechatpay.cn/doc/v3/merchant/4012791900 and https://pay.wechatpay.cn/doc/v3/merchant/4012791901
- Payment notification: https://pay.wechatpay.cn/doc/v3/merchant/4012791902
