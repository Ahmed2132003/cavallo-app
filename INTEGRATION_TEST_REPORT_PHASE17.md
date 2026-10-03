# INTEGRATION_TEST_REPORT_PHASE17 - Part P-094 End-to-End Integration Pass

Status: IN PROGRESS (script step 3 of 4 applied)

## 0. Preconditions (Phases 3-16)
Verified against the repository (not only PROJECT_PROGRESS.md) by `p094_step1.ps1`'s audit:
all API prefixes for auth, businesses (+follow, +ratings), products, posts, stories, moderation,
feed, search, conversations, notifications are routed in `config/urls.py`; chat consumers,
monetization models and `dispatch_notification` exist.
Open items inherited from PROJECT_PROGRESS.md Section 7 (NOT verified by this pass):
live Paymob verification and live FCM push delivery.

## 1. Walkthrough results
| # | Step | Method | Result | Notes |
|---|------|--------|--------|-------|
| 1 | Register Business, onboarding (P-028), Admin verify | automated (test_integration_phase17.py) | PASSED | Admin verify is an ORM update in the test; real Django Admin toggle to be checked manually |
| 2 | Create Product, Post, Story | automated | PASSED | |
| 3 | Moderator approves | automated | PASSED | Products are not moderated (F-1) |
| 4 | Register Customer | automated | PASSED | |
| 5 | Search (text + filter) and Discover | automated (TestPhase17Steps5To8) | PASSED | text, country, category, city, min_price filters; Discover lists both published Posts |
| 6 | Follow, Home feed hybrid algorithm on fresh fetch | automated (TestPhase17Steps5To8) | PASSED (after F-5 fix) | followed business content first, no duplicates; unfollow/re-follow flips the order immediately |
| 7 | Like / Comment / Save / Share | automated (TestPhase17Steps5To8) | PASSED | counters (1/1/1) visible on the public Post list; like is idempotent; saved Product listed in /saves/me/ |
| 8 | Rating + average_rating | automated (TestPhase17Steps5To8) | PASSED | avg 4 -> 3 -> 3.5 (upsert keeps count 2); min_rating search filter sees the new aggregate; see F-4 |
| 9 | Chat: text, image, product share | automated (TestPhase17Steps9To10Rest) | PASSED | start by business_id is idempotent; text, image (media_type=image) and a shared Product card (available, business_name, preview) all send; outsider gets 403 |
| 10 | Real-time delivery + status progression | automated (TestPhase17Steps9To10Rest + Live) | PASSED (after F-7 fix) | fetch-on-open (list, history, since) works and does not change status; over real WebSocket the Business receives the message live and sent -> delivered -> read is broadcast to both sides; offline push queued only when the recipient is not connected |
| 11 | Notifications + deep links | script step 3 | NOT STARTED | |
| 12 | Featured ranking in Search and Feed | script step 3 | NOT STARTED | |

## 2. Findings
- F-1 (plan vs code, not a bug): P-094 step 3 says a Moderator approves Product, Post and Story,
  but `products.Product` is not a `moderation.Moderatable`; products are visible immediately
  (`is_active`) and only Admin can hide/remove them (matches the product deck, Admin Dashboard
  "Products: Hide / Remove"). Post and Story are moderated.

## 3. Bugs found and fixed
- F-2 (real seam bug, FIXED): the public Business Profile (GET /api/v1/businesses/{id}/) is cached 5 min (P-030) and is_verified reads through to User.is_business_verified (P-024), but toggling verification in Django Admin never invalidated that cache, so the Verified badge lagged up to 5 minutes. Fix: new businesses/signals.py (post_save on User deletes business_profile:{id}), wired in businesses/apps.py ready(). Test now primes the cache before verifying and uses user.save() (what Admin does); the test also clears the cache per test (shared Redis + 5/min login throttle).

- F-3 (real bug, FIXED, found by step 1): POST /api/v1/products/ as multipart (what Flutter uses for the image) created every product with is_active=False, because DRF treats a missing BooleanField in form data as False. Fix: _FormSafeBooleanField in products/serializers.py (ProductSerializer.is_active); regression covered by products/tests and this module.
- F-5 (real seam bug, FIXED): HomeFeedView caches a user's first page for 90 s (P-060) but Follow/Unfollow never invalidated it, so after following a business the Home feed kept the pre-follow order for up to 90 s. Fix: social/views.py _invalidate_home_feed_cache() called from FollowToggleView POST and DELETE. Covered by TestPhase17Steps5To8 (follow, unfollow, re-follow, each followed by a fresh feed fetch).
- F-7 (real bug, FIXED): the WebSocket ack handler (chat/consumers.py _apply_status_transition) let ANY participant acknowledge ANY message, including the SENDER acknowledging their own message. A sender could therefore fake a read receipt and zero out the recipient's unread_count for their own messages. Fix: the lookup now excludes messages sent by the connected user, so a sender's own mark_delivered/mark_read is a silent no-op. Covered by TestPhase17Steps9To10Live; the existing P-069 tests (all acks sent by the recipient) still pass.

## 4. Open follow-up items
- F-4 (gap, NOT fixed): the public Business Profile (BusinessProfileSerializer) does not expose average_rating / ratings_count. Rating is only visible in the POST /rate/ response and the reviews list, and is filterable in Search, but a profile screen cannot show the stars from GET /api/v1/businesses/{id}/. Adding the two read-only fields is small but changes a serializer field set that other tests pin, so it is left as an explicit follow-up (needs a decision).
- F-6 (observation, NOT fixed, from code reading, not asserted by a test): follower_count on the cached public Business Profile (P-030, 5 min) is not invalidated by Follow/Unfollow, so the count can lag up to 5 minutes. Accepted TTL trade-off unless the product wants an instant counter.
