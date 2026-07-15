# Harbor Market WeChat Mini Program

This native Mini Program is the consumer browsing/cart MVP for Harbor Market. It reads the existing
public category, product, and media APIs and maintains a device-local cart. It does not yet log a
customer in, reserve inventory, create an order, collect a shipping address, or invoke WeChat Pay.

## Current scope

- Browse active categories and published products.
- Filter and page through the catalog.
- View product media, details, SKUs, specifications, price, and stock display state.
- Select a valid SKU/options combination.
- Add, update, remove, and clear local cart lines using integer-fen arithmetic.

The cart subtotal is informational. Only the future backend order service may confirm current price,
stock, and payable amount.

## Prerequisites

- [WeChat DevTools](https://developers.weixin.qq.com/miniprogram/dev/devtools/download)
- Node.js 22 for repository tests and lint
- Harbor Market running locally or through a reachable HTTPS test hostname

Start the local Harbor Market stack from the repository root:

```bash
cp .env.example .env
docker compose up --build -d
curl --fail http://127.0.0.1:8080/api/v1/health
```

## Local DevTools entry with `touristappid`

1. Open WeChat DevTools and select **Import Project**.
2. Choose this `miniprogram/` directory, which contains `project.config.json`.
3. Keep the committed `touristappid` for the first local simulator run.
4. Confirm Harbor Market is reachable at `http://127.0.0.1:8080`.
5. Select **Compile**.

`touristappid` is a zero-account local entry, not an owned Mini Program identity. It is suitable for
the DevTools simulator but cannot establish that phone preview, WeChat login, review, or payment is
ready.

## API base URL

The client defaults to the origin `http://127.0.0.1:8080`. To use another backend, open the Mini
Program's **设置 → 连接设置**, enter an absolute HTTP(S) origin without `/api/v1`, select **测试连接**,
and then select **保存地址**. The validated value is stored on the current device.

The programmatic equivalent uses the central API client before any request, for example from
`src/app.js`:

```js
const { setApiBaseUrl } = require("./api/client");

App({
  onLaunch() {
    setApiBaseUrl("https://api.harbor-market.example");
  },
});
```

Catalog modules append `/api/v1/catalog/...`. Relative backend media paths such as
`/api/v1/media/...` are resolved against the same origin. A validated override is persisted through
WeChat storage so it survives restarts; malformed stored configuration falls back safely to the
local development origin.

Environment guidance:

| Environment | Example origin | Notes |
|---|---|---|
| Mac DevTools simulator | `http://127.0.0.1:8080` | Local default |
| Same-LAN phone debug | `http://192.168.x.x:8080` | Temporary debug only; phone and Mac must share the LAN |
| Shared test/experience | `https://test-api.example.cn` | Must be reachable by the phone and configured as a legal domain |
| Production | `https://api.example.cn` | Must meet HTTPS, certificate, ICP, and WeChat-domain requirements |

On a phone, `127.0.0.1` and `localhost` point to the phone, not the Mac. DevTools can temporarily
disable domain/TLS verification and a phone can enable debugging for development, but a shared or
production QR must not depend on that bypass. See WeChat's
[network requirements](https://developers.weixin.qq.com/miniprogram/dev/framework/ability/network.html).

Do not put the Mini Program AppSecret, WeChat Pay private key, API v3 key, merchant certificate, or
any other server credential in this project. The AppSecret belongs only in backend secret storage.

## Test AppID and developer preview QR

The official Test AppID path is the fastest phone entry before Harbor Market's owned account is
ready:

1. Open the [Test AppID application](https://mp.weixin.qq.com/wxamp/sandbox?doc=1) and scan with the
   user's WeChat.
2. Follow the [official Test AppID guide](https://developers.weixin.qq.com/miniprogram/dev/devtools/sandbox.html).
3. Create an uncommitted `project.private.config.json` beside `project.config.json`:

   ```json
   {
     "appid": "wx_test_or_real_appid"
   }
   ```

4. Sign in to DevTools with the authorized WeChat account, compile, and select **Preview**.
5. Scan the generated QR with an authorized developer account.

Keep `project.private.config.json` out of Git. A Test AppID supports development/device preview, but
it is not the certified owned AppID required for production Mini Program payment.

## Owned AppID, experience QR, and public entry

For a stable pre-release entry:

1. [Register Harbor Market's Mini Program](https://mp.weixin.qq.com/wxopen/waregister?action=step1)
   under the `个体工商户` and complete account certification.
2. Add the working WeChat accounts as developers and the intended testers as experience members.
3. Put the owned AppID in `project.private.config.json` and use the public HTTPS test API origin.
4. Upload a tested build from DevTools.
5. In the Mini Program backend, select the uploaded development version as 体验版.
6. Send testers the experience QR.

For public access, submit the uploaded version for review. After approval, an administrator must
publish it. Users can then find Harbor Market through WeChat search or its production Mini Program
code. See the official [preview/upload/release flow](https://developers.weixin.qq.com/miniprogram/dev/framework/quickstart/release.html).

Optional automated preview/upload can use
[miniprogram-ci](https://developers.weixin.qq.com/miniprogram/dev/devtools/ci.html). It requires an
AppID and a code-upload private key generated in the Mini Program backend. Configure its upload IP
allowlist as well; WeChat recommends enabling that protection. Treat the private key as a secret and
never commit it.

## Tests

From this directory, using Node.js 22:

```bash
npm ci
npm test
npm run lint
```

These commands validate the credential-free API, catalog, money, and cart modules. A successful Node
suite does not replace a DevTools compile and phone-preview smoke test.

## Production checklist

- Owned account registered and certified under the correct subject.
- Accurate service category and any required category qualifications approved.
- Mini Program filing completed in WeChat Public Platform.
- API hostname separately ICP-filed and served through valid public HTTPS/TLS.
- API origin added to the required WeChat legal domains; enable URL/domain checking for the release
  build and do not rely on the development bypass.
- User privacy protection guide accurately declares data, purposes, retention, and contact details.
- Uploaded code passes device testing, WeChat review, and administrator publication.
- Before live payment: JSAPI/Mini Program payment permission approved, `mchid` bound and confirmed
  against the same AppID, and all API v3 credentials kept in the backend.
- Before selling physical goods: transaction-management authorization and WeChat order-delivery
  reporting integrated with fulfillment and settlement reconciliation.

Useful official references:

- [Privacy authorization](https://developers.weixin.qq.com/miniprogram/dev/framework/user-privacy/PrivacyAuthorize.html)
- [Mini Program filing guidance](https://cloud.tencent.com/document/faq/243/97691)
- [Current Mini Program payment onboarding](https://pay.wechatpay.cn/doc/v3/merchant/4015459512)
- [Physical-goods transaction rules](https://developers.weixin.qq.com/miniprogram/product/jiaoyilei/yunyingguifan.html)
- [Order-delivery management](https://developers.weixin.qq.com/miniprogram/dev/platform-capabilities/business-capabilities/order-shipping/order-shipping.html)

## Backend critical path before checkout

The next backend/admin sequence is:

1. Server-side `wx.login`/`code2Session` and trusted OpenID mapping.
2. Server-owned order/payable snapshots and inventory reservation/expiry/release.
3. Authenticated customer payment creation derived only from the order.
4. Live WeChat Pay v3 provider adapter and verified callback/query reconciliation.
5. Admin picking, shipment, cancellation, refund, and payment-review workflows.
6. WeChat shipment upload, delivery state, and settlement-event reconciliation.

The detailed requirements and state boundaries are in
`../.spec-workflow/specs/wechat-miniprogram-mvp/`.
