# Harbor Market WeChat Mini Program MVP design

## System boundary

```text
WeChat Mini Program
  pages/components
        |
        v
  catalog domain + local cart state
        |
        v
  API client / wx platform adapter
        |
        v
Harbor Market public API
  /api/v1/catalog/*
  /api/v1/media/*
```

The Mini Program is an additional consumer of the existing public catalog. It has no direct MinIO,
PostgreSQL, administrator, or payment-provider access. The backend remains authoritative for public
catalog visibility; the future order service will remain authoritative for price, inventory, and
payable amount.

## Source organization

The project is a native CommonJS Mini Program that can be imported directly into WeChat DevTools.
The implementation separates:

- `src/api/client.js` — API-origin validation, `wx.request` transport, envelope/status handling,
  and absolute media URL construction;
- `src/api/catalog.js` — public category/product endpoints;
- `src/domain/catalog.js` — catalog normalization and product/SKU selection helpers;
- `src/state/cart-store.js` — device-local cart transitions and totals;
- `src/utils/money.js` — integer-fen display formatting;
- home/product/cart pages plus a connection-settings page — view lifecycle, rendering, and user
  interaction only; and
- `tests/` — credential-free Node tests for the transport boundary and pure domain/state modules.

Pages may consume those modules, but API modules must not import pages and domain/state modules must
not depend on DevTools globals when a small injected boundary is sufficient for testing.

## Runtime modes

| Mode | AppID | API origin | Access | Limitations |
|---|---|---|---|---|
| Local simulator | committed `touristappid` | `http://127.0.0.1:8080` | DevTools Compile | No owned identity, phone QR, review, or payment proof |
| Test preview | official Test AppID | LAN debug URL or public test HTTPS URL | DevTools preview QR | Development only; not a certified payment identity |
| Owned development/experience | Harbor Market AppID | public HTTPS test URL | developer or experience QR | Requires account membership and configured legal domain |
| Production | same certified Harbor Market AppID | ICP-filed public HTTPS URL | search/production Mini Program code | Filing, privacy, category, review, payment, and delivery gates apply |

The real or Test AppID belongs in uncommitted `project.private.config.json`; `project.config.json`
remains safe for a fresh contributor to import with `touristappid`. AppID itself is not a signing
secret, but keeping environment selection private avoids accidental upload under the wrong app.
AppSecret and all payment material are backend secrets and must never be added to either file.

## API-origin contract

The client holds one normalized absolute origin. It defaults to `http://127.0.0.1:8080` for the Mac
DevTools simulator and may be replaced through `setApiBaseUrl(...)`. A validated override is stored
through `wx` storage and recovered safely on later launches. Callers provide the origin only, without
`/api/v1`; endpoint modules append their API paths.

Examples:

```js
setApiBaseUrl("http://127.0.0.1:8080");
setApiBaseUrl("https://api.harbor-market.example");
```

Rules:

- accept only absolute `http:` or `https:` origins and normalize the trailing slash;
- do not silently fall back to production if configuration is invalid;
- fail closed to the localhost development origin when a stored override is corrupt;
- concatenate endpoint paths without double slashes;
- prefix relative `/api/v1/media/...` values with the same origin, while retaining valid absolute
  media URLs; and
- reject non-2xx HTTP responses and malformed `{data: ...}` envelopes even when `wx.request` calls
  its `success` callback.

`127.0.0.1` in a physical-device build refers to the phone, not the Mac. A same-LAN address may be
used only for deliberate debug testing with domain verification disabled in DevTools and debugging
enabled on the device. A shared or production QR must use the public HTTPS hostname.

## Catalog model

The Mini Program consumes only published products and active categories returned by the backend.
Stable category, product, and SKU codes are integration identities. Human-readable names and list
indexes are presentation data.

The product detail resolves the selected SKU from structured attributes and specifications. It must
not synthesize a purchasable combination absent from the backend SKU list. Stock and prices are
display snapshots only. An archived/unpublished product or unavailable media is handled as a normal
unavailable state, not by reaching into administrator APIs.

## Cart model

A cart line contains the minimum stable selection identity plus a sanitized display snapshot:

```text
product_code + sku_code + selected option codes
quantity
display name/image/price snapshot
```

All arithmetic uses integer fen. Adding the same selection increments its quantity; a different SKU
or option set creates a different line. Quantity is bounded to a reasonable positive integer.
Corrupt or unknown persisted lines are discarded safely.

