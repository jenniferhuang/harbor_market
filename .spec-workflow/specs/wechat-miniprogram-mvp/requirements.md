# Harbor Market WeChat Mini Program MVP requirements

## Purpose

This phase provides a native WeChat Mini Program storefront for anonymous catalog browsing and a
device-local cart. It consumes the existing public catalog API and creates the frontend boundary
needed for later WeChat identity, checkout, order, and live-payment work without pretending those
backend capabilities already exist.

The first deliverable is a locally runnable DevTools project. A phone QR is a separate environment
gate that requires an official Test AppID or Harbor Market's owned AppID.

## Functional requirements

### Catalog browsing

1. The Mini Program must list active public categories from
   `GET /api/v1/catalog/categories`.
2. It must list published products from `GET /api/v1/catalog/products`, support category filtering,
   and preserve the API's pagination contract.
3. It must show a product detail from `GET /api/v1/catalog/products/{product_code}`, including the
   public price, stock status, SKU choices, structured specifications, selling points, and available
   cover/gallery/detail media.
4. Relative media paths such as `/api/v1/media/...` must resolve against the configured API origin.
   The MinIO S3 endpoint must never be exposed directly to the Mini Program.
5. Pages must have explicit loading, empty, retryable-error, offline, and unavailable-product
   states. A successful `wx.request` callback is not enough: the client must validate HTTP status
   and the API response envelope.

### Device-local cart

6. A shopper must be able to add an active SKU, increase or decrease quantity, remove a line, and
   clear the cart.
7. Cart line identity must be based on stable product/SKU codes and selected option codes rather
   than names or array positions.
8. Cart amounts use integer fen internally and are formatted only for display. Floating-point yuan
   values must not become the cart's source of truth.
9. The cart is a convenience state owned by the device. It does not reserve inventory, create an
   order, or establish a payable amount. Displayed price and availability must be treated as stale
   until the future order service revalidates them server-side.
10. The cart must persist across page navigation and application restarts. Its device-storage
    document must be versioned, fail closed to an empty cart for corrupt or unsupported data, and
    never contain credentials or sensitive personal information.

### Runtime configuration and access modes

11. API calls must obtain their origin from one central client configuration. The development
    default is `http://127.0.0.1:8080`; callers may set another absolute `http://` or `https://`
    origin without changing catalog or page modules. A settings view must validate, connection-test,
    and persist a device-local override. No AppSecret, merchant credential, private key, or API v3
    key may appear in Mini Program code or configuration.
12. The committed DevTools project uses `touristappid` only as a zero-account local simulator
    entry. Tourist mode is not evidence that login, device preview, review, or payment works.
13. Phone preview requires either an official Test AppID or the owned Mini Program AppID and a
    WeChat account authorized for that project. Environment-specific AppIDs should override the
    committed tourist value through an uncommitted `project.private.config.json`.
14. A Test AppID may be used for development and phone preview, but it cannot replace the certified
    owned Mini Program identity required for production review and Mini Program payment.
15. Production and shared-device builds must use a public HTTPS API hostname configured as a
    WeChat `request` legal domain. A phone must not be configured with `127.0.0.1` or `localhost`.

### Scope and trust boundaries

16. This MVP must not expose the administrator-only payment mock to shoppers. Its
    `MOCK-HMAC-SHA256` client parameters are deliberately incompatible with `wx.requestPayment`.
17. This MVP must not invent customer checkout, order, inventory-reservation, WeChat-login,
    fulfillment, or refund APIs. UI that implies an order was accepted or paid is outside scope.
18. The source must be modular: platform/network behavior, catalog normalization, cart state,
    formatting, and pages must not be coupled into one module.
19. Domain utilities and API behavior must have deterministic automated tests that run without
    WeChat credentials. The project must lint and compile in the supported Node/DevTools workflow.

## Production gates

These are release requirements, not claims about the current MVP:

1. Register the owned Mini Program under the `个体工商户`, complete account certification, choose
   accurate service categories, and provide any category-specific qualifications.
2. Complete Mini Program filing in the WeChat Public Platform. This is distinct from the API
   hostname's ICP filing.
3. Serve the API through an ICP-filed HTTPS hostname with a valid trusted certificate and TLS,
   configure the required WeChat legal domains, and keep all WeChat server APIs and AppSecret use
   in the backend.
4. Publish an accurate user privacy protection guide. Declare each personal-information purpose,
   retention period, and contact channel; synchronize user agreement before invoking declared
   privacy APIs.
5. Upload, test, submit for WeChat review, and have an administrator publish the approved version.
6. Before live Mini Program payment, certify the account, obtain JSAPI/Mini Program payment
   permission, bind and confirm `mchid` to the same AppID, install API v3 credentials server-side,
   and implement the live provider adapter.
7. Because Harbor Market sells physical goods, implement WeChat order-delivery management and
   settlement synchronization. Missing shipment reporting can restrict production payment and
   settlement.

## Explicit non-goals

- WeChat login, OpenID/UnionID binding, customer profiles, or phone-number authorization
- Server-owned quotes, orders, inventory reservations, checkout, or order history
- Calling `wx.requestPayment` or enabling live WeChat Pay
- Shipping-address collection, fulfillment, refunds, or WeChat delivery reporting
- Changing the existing Vue admin panel or browser storefront
- Publishing a production Mini Program from the repository alone
