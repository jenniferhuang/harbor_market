# Harbor Market WeChat Mini Program MVP tasks

## Browsing/cart MVP

- [x] Add a directly importable native WeChat DevTools project with committed `touristappid` and an
  uncommitted private AppID override path.
- [x] Add one validated, replaceable API-origin boundary with a localhost simulator default.
- [x] Add a connection-settings view that validates, tests, and persists a device-local API origin.
- [x] Add a `wx.request` wrapper that rejects non-2xx responses and malformed API envelopes.
- [x] Add catalog clients and normalization for categories, product lists, details, SKUs,
  specifications, and relative product-media URLs.
- [x] Add category/product browsing with loading, empty, error, retry, and unavailable states.
- [x] Add product detail and valid SKU/specification selection.
- [x] Add integer-fen formatting and a device-local cart with add, update, remove, clear, deduplicated
  line identity, quantity bounds, and safe storage recovery.
- [x] Ensure no Mini Program page invokes administrator routes, the payment mock, or
  `wx.requestPayment`.
- [x] Add deterministic tests for API-origin validation, HTTP/envelope errors, media URLs, catalog
  normalization, money formatting, and cart transitions.
- [x] Run the supported Node test/lint workflow.
- [ ] Perform a DevTools compile smoke test after the IDE login/service-port gate is cleared.
- [x] Document scope, architecture, local entry, access gates, and the future backend critical path.

## Preview access

- [ ] Import the project with `touristappid` and verify the local Mac DevTools simulator against
  `http://127.0.0.1:8080`.
- [ ] Have the user apply for an official Test AppID or provide developer access to the owned AppID.
- [ ] Configure the selected AppID privately and generate a developer-preview QR.
- [ ] For stable user acceptance, add the user's WeChat as an experience member, upload a version,
  set it as 体验版, and deliver its experience QR.

## Backend/admin critical path before checkout

- [ ] Implement server-side `wx.login`/`code2Session`, trusted OpenID mapping, and Harbor sessions.
- [ ] Implement a server-owned order/payable aggregate and inventory reservation lifecycle.
- [ ] Replace administrator-created mock attempts with an authenticated customer payment command
  whose owner, amount, currency, description, and OpenID come only from the order.
- [ ] Implement the live WeChat Pay v3 adapter with claim/network/re-lock orchestration,
  cryptographic notification handling, reconciliation, and credential rotation.
- [ ] Implement admin order fulfillment, cancellation, shipment, refund, audit, and payment-review
  workflows.
- [ ] Integrate WeChat order-delivery reporting and settlement-event reconciliation.

## Production release gates

- [ ] Register and certify the owned Mini Program under the `个体工商户`; configure truthful service
  categories and any required qualifications.
- [ ] Complete Mini Program filing and separately confirm the API hostname's ICP filing.
- [ ] Deploy the API on public HTTPS, validate its certificate/TLS, and configure WeChat legal
  domains without relying on the development bypass.
- [ ] Configure and approve the privacy-protection guide and consent flow for every personal-data
  feature actually used.
- [ ] Obtain Mini Program payment permission, bind and confirm `mchid` to the same AppID, and complete
  transaction-management authorization.
- [ ] Upload, test, submit for review, publish, and verify the production Mini Program entry.