Cart subtotal is explicitly advisory. The future backend order command must reload the SKU, validate
availability, calculate a canonical payable snapshot, reserve inventory, and return any changes for
user confirmation before payment creation.

## Payment boundary

The current backend mock is an administrator-only payment-state exerciser. Its client parameters use
`MOCK-HMAC-SHA256`, not the RSA contract accepted by `wx.requestPayment`. The browsing/cart MVP must
not call the mock, display a fake WeChat cashier, or change order state from a client callback.

After merchant onboarding, the production path is:

```text
wx.login code -> Harbor backend -> code2Session -> trusted OpenID mapping
cart -> server quote/order + inventory reservation
order -> customer payment endpoint -> WeChat JSAPI prepay
RSA client parameters -> wx.requestPayment
verified callback/query -> authoritative payment and order transition
```

The Mini Program return callback is only presentation feedback. It should lead to a backend refresh;
it never proves settlement.

## Future backend and admin critical path

1. **WeChat identity:** exchange one-time `wx.login` codes server-side, map OpenID to a Harbor user,
   issue Harbor's own session, and never expose AppSecret or `session_key`.
2. **Order/payable aggregate:** persist item/SKU/price/address snapshots, idempotent commands, stock
   reservation/expiry/release, and explicit order transitions separate from payment state.
3. **Customer payment command:** derive owner, amount, currency, description, and payer OpenID from
   server records. Add the live v3 adapter using claim/network/re-lock so provider I/O never runs
   while payment/order rows are locked.
4. **Admin fulfillment:** searchable orders, payment review, cancellation, picking, shipment, refund,
   customer-service notes, and append-only audit events with role checks.
5. **Delivery and settlement:** confirm transaction-management authorization, upload shipment data
   through `/wxa/sec/order/upload_shipping_info`, query delivery state, accept settlement events,
   retry safely, and expose discrepancies to operators.
6. **Refund aggregate:** represent requested/processing/succeeded/failed and partial refunds separately
   from payment and order status.
7. **Production operations:** secret rotation, notification verification/decryption, reconciliation,
   observability, HTTPS/domain configuration, privacy operations, filing, review, and release.

## Access-entry lifecycle

1. **Tourist local entry:** import `miniprogram/` in DevTools and compile with `touristappid`.
2. **Developer preview:** apply for a Test AppID or use the owned AppID, authorize the developer,
   replace AppID privately, compile, and use DevTools Preview to generate a QR.
3. **Experience entry:** upload with the owned AppID, select the development version as 体验版 in
   the Mini Program backend, add experience members, and distribute its experience QR.
4. **Public entry:** submit the uploaded version for review; after approval an administrator publishes
   it. Users can then enter through search or the production Mini Program code.

## Official contract references

- [Getting started and AppID](https://developers.weixin.qq.com/miniprogram/dev/framework/quickstart/getstart.html)
- [DevTools project configuration](https://developers.weixin.qq.com/miniprogram/dev/devtools/projectconfig)
- [Official Test AppID](https://developers.weixin.qq.com/miniprogram/dev/devtools/sandbox.html)
- [Network and legal-domain requirements](https://developers.weixin.qq.com/miniprogram/dev/framework/ability/network.html)
- [Mini Program login](https://developers.weixin.qq.com/miniprogram/dev/framework/open-ability/login.html)
- [Server-side code2Session](https://developers.weixin.qq.com/miniprogram/dev/server/API/user-login/api_code2session.html)
- [Privacy-protection guide](https://developers.weixin.qq.com/miniprogram/dev/framework/user-privacy/PrivacyAuthorize.html)
- [Preview, upload, review, and publish](https://developers.weixin.qq.com/miniprogram/dev/framework/quickstart/release.html)
- [Mini Program payment onboarding](https://pay.wechatpay.cn/doc/v3/merchant/4015459512)
- [Mini Program payment invocation](https://pay.wechatpay.cn/doc/v3/merchant/4012791898)
- [Physical-goods transaction rules](https://developers.weixin.qq.com/miniprogram/product/jiaoyilei/yunyingguifan.html)
- [Order-delivery management](https://developers.weixin.qq.com/miniprogram/dev/platform-capabilities/business-capabilities/order-shipping/order-shipping.html)
- [Tencent filing FAQ](https://cloud.tencent.com/document/faq/243/97691)
