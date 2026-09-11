# Configuration & Secrets — Backend (`cavallo-app`)

Every environment variable the backend reads lives in `.env.example` (copy it to `.env`
for local dev — `.env` is gitignored, never commit real secrets). This file explains what
each value is for, who consumes it, and whether it's required to run the app locally.

This doc is the source of truth cross-referenced against the architecture plan's
**Section 7 (Required Inputs)**. Any future part that needs a new secret/config value adds
it to both `.env.example` and this file — never invents an ad hoc `os.environ` read.

## Django core

| Variable | Consumed by | Required to run locally? |
| --- | --- | --- |
| `SECRET_KEY` | `config/settings.py` | Yes — any non-empty string works in dev |
| `DEBUG` | `config/settings.py` | Yes — `True` in dev |
| `ALLOWED_HOSTS` | `config/settings.py` | Yes — default covers local dev |

## Database & Redis

| Variable | Consumed by | Required to run locally? |
| --- | --- | --- |
| `DATABASE_URL` | `config/settings.py` (`django-environ`'s `env.db()`) | Yes |
| `REDIS_URL` | `config/settings.py` (cache, Channels layer, Celery broker/result backend) | Yes |

Note: from the host machine, Postgres and Redis are exposed on `localhost:5435` and
`localhost:6381` respectively (see P-000's port table in `PROJECT_PROGRESS.md`) — but
`DATABASE_URL`/`REDIS_URL` above use the Docker-internal service names/ports (`db:5432`,
`redis:6379`), since Django itself runs inside the same Docker network.

## CORS

| Variable | Consumed by | Required to run locally? |
| --- | --- | --- |
| `CORS_ALLOWED_ORIGINS` | `config/settings.py` | No — empty is fine until a browser-based client needs cross-origin access |

## JWT

| Variable | Consumed by | Required to run locally? |
| --- | --- | --- |
| `JWT_ACCESS_TTL_MINUTES` | Reserved for `SIMPLE_JWT` settings, wired up in the auth part | No effect yet — placeholder only until that part reads it |
| `JWT_REFRESH_TTL_DAYS` | Same as above | Same as above |

These two are documented now (Phase 0) so the auth part doesn't invent its own convention
for token lifetimes — it just reads these.

## Sentry — **INPUT REQUIRED**

| Variable | Consumed by | Required to run locally? |
| --- | --- | --- |
| `SENTRY_DSN` | Monitoring part (P-024) — `sentry_sdk.init()` is skipped entirely if this is unset | No — dev works fine with it blank; you simply get no error reporting |

Blocked on: a real Sentry project/DSN. See architecture Section 7, item covering
monitoring.

## Object Storage — **INPUT REQUIRED**

| Variable | Consumed by | Required to run locally? |
| --- | --- | --- |
| `OBJECT_STORAGE_PROVIDER` | Media/uploads storage backend (wired in its own later part) | No |
| `OBJECT_STORAGE_BUCKET` | Same | No |
| `OBJECT_STORAGE_KEY` | Same | No |
| `OBJECT_STORAGE_SECRET` | Same | No |
| `OBJECT_STORAGE_REGION` | Same | No |

Blocked on: real object storage credentials (architecture Section 7, item 2). Until
these are set, dev falls back to local disk storage — product images, post/reel/story
media just won't survive a container rebuild, which is fine for local development but
must be resolved before any real deployment (Phase 2 storage part).

## Firebase Cloud Messaging — **INPUT REQUIRED**

| Variable | Consumed by | Required to run locally? |
| --- | --- | --- |
| `FCM_PROJECT_ID` | Push notifications part (Phase 13) | No |
| `FCM_SERVICE_ACCOUNT_JSON_PATH` | Same | No |

Blocked on: a real Firebase project (architecture Section 7, item 4). Without these,
push notifications are simply never sent — everything else in the app works normally.

## Paymob — **INPUT REQUIRED**

| Variable | Consumed by | Required to run locally? |
| --- | --- | --- |
| `PAYMOB_API_KEY` | Payments part (Phase 15), if/when in-app payment is added | No |
| `PAYMOB_WEBHOOK_SECRET` | Same | No |

Blocked on: real Paymob credentials (architecture Section 7, item 3). Note the MVP as
scoped has **no in-app payment/checkout at all** (deals happen outside the platform per
the product plan) — these vars are reserved for a possible future phase, not anything
in the current MVP roadmap.

## Postgres container init vars

| Variable | Consumed by | Required to run locally? |
| --- | --- | --- |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | The `db` service in `docker-compose.yml` itself (official `postgres` image's init vars) | Yes — must match the credentials embedded in `DATABASE_URL` above |

---

## Cross-check against architecture Section 7

Every currently-blocked input from Section 7 has a named placeholder above:

- Object storage credentials → `OBJECT_STORAGE_*` ✅
- Paymob credentials → `PAYMOB_*` ✅
- Firebase project → `FCM_*` ✅
- Sentry DSN → `SENTRY_DSN` ✅

No Section 7 item is missing a corresponding placeholder as of this part.

## Flutter side

See the Flutter repo's own `CONFIG.md` for `API_BASE_URL` / environment (`--dart-define`)
conventions — kept in that repo since it's consumed by `lib/core/config/app_config.dart`
there, not by this backend.