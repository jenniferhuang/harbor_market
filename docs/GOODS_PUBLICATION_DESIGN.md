# Goods publication and private owner preflight

**Status: current behavior documented; private shopper preflight is DESIGN ONLY / NOT IMPLEMENTED.** This review reads the current repository. It does not publish goods, change application code, or assert a fresh runtime check. The Live DB & Storage tab retains its separately dated observation.

## 1. How do goods become visible to customers?

1. The shop operator signs in to the Vue administration UI and creates a product. New products start as `draft`.
2. The operator saves its category, description, price, SKUs and specifications, then uploads images. PostgreSQL stores business records and image object keys; private MinIO stores the image bytes.
3. In the current editor, select `published` and Save. There is no separate Publish button or reviewer approval step today.
4. The shared FastAPI backend validates the product and commits its record. Publication requires an active category and exactly one cover image. Product validation also requires exactly one active default SKU, valid SKU/specification contracts, nonnegative prices/stock and market price at least base price. Positive stock is not a publication requirement.
5. The consumer Mini Program calls the public catalog. Only `published` products with an active category are returned. Images are read through the backend media route, which checks visibility before reading MinIO.

[Admin Save](/Users/jennifer.huang/Documents/AI_Workspace/Tools/harbor_market/frontend/src/views/ProductsAdminView.vue:518), [backend commit](/Users/jennifer.huang/Documents/AI_Workspace/Tools/harbor_market/backend/app/services/catalog.py:219), [publish checks](/Users/jennifer.huang/Documents/AI_Workspace/Tools/harbor_market/backend/app/services/catalog.py:384), [public filter](/Users/jennifer.huang/Documents/AI_Workspace/Tools/harbor_market/backend/app/services/catalog.py:135), [private media boundary](/Users/jennifer.huang/Documents/AI_Workspace/Tools/harbor_market/backend/app/api/routes/catalog.py:76).

## 2. Does publishing require redeployment?

**No. Goods publication changes data.** It does not rebuild the backend or Mini Program. Customers see the committed change on their next server fetch. Application code/layout changes follow a separate application release process.

The current Mini Program loads catalog/detail data when pages first load and when the customer pulls down to refresh. It does not push goods changes to an already open page. Its cart contains device-local snapshots and does not revalidate price/stock on returning to the cart; future checkout must obtain authoritative values from the backend.

[Home refresh](/Users/jennifer.huang/Documents/AI_Workspace/Tools/harbor_market/miniprogram/src/pages/home/home.js:68), [detail refresh](/Users/jennifer.huang/Documents/AI_Workspace/Tools/harbor_market/miniprogram/src/pages/product/product.js:81), [local cart](/Users/jennifer.huang/Documents/AI_Workspace/Tools/harbor_market/miniprogram/src/pages/cart/cart.js:40).

## 3. Where can the shop owner preview today?

**Implemented: saved draft fields and uploaded images in the admin editor.** An authenticated administrator can open any saved draft. Draft images use `private, no-store`; anonymous or ordinary users receive 404. This is admin access: every admin qualifies, because products have no owner/creator field.

**Not implemented: a private complete shopper-layout preview**, a draft-aware native Mini Program page, preview QR/grant, or owner-specific permission. A Mini Program simulator or tester build still calls the public catalog; tester access does not make production drafts visible. A separate test backend can contain published test goods, but this is a test environment, not a production owner-only preview.

[Editor image previews](/Users/jennifer.huang/Documents/AI_Workspace/Tools/harbor_market/frontend/src/views/ProductsAdminView.vue:1416), [admin product reads](/Users/jennifer.huang/Documents/AI_Workspace/Tools/harbor_market/backend/app/api/routes/admin_catalog.py:370), [media privacy](/Users/jennifer.huang/Documents/AI_Workspace/Tools/harbor_market/backend/app/api/routes/catalog.py:96), [current identity model](/Users/jennifer.huang/Documents/AI_Workspace/Tools/harbor_market/backend/app/models/user.py:25).

## 4. What statuses are available?

- `draft` — saved, hidden from public catalog/detail; admins can edit it.
- `published` — visible on public fetch only while its category is active.
- `archived` — hidden from public catalog/detail; it can be edited and republished subject to validation.
- `preview` — **not an existing status**. The proposal treats preview as a private viewing mode while goods remain draft.

Changing published goods to draft or archived removes public visibility on subsequent API reads. Disabling their category also hides them, even if their stored status is still published. Stock availability is a separate dimension; zero-stock goods can remain published.

[Status selector](/Users/jennifer.huang/Documents/AI_Workspace/Tools/harbor_market/frontend/src/views/ProductsAdminView.vue:1224), [status constraints](/Users/jennifer.huang/Documents/AI_Workspace/Tools/harbor_market/backend/app/models/product.py:135).

## 5. Proposed private preflight — not provided yet

**Desired experience:** Save draft → open private shopper preview → inspect/check readiness → owner selects Publish → server validates and commits → consumers fetch it. No second person reviews or approves the goods in this proposed workflow. Automated validation still runs.

- **Keep it private:** a future server-side permission must identify the designated shop owner and authorize that product on every preview and image read. The current `is_admin` flag alone cannot guarantee owner-only access among multiple admins. Deny guests and other accounts. Keep public catalog filtering unchanged; never temporarily publish a product to preview it.
- **Show the saved version:** open a read-only mobile shopper layout in a new browser tab or side panel, with a clear “Private draft preview” banner. Show the actual saved title, images, SKU/options, price, stock display and description. Cart, ordering, payment and reservation actions stay disabled. Serve preview responses and media as `private, no-store`.
- **Make readiness visible:** reuse existing publication validators and add a proposed readiness display for missing fields, media-read errors and invalid SKU/options. Zero stock can be a visible advisory rather than an invented publication blocker.
- **Publish deliberately:** add a proposed explicit Publish action. Revalidate on the server and guard against the draft changing after preview, using a version/concurrency contract that still needs implementation. On failure, keep the product private and show the failed check.
- **Native preview is a later stage:** the current consumer Mini Program has no owner login. A future native draft preview needs server-side owner identity and scoped authorization for data and media. A WeChat preview QR by itself is insufficient. No preview API, token or permission is being implemented in this documentation change.

## Already-live goods need a separate revision design

**Current limitation:** saving edits to a published product immediately updates its live record. Changing it to draft hides the whole listing. There is no separate unpublished revision that preserves the previous public version.

**Proposed later stage:** store a working draft revision separately from the published snapshot. The owner previews that revision privately; Publish validates and switches the active published revision atomically. A possible `product_revisions` table and `published_revision_id` are design ideas only: neither exists in the current schema. Media visibility must follow the selected published revision. Retention and cleanup must preserve objects referenced by either draft or live snapshots.

## Design completion criteria

- Guests and other accounts cannot fetch preview records or preview images, including direct URLs.
- Draft preview does not create a public catalog listing or an order/payment/inventory mutation.
- A failed or stale-version publication keeps the draft private; a successful publication appears on the next consumer API fetch without code redeployment.
- A future live-revision workflow keeps the previous listing public until the owner explicitly publishes its replacement.
- Current editor/image preview, planned shopper preview and future native preview remain clearly distinguished in the UI and documentation.
