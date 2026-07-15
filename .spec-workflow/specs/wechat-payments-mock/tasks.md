# Harbor Market mock-first payment tasks

- [x] Define provider-neutral payment DTOs and gateway protocol.
- [x] Implement explicit canonical state policy with authoritative late-success handling.
- [x] Implement a production-blocked, signed, DB-backed durable WeChat Pay mock.
- [x] Add payment attempt, state history, and provider inbox models; retain normalized envelopes for
  valid matched and unmatched callbacks without storing raw plaintext bodies.
- [x] Add Alembic migration with idempotency and active-attempt uniqueness constraints; retain and
  flag multiple provider-confirmed successes instead of rejecting financial truth.
- [x] Add administrator-only mock creation, query, close, reconcile, and scenario endpoints.
- [x] Add signed callback verification, invariant checking, and provider-event deduplication.
- [x] Add unit/integration tests for transitions, retries, callback loss/replay/tamper, amount
  mismatch, and close/pay races.
- [x] Limit callback bodies to 1.25 MiB (including bounded envelope room) and restrict mock state
  injection to `NOTPAY`/`SUCCESS`/`CLOSED`.
- [x] Keep production disabled and document safe local mock configuration.
- [ ] Add server-owned order/payable snapshots and inventory reservation.
- [ ] Add WeChat identity mapping and derive `openid` server-side.
- [ ] Before enabling live payments, implement claim/network/re-lock orchestration so no provider
  network call runs while payment or order rows are locked.
- [ ] Implement and certify the live WeChat Pay v3 adapter after merchant onboarding and completion
  of the lock-free network-call gate.
- [ ] Implement the separate refund aggregate and fulfillment/delivery reporting.
