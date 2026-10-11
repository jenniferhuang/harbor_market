# Isolated fake buyer

These scripts are only for local testing. They use the normal backend services
with an injected WeChat identity and a separate SQLite database at
`.data/fake-buyer-ui/ui-harness.sqlite`. The harness ignores `.env` and inherited
application settings, disables payments, and binds only `127.0.0.1:18080`.
Neither script accepts a database, API URL, or host override.

From the repository root, start the harness in one terminal:

```sh
backend/.venv/bin/python backend/scripts/fake_buyer_harness.py
```

The port must be free before starting. Stop any previous test harness using that
port first. The normal development service can continue on its own port.
Fixtures are created or reused in the isolated database; favorites and coupon
claims remain available when the harness is reopened. An expired fixture coupon
is renewed for testing.

To use the fake buyer in WeChat DevTools, open the Mini Program project and set
its API origin in the simulator console:

```js
wx.setStorageSync('harbor_market_api_base_url', 'http://127.0.0.1:18080')
```

Recompile, then use the existing login dialog. The harness maps every login code
to the same test customer, so no real WeChat secret is needed by the backend.
The seed contains coffee, tea, a sold-out product, and a coupon for ¥2 off ¥10.
Large coffee with pearls and milk costs ¥7; two coffees plus one tea total ¥16.50,
or ¥14.50 with the coupon. Cart and login restoration use the simulator's local
storage; retain that storage when closing and reopening DevTools.

In another terminal, the real-HTTP module runner checks the current Mini Program
login dialog, favorite, SKU/addons, cart selection and thresholds, coupon claim,
authoritative quote, session restoration, and logout/relogin:

```sh
node miniprogram/scripts/fake-buyer-flow.cjs
```

It checks the isolated store marker before customer writes and uses real HTTP
responses. It simulates only WeChat platform APIs and page/component registration;
it does not assert native rendering. It starts with fresh local test storage and
supports an already-favorited product or already-claimed coupon. Do not run it
at the same time as another journey using this fake buyer.

The sanitized result is `.data/fake-buyer-ui/customer-flow-result.json`. Tokens
and raw authentication responses are never written to the result. Both the
database and result stay outside Git.

Syntax and lint checks can be run without starting the harness:

```sh
backend/.venv/bin/python -m py_compile backend/scripts/fake_buyer_harness.py
backend/.venv/bin/ruff check backend/scripts/fake_buyer_harness.py
node --check miniprogram/scripts/fake-buyer-flow.cjs
cd miniprogram
./node_modules/.bin/eslint scripts/fake-buyer-flow.cjs
```
