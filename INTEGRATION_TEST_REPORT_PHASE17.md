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

## P-095 - Deep-Link Cross-Navigation Integration Test

Status: VERIFIED - 2 OPEN DEFECTS (D-1, D-2) + 1 GAP (G-1). Phase 17 gate stays OPEN.

Method: every event was triggered for real (moderation.services approve/reject,
the real comment endpoint, the real like endpoint). The kwargs each source
handed to `dispatch_notification.delay(...)` were run through the REAL
`dispatch_notification` body (same pattern as P-094), the stored row was read
back through the real `GET /api/v1/notifications/`, and the screen the deep link
opens was checked by replaying the exact GET the Flutter detail screen issues.
On the Flutter side the real navigator, resolver, screens and repositories were
exercised over a fake Dio. No new notification type or deep-link target was added.

### Result per notification type

| # | notification_type | Trigger | deep_link_type stored | target_id stored | Lands on the exact content? | Result |
|---|-------------------|---------|-----------------------|------------------|-----------------------------|--------|
| 1 | moderation_approved (Post) | moderator approves a Post | post_detail OK | Post id OK | Backend serves that exact Post (id, status=published). Public screen loads it. | PASS (backend); not tapped on a device |
| 1b | moderation_approved (Reel) | moderator approves a Reel | reel_detail OK | Reel id OK | Backend serves that exact Reel, BUT the public screen shows "Reel not found" until processing_status is "ready" | DEFECT D-2 |
| 2 | moderation_rejected | moderator rejects a Post with a reason | post_detail OK | Post id OK | Backend serves the Post with status=rejected and rejection_reason. The public screen shows "Post not found" and hides the reason | DEFECT D-1 |
| 3 | new_follower | follow | see P-094 | see P-094 | see P-094 | PASS (P-094) |
| 4 | comment_on_content (Post) | customer comments on a Post | post_detail OK | Post id OK | Backend serves that exact Post; owner receives it, commenter does not | PASS (backend); not tapped on a device |
| 4b | comment_on_content (Reel) | customer comments on a Reel | reel_detail OK | Reel id OK | Backend serves that exact Reel | PASS (backend); not tapped on a device |
| 5 | new_like | customer likes a Reel | NOT SENT | NOT SENT | no notification exists | GAP G-1 |
| 6 | chat_message | offline recipient | see P-094 | see P-094 | see P-094 | PASS (P-094) |
| 7 | new_share | none | - | - | no source anywhere in the code | GAP G-1 |
| 8 | new_rating | none | - | - | no source anywhere in the code | GAP G-1 |
| 9 | system_announcement | none | - | - | no source anywhere in the code | GAP G-1 |

Also: no source ever sends `product_detail`, so that deep-link target is covered
only by the P-080 resolver unit tests (`/product/33`), not by a real event.
A rejected or approved Story deep-links to `business_profile` (+ the business id)
by design: the P-078 contract has no story detail screen (pinned by
`test_content_without_detail_screen_links_to_business_profile`).

### Defects

D-1 (open, Flutter): a rejected Post/Reel opens "not found" for its owner.
- Cause: the notification deep-links to `/post/:id` and `/reel/:id`, the PUBLIC
  detail screens. `PostPublicRepositoryImpl.fetchPublicPost` returns null when
  `status != "published"`, which the screen renders as "Post not found. It may
  have been removed." The rejection reason (served by the backend, and carried
  by the owner-side `Post`/`Reel` entities since P-044) is never shown.
- Impact: breaks the P-095 acceptance criterion "showing the rejection reason
  where applicable". The data exists on both sides; what is missing is an
  owner-facing view reachable from the notification.
- Not fixed in P-095: it needs an owner-facing detail view (or an owner-aware
  public screen), which is a screen/architecture decision, not a small
  resolver/mapping fix. Hiding it by blanking the deep link in the backend was
  rejected on purpose: it would remove the symptom, not the defect.

D-2 (open, Flutter, timing-dependent): an approved Reel whose
`processing_status` is not yet "ready" opens "not found". Moderation approval
does not change `processing_status`, so tapping the approval notification before
transcoding finishes shows "Reel not found". Same root cause and same fix area
as D-1 (`ReelPublicRepositoryImpl.fetchPublicReel`).

### Gap

G-1 (open, backend, decision needed): `new_like`, `new_share`, `new_rating` and
`system_announcement` exist as NotificationType choices but nothing enqueues them
(`LikeToggleView` creates the Like and the counter, then returns). Adding a
source is new scope, so P-095 records it instead. Decision needed: build the
sources, or remove the unused types from the contract.

### Observation

O-1: `StorySerializer` has no `rejection_reason` (documented in
`stories/serializers.py`), so a rejected Story has no rejection reason to show
even once D-1 is fixed.

### Automated evidence added by P-095

- Backend `notifications/tests/test_deep_link_sweep.py`: 6 passed, 1 xfailed.
  The xfail is `test_like_on_reel_notifies_owner_and_links_to_that_reel`
  (strict, tied to G-1: it fails with XPASS the day a like source is added).
  `test_every_notification_type_is_classified` fails if a new NotificationType is
  added without being covered or documented as a gap.
- Backend `pytest notifications/tests`: 114 passed, 1 xfailed.
- Flutter `test/features/notifications/presentation/notification_unpublished_content_test.dart`:
  3 passed. These are CHARACTERIZATION tests that pin D-1 and D-2; when the defects
  are fixed their assertions must be flipped to expect the content and the
  rejection reason.
- Flutter `flutter test test/features/notifications`: 72 passed.

### NOT verified by this part

- Tapping each notification on a real device (verification used the real
  navigator/screens/repositories in widget tests over a fake Dio).
- Live FCM push delivery (Firebase not configured; unchanged from P-094).

### Phase 17 gate

P-094 passed. P-095 verified but did not pass: D-1 and D-2 are open and G-1
needs a decision. Phase 17 is NOT marked COMPLETE. Phase 18 must not start until
D-1 and D-2 are fixed (or explicitly accepted by the owner) and G-1 has a decision.
