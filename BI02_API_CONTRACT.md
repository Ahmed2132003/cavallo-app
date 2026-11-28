# BI-02 API Contract: Product Funnel, Content Analytics, Dashboard

Base: `/api/v1/analytics/`. JSON. Errors use the standard envelope
`{"error": {"code", "message", "fields"}}`.

## Conventions
- Auth: all dashboard endpoints need a Bearer JWT of the OWNER of
  business `{id}`. Check order: 401 -> 404 (no such business) -> 403
  (not the owner or a Customer) -> 400 (bad query). `business_id` in the
  query or body is ignored.
- Dates: `date_from`, `date_to` = inclusive UTC dates `YYYY-MM-DD`.
  `period` = `7d|30d|90d`, used only when `date_from` is missing (window
  ending at `date_to`). Default `period` = `30d`. Default `date_to` = the
  last rolled-up day, else yesterday (UTC). Max range 366 days.
  Bad range -> 400 `VALIDATION_ERROR` with `fields.date_from`.
- Missing data is `null`, never 0. View/engagement numbers are `null`
  when no day of the range was rolled up. Order and interest numbers are
  real counts.
- Money: strings with 2 decimals, always per currency.
- Rates: fractions rounded to 4 places (0.25 = 25%), `null` when the
  denominator is 0 or data is missing.
- Timestamps: ISO-8601 UTC with `Z`.
- All endpoints except the two POSTs are GET only (others -> 405).

## Internal vs external data
- Automatic (internal): views, unique views, interests, orders, sales,
  likes, comments, saves, in-app shares. Collected by the platform.
- Manual (external): numbers from Instagram/Facebook/TikTok. The owner
  types them in; each carries `source` and `last_updated`. Never merged
  with internal numbers. Reach is not defined.

## coverage (in every rollup-based response)
```json
{"days_in_range": 2, "rollup_days": 2, "views_available": true, "views_complete": true}
```

## 1. Record a view
`POST /analytics/events/views/` (logged in or anonymous, throttled)

Request:
```json
{
  "event_id": "8f14e45f-ceea-4b3a-9a1d-5c6f1c2d3e4f",
  "target_type": "product",
  "target_id": 12,
  "occurred_at": "2026-03-10T12:00:00Z",
  "watch_ms": 4200,
  "anon_id": "install-0123456789abcdef"
}
```
- `event_id` UUID from the client (retries never double count).
- `target_type`: `product|post|reel`. `target_id` integer >= 1.
- `occurred_at` optional, not older than 48h, not more than 5 min ahead.
- `watch_ms` only for reels (0..86400000). Reel views under 3000 ms are ignored.
- `anon_id` required for anonymous callers (16-128 chars `[A-Za-z0-9._:-]`),
  ignored for logged-in callers.
- Call when the DETAIL screen opens or the video starts, never when an item
  merely appears in a list.

Responses:
- 201 `{"status": "recorded", "event_id": "..."}`
- 200 `{"status": "duplicate", "event_id": "..."}`
- 200 `{"status": "ignored", "reason": "below_min_watch_ms", "min_watch_ms": 3000}`
- 400 validation, 403 owner viewing own item, 404 target missing/not active,
  429 throttled (`THROTTLED`).

