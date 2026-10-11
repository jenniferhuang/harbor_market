# Login and draft preview: which identity is needed?

**Clarification: the earlier owner-login statement applies to private draft access inside the native WeChat runtime. It does not mean customers must log in when they open the Mini Program.** Current behavior and proposed requirements are separated below. No login or preview feature is implemented by this document.

## Customer entry and browsing

Opening our Mini Program through search or an ordinary entry QR does not require an additional Harbor login to browse published goods. Our current public catalog APIs already allow anonymous requests, and the consumer client does not call `wx.login` or create a Harbor session. Entry into the app does not authorize access to private drafts.

- **Browse/search published goods:** Harbor login is unnecessary in the current client. Keep public browsing available in the proposed design.
- **Device-local cart:** Harbor login is unnecessary today; it is stored on that device and does not create a server order.
- **Future account, personal order history and checkout:** we propose requiring a verified customer identity/session for the relevant backend operations. Customer identity and commerce are still unimplemented. This is our proposed application requirement, not a requirement to block every Mini Program opening with a login screen.
- **Private draft reads:** authentication and the appropriate server-side permission are required. A customer session alone must not grant access to drafts.

## Confirmed current cart behavior and proposed delivery checkout

**Adding to the current cart does not require login.** It writes to local WeChat storage, not a customer account or server order. Keep guest browsing and guest cart in the proposed design. Require a verified customer session when saving a delivery address, starting checkout, placing an order or reading personal order history. Login need not interrupt the earlier browse/add-to-cart steps.

Proposed customer flow: Browse → add to guest cart → checkout → establish customer identity → select/save delivery address → server validates goods and shipping/total → confirm order → payment → shop fulfills shipment → customer views delivery status.

- Keep cart contents when the customer authenticates. Account-synchronized carts are optional future work; local cart contents can be submitted to checkout as product/SKU/options IDs and quantities.
- The backend derives the customer ID from the verified session. It saves addresses under that customer and checks ownership when an address is read, updated or selected for an order.
- A delivery address includes recipient name, contact phone, region/city and detailed street address, with postal code where needed. Save an address snapshot on the order so later address-book edits do not alter an existing shipment.
- The backend rechecks product publication, active SKU/options, current price and available stock, then calculates goods/shipping totals, reserves inventory and creates an order with an idempotent submission contract. Cached cart prices or stock are not authoritative.
- Authorized shop operators fulfill the order and update shipment/tracking information. The customer sees only their own orders and delivery details.
- **All customer identity, saved address, order, payment and delivery steps above remain TBD.** The current checkout action only explains that ordering is unavailable.

Possible customer identity mappings, address-book, order/order-line and shipment tables are proposed additions to PostgreSQL. They are not present in the current schema. MinIO continues to hold product image files; it is not the storage for customer addresses or orders.

[Current add-to-cart](/Users/jennifer.huang/Documents/AI_Workspace/Tools/harbor_market/miniprogram/src/pages/product/product.js:165), [local cart persistence](/Users/jennifer.huang/Documents/AI_Workspace/Tools/harbor_market/miniprogram/src/state/cart-store.js:123), [current checkout message](/Users/jennifer.huang/Documents/AI_Workspace/Tools/harbor_market/miniprogram/src/pages/cart/cart.js:124).

For a future WeChat identity integration with our self-hosted backend, the usual exchange is `wx.login` code → backend verifies with WeChat and obtains OpenID → backend creates its own application session. Knowing the WeChat identity and deciding whether that identity owns a shop are separate steps. See [Tencent CloudBase's explanation of self-hosted Mini Program identity](https://tcb.cloud.tencent.com/blog/2026/06/08/wechat-miniprogram-ai-skill).

[Anonymous catalog routes](/Users/jennifer.huang/Documents/AI_Workspace/Tools/harbor_market/backend/app/api/routes/catalog.py:34), [consumer requests](/Users/jennifer.huang/Documents/AI_Workspace/Tools/harbor_market/miniprogram/src/api/client.js:146), [current client scope](/Users/jennifer.huang/Documents/AI_Workspace/Tools/harbor_market/miniprogram/README.md:3).

## Alternative: shop-owner preview through the management UI

**A browser preview is another possible option using the existing administration login and backend draft APIs.** It does not need an owner login in the consumer Mini Program. The backend supplies the saved draft; a new browser frontend screen would render a mobile shopper-style preview. This alternative does not replace the requested native owner preview described below.

Proposed flow: Admin login → select saved draft → “Preview as Mini Program” → private mobile browser preview → owner publishes when ready.

- Reuse the authenticated admin session and existing admin product/image reads. The new preview screen is still TBD.
- Keep the product in `draft`, retain public catalog filtering, and serve preview responses privately. The screen is read only, with no real cart, ordering, payment or stock reservation.
- Match shopper presentation and rules, including active-SKU filtering. Admin serialization includes inactive SKUs today; a shopper preview must not display them simply because its source is an admin API.
- Existing draft access is available to every admin. If only the designated owner may preview among several admins, a server-enforced owner permission is an additional TBD; the current `is_admin` flag alone does not provide this isolation.

**This would be a browser layout preview, not native WeChat execution.** It can check saved product content and approximate mobile presentation. It cannot establish actual WeChat component/layout, device behavior or platform integration. Those need separate simulator/device verification.

[Authenticated draft API](/Users/jennifer.huang/Documents/AI_Workspace/Tools/harbor_market/backend/app/api/routes/admin_catalog.py:370), [cookie/session check](/Users/jennifer.huang/Documents/AI_Workspace/Tools/harbor_market/backend/app/api/dependencies.py:45), [admin draft images](/Users/jennifer.huang/Documents/AI_Workspace/Tools/harbor_market/backend/app/api/routes/catalog.py:96), [shopper SKU filtering](/Users/jennifer.huang/Documents/AI_Workspace/Tools/harbor_market/backend/app/services/catalog.py:264).

## Requested owner preview inside the actual Mini Program — design only

**Yes: an identified and authorized shop owner can be given a private native draft-preview mode.** The backend would return saved draft data and protected images to that owner while ordinary consumers continue to receive only published goods. This is the requested native preview design; it is not restricted to a browser imitation. The feature is still unimplemented.

Proposed owner flow: Open Mini Program → establish verified WeChat/Harbor identity → backend checks its trusted owner-role/account binding and product scope → open draft Preview → authenticated preview API returns draft content and images → render the real Mini Program product page in read-only preview mode.

- The WeChat account must be linked through trusted setup to an authorized shop operator. Identifying a WeChat user does not make that user an owner; the backend checks the binding and permissions. The client cannot self-declare an owner role.
- Use a separate authenticated preview read path. Keep the ordinary public catalog published-only; a `preview=1` flag or QR parameter is never sufficient authorization.
- Protect every draft record and image read. Return preview responses privately and keep MinIO private. The current native client does not inherit the browser's admin cookie; native session and media authorization must be explicitly implemented.
- Keep goods in `draft`, identify the screen as Private draft preview, follow shopper active-SKU/display rules and disable real cart, order, payment and reservation actions. Viewing a draft does not publish it.
- Current browser draft APIs allow all active admins. Strictly limiting native preview to the designated owner or that owner's shop/products requires the new server-side role/scope rules. Those rules do not exist yet.

**Separate design workstreams:** keep guest browse/cart available; add customer identity/address/order/delivery flows; add verified native owner draft preview. Native preview depends on owner identity and product/media permissions, not on consumer checkout or delivery being implemented first. The browser preview described above remains another possible preflight UI. This documentation does not implement any of these additions.
