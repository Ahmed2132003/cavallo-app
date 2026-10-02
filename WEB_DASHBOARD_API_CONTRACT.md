# Web Dashboard API Contract: buying "Featured" status

**Audience:** a frontend engineer building the Web Dashboard with any technology (React, Vue, plain HTML, anything). You need no knowledge of this backend's internals. Everything below is plain HTTP + JSON.

**What the dashboard does:** a Business owner logs in, sees the purchasable plans, picks one, is redirected to the payment gateway's hosted checkout page, pays there, comes back, and sees "Featured until [date]".

**What is NOT in this document:** the dashboard UI itself and its tech stack (a separate project). Admin features. Anything about the mobile app.

---

## 1. Conventions

| Item | Value |
|---|---|
| Base URL (local development) | `http://localhost:8095` |
| Base URL (staging / production) | Ask the backend operator. Always HTTPS. |
| API prefix | `/api/v1/` |
| Request / response format | JSON. Send `Content-Type: application/json` on requests with a body. |
| Authenticated requests | Header `Authorization: Bearer <access_token>` |
| Trailing slash | **Required** on every URL below. |
| Dates | ISO-8601 UTC, for example `2026-11-01T12:30:45.123456Z` |
| Money | `price` is a **string** with 2 decimals (for example `"250.00"`). Do not parse it as a float for arithmetic. |
| Currencies | `EGP`, `SAR`, `AED`, `JOD` |

### Error format (every error, every endpoint)

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Invalid input.",
    "fields": { "plan_id": ["This field is required."] }
  }
}
```

`code` is stable and safe to branch on. `message` is for humans. `fields` is only filled for `VALIDATION_ERROR`; otherwise it is `{}`.

| HTTP | `code` | Meaning |
|---|---|---|
| 400 | `VALIDATION_ERROR` | Bad or missing input. See `fields`. |
| 401 | `AUTHENTICATION_FAILED` | Missing, invalid or expired access token, or wrong login credentials. |
| 403 | `PERMISSION_DENIED` | Logged in, but not allowed (for example a Customer account). |
| 404 | `NOT_FOUND` | Resource missing (for example no business profile yet). |
| 405 | `METHOD_NOT_ALLOWED` | Wrong HTTP method. |
| 429 | `THROTTLED` | Too many requests (login: 5 per minute). |
| 503 | `SERVICE_UNAVAILABLE` | Payment service down or not configured. Retry later. |

### CORS

A browser app on another origin only works if the backend operator has added the dashboard's origin to the backend setting `CORS_ALLOWED_ORIGINS` (staging / production). Local development allows all origins. If every browser call fails with a CORS error while `curl` works, this is the cause.

---

## 2. The whole flow at a glance

1. `POST /api/v1/auth/login/` -> store `access` and `refresh` tokens.
2. `GET /api/v1/auth/me/` -> check `account_type == "business"`.
3. `GET /api/v1/monetization/plans/` -> show the plans (can be done before login).
4. `GET /api/v1/businesses/me/` -> show the current state (`is_featured`, `featured_until`).
5. User picks a plan -> `POST /api/v1/payments/initiate/` with `{"plan_id": ...}` -> get `payment_url`.
6. Redirect the browser to `payment_url` (`window.location = payment_url`). The user pays on the gateway's page. Card data never touches your app or this backend.
7. The user returns to your site. **Activation is asynchronous** (see section 4): poll `GET /api/v1/businesses/me/` until `is_featured` is `true`.

---

## 3. Endpoints

### 3.1 Login

`POST /api/v1/auth/login/`, no authentication. This is the same login the mobile app uses; it is a standard REST endpoint and works from any client.

Request:
```json
{ "email": "owner@example.com", "password": "their-password" }
```

Response `200`:
```json
{ "access": "<jwt>", "refresh": "<jwt>" }
```

Errors: `400` (missing fields), `401 AUTHENTICATION_FAILED` (wrong email/password, or inactive account), `429 THROTTLED` (limit: 5 attempts per minute per client).

Token lifetimes (defaults, set by the operator): access token **15 minutes**, refresh token **14 days**.

### 3.2 Refresh the access token

`POST /api/v1/auth/refresh/`, no authentication.

Request: `{ "refresh": "<refresh token>" }`
Response `200`: `{ "access": "<new jwt>", "refresh": "<new jwt>" }`

Refresh tokens are **rotated**: the response contains a NEW refresh token and the one you just sent stops working. Always store the new one. Reusing an old refresh token returns `401`; send the user back to login.

When any authenticated call returns `401 AUTHENTICATION_FAILED`, refresh once and retry; if that also fails, go to login.

### 3.3 Logout

`POST /api/v1/auth/logout/`, authenticated. Request: `{ "refresh": "<refresh token>" }`. Response `200`: `{ "detail": "Successfully logged out." }`. The refresh token is blacklisted immediately. Also discard both tokens client-side.

### 3.4 Who am I

`GET /api/v1/auth/me/`, authenticated.

```json
{ "id": 12, "email": "owner@example.com", "account_type": "business", "is_moderator": false, "is_staff": false }
```

Only `account_type == "business"` accounts can buy a plan. A `"customer"` account gets `403` from the purchase endpoint, so use this to show a clear message early.

### 3.5 List plans (public)

`GET /api/v1/monetization/plans/`, **no authentication** (safe to call before login; an invalid `Authorization` header is ignored).

Response `200`, a plain JSON array (not paginated), ordered by `duration_days` then `id`:
```json
[
  { "id": 1, "name": "Featured 7 days",  "duration_days": 7,  "price": "80.00",  "currency": "EGP" },
  { "id": 2, "name": "Featured 30 days", "duration_days": 30, "price": "250.00", "currency": "EGP" }
]
```
An empty array `[]` means no plans have been created yet (the operator creates them in the backend's admin site).

### 3.6 Start a payment

`POST /api/v1/payments/initiate/`, **authenticated**, Business accounts only.

Request:
```json
{ "plan_id": 2 }
```
`plan_id` is an `id` from the plans list. That is the only input. The buyer is always the logged-in user's own business; any `business_id` you send is ignored.

Response `201`:
```json
{ "payment_url": "https://eg.checkout.paymob.com/?publicKey=...&clientSecret=..." }
```
Redirect the browser to `payment_url`. Treat it as an opaque URL: do not parse or store it. Each call creates a new pending payment attempt, so do not call it again on page reload; call it only when the user clicks "Pay".

| HTTP | `code` | When | What to show |
|---|---|---|---|
| 400 | `VALIDATION_ERROR` (`fields.plan_id`) | `plan_id` missing, not a number, or no such plan | Refresh the plan list |
| 401 | `AUTHENTICATION_FAILED` | Not logged in / token expired | Refresh token, else login |
| 403 | `PERMISSION_DENIED` | Customer account | "Only business accounts can buy a plan" |
| 404 | `NOT_FOUND` | Business account with no business profile yet | "Create your business profile in the app first" |
| 503 | `SERVICE_UNAVAILABLE` | Payment service down or not configured | "Try again later" |

### 3.7 Current Featured status

`GET /api/v1/businesses/me/`, authenticated. Returns the logged-in owner's business. `404 NOT_FOUND` if the account has no business profile yet.

```json
{
  "id": 5,
  "business_name": "Acme Trading",
  "business_type": "trader",
  "country": "EG",
  "city": "Cairo",
  "description": "",
  "category": null,
  "phone_number": "+201001234567",
  "is_verified": false,
  "follower_count": 0,
  "is_featured": true,
  "featured_until": "2026-11-01T12:30:45.123456Z"
}
```

The two fields that matter to the dashboard:

* `is_featured` (boolean): `true` while the business currently has Featured status.
* `featured_until` (string or `null`): when the active subscription ends. `null` when not featured. Show "Featured until [date]" from this.

Both are read-only and only appear on this owner endpoint (the public business profile and search results do not include them). Both can lag by up to one day after the real expiry moment, because expiry is processed by a daily job; treat them as "status as of the last daily job".

---

## 4. After the payment: activation is asynchronous

Paying on the gateway's page does **not** make the business Featured by itself. The gateway notifies the backend in the background (a webhook), the backend verifies it, and only then activates Featured status. A daily reconciliation job catches the rare case where that notification never arrives (up to about a day later).

Therefore, when the user comes back from the gateway:

1. **Do not** treat the redirect itself as proof of payment. A user can also close or cancel the payment page.
2. Poll `GET /api/v1/businesses/me/` every 3 to 5 seconds, for up to about 60 seconds, until `is_featured` is `true`.
3. If it becomes `true`: show success with `featured_until`.
4. If it is still `false` after the polling window: show "We are confirming your payment. If you completed it, Featured status will appear shortly" and let the user check again later. Do not tell them the payment failed (the dashboard cannot know). There is no payment-status endpoint.

**Where the user lands after paying:** the return URL is configured on the backend by the operator (setting `PAYMOB_REDIRECTION_URL`), not sent by the dashboard. Ask the operator to set it to the dashboard page that does the polling above. If unset, the gateway shows its own result page.

**Renewing:** buying again while already Featured is allowed. The new plan **replaces** the current one and starts at activation time; remaining days of the old plan are **not** added on. Consider warning the user in the UI when `is_featured` is already `true`.

---

## 5. Example session with curl

```bash
BASE=http://localhost:8095