## 2. Overview
`GET /business/{id}/overview/?date_from&date_to&period`
```json
{
  "business_id": 7,
  "period": {"date_from": "2026-03-10", "date_to": "2026-03-11", "days": 2, "period": "custom"},
  "overview": {
    "product_views": 19, "product_unique_views": 13,
    "interests": 3, "interested_customers": 3,
    "orders": 5, "pending_orders": 1, "confirmed_orders": 3,
    "completed_orders": 1, "cancelled_orders": 1, "units": 4,
    "sales": [{"currency": "EGP", "sales_value": "450.00", "sold_orders": 3, "units": 4, "aov": "150.00"}],
    "content_views": 18, "content_unique_views": 5,
    "likes": 5, "comments": 1, "saves": 1, "in_app_shares": 1
  },
  "funnel": {
    "unit_note": "views = unique views (viewer per item per UTC day); the other stages are distinct customers. Rates are stage / previous stage (fraction), null when the previous stage is 0 or view coverage is incomplete.",
    "stages": [
      {"key": "views", "count": 13}, {"key": "interests", "count": 3},
      {"key": "orders", "count": 3}, {"key": "confirmed", "count": 3},
      {"key": "completed", "count": 1}
    ],
    "conversions": [
      {"from": "views", "to": "interests", "rate": 0.2308},
      {"from": "interests", "to": "orders", "rate": 1.0},
      {"from": "orders", "to": "confirmed", "rate": 1.0},
      {"from": "confirmed", "to": "completed", "rate": 0.3333}
    ],
    "overall_rate": 0.0769
  },
  "previous_period": null,
  "coverage": {"days_in_range": 2, "rollup_days": 2, "views_available": true, "views_complete": true},
  "data_updated_at": "2026-03-12T00:15:04Z",
  "orders_data_updated_at": "2026-03-11T15:20:00Z"
}
```
`previous_period` is `null` unless both windows are fully covered. When
present: `{"period": {...}, "overview": {...}, "funnel": {...}, "coverage": {...}, "data_updated_at": "...", "deltas": {"orders": {"current": 5, "previous": 2, "change": 3, "change_pct": 1.5}, ...}}`
(one delta per scalar key; `change_pct` is `null` on a zero base).
When no view data exists: view fields are `null`, `funnel.stages[0].count`
is `null`, `overall_rate` is `null`.

## 3. Products
`GET /business/{id}/products/?ordering&page&page_size&date_from&date_to&period`
- `ordering`: `views|unique_views|interests|orders|sales|conversion`, prefix
  `-` for descending. Default `-views`. Rows with `null` sort last.
- `page` >= 1 (default 1), `page_size` 1..100 (default 20).

```json
{
  "business_id": 7,
  "period": {"date_from": "2026-03-10", "date_to": "2026-03-11", "days": 2, "period": "custom"},
  "count": 2, "page": 1, "page_size": 20, "total_pages": 1,
  "results": [{
    "product_id": 12, "name": "Leather bag", "is_active": true, "currency": "EGP",
    "views": 14, "unique_views": 9, "interests": 2, "saves": 1,
    "orders": 3, "sold_orders": 3, "units": 4,
    "sales": [{"currency": "EGP", "sales_value": "450.00"}],
    "conversion": 0.3333
  }],
  "coverage": {"days_in_range": 2, "rollup_days": 2, "views_available": true, "views_complete": true},
  "data_updated_at": "2026-03-12T00:15:04Z"
}
```
`conversion` = sold orders / unique views (`null` without full coverage).

`GET /business/{id}/products/{product_id}/` (a product of another business
is 404):
```json
{
  "business_id": 7,
  "product": {"product_id": 12, "name": "Leather bag", "currency": "EGP", "is_active": true},
  "period": {"date_from": "2026-03-10", "date_to": "2026-03-11", "days": 2, "period": "custom"},
  "totals": {
    "views": 14, "unique_views": 9, "saves": 1, "interests": 2,
    "orders": 3, "sold_orders": 3, "units": 4,
    "sales": [{"currency": "EGP", "sales_value": "450.00", "sold_orders": 3, "units": 4, "aov": "150.00"}],
    "conversion": 0.3333
  },
  "interested_customers": 2,
  "funnel": {"unit_note": "...", "stages": [...], "conversions": [...], "overall_rate": 0.1111},
  "trend": [
    {"date": "2026-03-10", "views": 10, "unique_views": 6, "interests": 0,
     "orders": 2, "sold_orders": 2, "sales": {"EGP": "350.00"}},
    {"date": "2026-03-11", "views": 4, "unique_views": 3, "interests": 0,
     "orders": 1, "sold_orders": 1, "sales": {"EGP": "100.00"}}
  ],
  "coverage": {...},
  "data_updated_at": "2026-03-12T00:15:04Z",
  "orders_data_updated_at": "2026-03-11T15:20:00Z"
}
```
In `trend`, `views`, `unique_views` and `interests` are `null` for a day that
was not rolled up; orders are always real numbers.

## 4. Content
`GET /business/{id}/content/?type=post|reel|all&ordering&page&page_size&date_from&date_to&period`
- `ordering`: `created_at|views|unique_views|likes|comments|saves|in_app_shares|engagement_rate`
  (prefix `-`). Default `-views`.

```json
{
  "business_id": 7, "type": "all",
  "period": {...}, "count": 2, "page": 1, "page_size": 20, "total_pages": 1,
  "results": [{
    "content_type": "post", "content_id": 31, "caption_preview": "New arrivals",
    "status": "published", "created_at": "2026-03-01T09:00:00Z",
    "views": 10, "unique_views": 5, "likes": 2, "comments": 1, "saves": 1,
    "in_app_shares": 1, "engagement_rate": 1.0
  }],
  "coverage": {...}, "data_updated_at": "2026-03-12T00:15:04Z"
}
```

`GET /business/{id}/content/{type}/{id}/` (`type` = `post|reel`):
```json
{
  "business_id": 7,
  "content": {"content_type": "post", "content_id": 31, "caption_preview": "New arrivals", "status": "published", "created_at": "2026-03-01T09:00:00Z"},
  "period": {...},
  "internal": {
    "source": "internal_events",
    "totals": {"views": 10, "unique_views": 5, "likes": 2, "comments": 1, "saves": 1, "in_app_shares": 1, "engagement_rate": 1.0},
    "trend": [{"date": "2026-03-10", "views": 10, "unique_views": 5, "likes": 2, "comments": 1, "saves": 1, "in_app_shares": 1, "engagement_rate": 1.0}],
    "notes": {
      "in_app_shares": "In-app share events only; they do not prove any external view.",
      "reach": "Not defined: there is no reliable source for it."
    }
  },
  "external": {
    "metrics": [{"platform": "instagram", "metric_type": "views", "value": 500, "source": "manual", "last_updated": "2026-03-01T00:00:00Z", "external_reference": ""}],
    "note": "Owner-entered numbers from other platforms. Never merged with the internal numbers above."
  },
  "coverage": {...}, "data_updated_at": "2026-03-12T00:15:04Z"
}
```

## 5. External metrics (owner-entered)
`GET /business/{id}/content/{type}/{id}/external-metrics/`
```json
{
  "business_id": 7, "content_type": "post", "content_id": 31,
  "metrics": [{"platform": "instagram", "metric_type": "views", "value": 700, "source": "manual", "last_updated": "2026-03-02T00:00:00Z", "external_reference": ""}],
  "providers_registered": []
}
```
Latest value per (platform, metric_type) by `captured_at`.

`POST` the same URL (201 returns the stored snapshot):
```json
{
  "platform": "instagram",
  "metric_type": "views",
  "metric_value": 700,
  "captured_at": "2026-03-02T00:00:00Z",
  "external_reference": "https://instagram.com/p/abc"
}
```
- `platform`: `instagram|facebook|tiktok`. `metric_type`: `views|likes|shares|saves`.
- `metric_value` integer 0..10^12. `captured_at` optional (default now), not in
  the future. `source` cannot be sent: it is always `manual`.
- Snapshots accumulate; nothing is updated.

## 6. Orders summary
`GET /business/{id}/orders-summary/?date_from&date_to&period&top`
(`top` 1..20, default 5). Live from the orders table; default `date_to` = today (UTC).
```json
{
  "business_id": 7,
  "period": {...},
  "orders": 5, "units": 4,
  "by_status": [
    {"status": "pending", "orders": 1}, {"status": "confirmed", "orders": 1},
    {"status": "processing", "orders": 1}, {"status": "shipped", "orders": 0},
    {"status": "delivered", "orders": 1}, {"status": "cancelled", "orders": 1}
  ],
  "sales": [{"currency": "EGP", "sales_value": "450.00", "sold_orders": 3, "units": 4, "aov": "150.00"}],
  "sales_definition": "Sales Value = confirmed + processing + shipped + delivered orders, per currency. pending and cancelled are excluded.",
  "trend": [{"date": "2026-03-10", "orders": 2, "sold_orders": 2, "sales": {"EGP": "350.00"}}],
  "top_products": [{"product_id": 12, "name": "Leather bag", "currency": "EGP", "sales_value": "450.00", "sold_orders": 3, "units": 4}],
  "data_updated_at": "2026-03-11T15:20:00Z"
}
```

## 7. Legacy daily (unchanged)
`GET /business/{id}/daily/` returns the existing cursor envelope
`{next, previous, results}` with the same 10 fields per row.

## Client notes
- Treat `null` as "no data", not zero. Show the coverage note when
  `views_complete` is false.
- Orders and interests are live; views/engagement come from the daily
  rollup of previous days (UTC), so today is never included in them.
- Throttles: 60/min anonymous, 120/min logged in, on the view endpoint only.