# 1. Login
curl -s -X POST $BASE/api/v1/auth/login/ \
  -H "Content-Type: application/json" \
  -d '{"email":"owner@example.com","password":"their-password"}'
# -> {"access":"...","refresh":"..."}

# 2. Plans (no auth needed)
curl -s $BASE/api/v1/monetization/plans/

# 3. Start a payment (replace ACCESS and the plan id)
curl -s -X POST $BASE/api/v1/payments/initiate/ \
  -H "Authorization: Bearer ACCESS" \
  -H "Content-Type: application/json" \
  -d '{"plan_id": 2}'
# -> {"payment_url":"https://..."}   (open it in a browser to pay)

# 4. Status
curl -s $BASE/api/v1/businesses/me/ -H "Authorization: Bearer ACCESS"
```

On Windows PowerShell use `curl.exe` and escape the inner quotes of the JSON body (`'{\"plan_id\": 2}'`).

---

## 6. What the backend operator must have in place

The dashboard can be built and tested against the endpoints above, but a real purchase needs:

1. At least one **Plan** created in the backend's admin site (there are no default plans).
2. **Payment gateway credentials** configured on the backend. Until then, `POST /payments/initiate/` returns `503 SERVICE_UNAVAILABLE`.
3. The dashboard's origin in `CORS_ALLOWED_ORIGINS` (staging / production).
4. `PAYMOB_REDIRECTION_URL` pointing at the dashboard's post-payment page (optional but recommended).
5. HTTPS everywhere outside local development.

## 7. Security notes for the dashboard

* Never ask for or handle card data. Payment happens only on the gateway's hosted page.
* Keep tokens out of URLs and logs. Prefer in-memory storage for the access token.
* Do not trust any client-side state for "is featured"; always read it from `GET /api/v1/businesses/me/`.
