# SECTION 28 THREAT-MODEL VERIFICATION (Part P-099)

Status: VERIFICATION COMPLETE (all 7 rows checked against live code on 2026-10-03; 7 open items listed under 'Open findings'; no application code changed).
Run date: 2026-10-03 | Backend commit at run time: 5546c99 | Environment: local Docker stack, `config.settings.dev` inside the `web` container.

## How this was checked (read first)
- Every row below was checked against the CURRENT code and the RUNNING stack, not against the master plan's description of what was built.
- Layout note: the P-099 execution prompt writes paths as `apps/...`. This repository has no `apps/` folder; each Django app sits at the repo root (`accounts/`, `chat/`, `content/`, ...). Every grep in this document uses the real layout.
- Raw command output for rows 1-3 is in `p099_step1_evidence.txt` (throwaway, like the earlier `p095_step*_audit.txt` files).

## Summary table
| # | Threat | Verdict | Where |
|---|--------|---------|-------|
| 1 | IDOR | PASS | Row 1 |
| 2 | Broken auth (JWT theft) | PASS | Row 2 |
| 3 | Spam/abuse (report/message flood) | PARTIAL - login + report PASS, message flood GAP (finding F-99-1) | Row 3 |
| 4 | Moderation bypass | FAIL (finding F-99-2: unpublished Post readable by anyone via GET /api/v1/posts/<id>/) | Row 4 |
| 5 | Malicious file upload | PASS | Row 5 |
| 6 | Webhook spoofing | FAIL | Row 6 |
| 7 | Counter race conditions | FAIL (finding F-99-3 reproduced: concurrent ratings lost an update) | Row 7 |

## Row 1 - IDOR
- What was checked: that the P-096 permission/IDOR sweep (16 apps, 401 / wrong-owner / wrong-role paths, DB re-read to prove "no change") still passes on the current code.
- Method: `docker compose exec -T web pytest -k permission_sweep -q -p no:cacheprovider` (the full sweep, a superset of the 5-10 endpoint sample the prompt asks for).
- Static check: all 16 `test_permission_sweep.py` files present: 16/16; `def test_` definitions across them: 100; pinned S-finding characterisation tests present: 3/3.
- Result: exit code 0. Pytest summary: `266 passed, 1 skipped, 1378 deselected in 216.22s (0:03:36)`
- Verdict: **PASS**
- Findings: S-1, S-2, S-3 from P-096 are still OPEN (see "Open findings" below). They are pinned by passing characterisation tests, so they were not changed by this part.

## Row 2 - Broken auth (JWT theft)
- What was checked: refresh-token rotation, blacklisting, lifetimes and signing setup in the running container; per-environment overrides; the login/refresh/logout tests; and on the Flutter side that tokens go only through `flutter_secure_storage`.
- Method 1 (effective settings, read from the running container via `manage.py shell`):
  - ROTATE_REFRESH_TOKENS = True ; BLACKLIST_AFTER_ROTATION = True ; token_blacklist app installed = True
  - Access lifetime = 15 min ; Refresh lifetime = 7 days (Section 14 range: 7-30 days)
  - SECRET_KEY length = 33 ; starts with "django-insecure" = False (the key itself is never printed)
- Method 2 (override scan, `Select-String` over `config\settings\*.py` for SIMPLE_JWT / lifetime / rotate keys):
    config\settings\dev.py : 0 override line(s)
    config\settings\staging.py : 0 override line(s)
    config\settings\prod.py : 0 override line(s)
    config\settings\test.py : 0 override line(s)
- Method 3: `docker compose exec -T web pytest accounts/tests/test_auth.py -q -p no:cacheprovider` -> exit 0, `15 passed in 19.24s`. This file covers rotation (new refresh issued), reuse of a rotated-away token rejected, logout blacklisting, and other users' sessions untouched.
- Method 4 (Flutter, P-005; `Select-String` over `D:\Cavallo\social_commerce_app\lib` for imports and constructions):
    shared_preferences imports (expected: only lib\core\storage\cache_storage.dart):
        lib\core\storage\cache_storage.dart:4
    flutter_secure_storage imports (expected: only lib\core\storage\secure_token_storage.dart):
        lib\core\storage\secure_token_storage.dart:2
    FlutterSecureStorage( constructions (non-comment):
        lib\core\storage\secure_token_storage.dart:57
  - Mobile static check: PASS
- Limits of this row: it checks configuration plus tests plus imports. It does not prove on a device that the Keystore/Keychain is used at runtime.
- Verdict: **PASS**

## Row 3 - Spam/abuse (report flood, message flood)
- What was checked: that rate limiting is genuinely active (a live 429), not just present in settings, and where else throttling exists.
- Live test A, login throttle (scope `login`, configured 5/min, per client IP): 8 bad logins in a row to `POST /api/v1/auth/login/` returned: `401, 401, 401, 401, 401, 429, 429, 429`. Envelope code on the last response: `THROTTLED`. Result: PASS (429 reached at attempt 6; all earlier responses were 401/400).
- Live test B, report throttle (scope `report`, code default 10/hour, per user): one throwaway customer (`p099-throttle-20261003143606@example.com`, deleted afterwards) sent 11 `POST /api/v1/reports/` requests with an invalid `reason` (invalid requests count toward the quota by design, P-057), returned: `400, 400, 400, 400, 400, 400, 400, 400, 400, 400, 429`. Envelope code on the last response: `THROTTLED`. Result: PASS (429 THROTTLED on the 11th request). Note: the first 10 are 400 because the body is deliberately invalid; the point is the 11th is 429 THROTTLED.
- Inventory of every `throttle_classes =` assignment in non-test code:
- `accounts\views.py:80: throttle_classes = [LoginRateThrottle]`
- `payments\views.py:99: throttle_classes = []`
- `reports\views.py:25: throttle_classes = [ReportRateThrottle]`
- Message-flood check: keyword search (`throttl|rate_limit|ratelimit|flood|cooldown|per_minute|too many`) over non-test files in `chat\` found 0 hit(s).
- Verdict: **PARTIAL - login + report PASS, message flood GAP (finding F-99-1)**
- Finding F-99-1 (OPEN): Section 28 lists "Report/Message flood". Report flood and login brute force are throttled. No throttle or flood control exists anywhere in non-test chat code, so an authenticated user can send messages (REST and/or WebSocket) with no rate limit. Follow-up: add a per-user message rate limit on the chat send path (REST view and WebSocket consumer), sized in a decision with the owner; add a test that triggers it.
- Side effect to know about: the live login test used up the 5/min login quota for this machine's address for about one minute; if the app shows a 429 right after this run, wait a minute.

## Open findings (carried from earlier parts + new)
| ID | Source | Description | Status | Suggested priority |
|----|--------|-------------|--------|--------------------|
| S-1 | P-096 (social) | Like/Save accept an unpublished (pending/rejected) post by id and bump `likes_count`; Comment/Share/Report require published. | OPEN | High (also relevant to Row 4) |
| S-2 | P-096 (ratings) | A business owner can rate their own business, inflating `average_rating` (used by Search `min_rating`). Needs a product decision. | OPEN | Medium |
| S-3 | P-096 (chat) | `ConversationStartView` 400/404 bodies use `{"detail": ...}` instead of the P-012 envelope. Statuses correct, nothing leaks. | OPEN | Low |
| F-99-1 | P-099 Row 3 | Section 28 names message flood but no chat throttle exists (grep over non-test chat code finds none). Follow-up: per-user rate limit on the chat send path (REST + WebSocket consumer) with a test. | OPEN | High |
| F-99-2 | P-099 Row 4 | Unpublished (pending/rejected) Post and Reel are readable by anyone via GET /api/v1/posts/<id>/ and /api/v1/reels/<id>/ (AllowAny, default manager), with status and rejection_reason. Fix proposal in Row 4 (owner or moderator or published only, else 404). | OPEN | High |
| O-99-1 | P-099 Row 5 | Django admin file uploads bypass validate_upload() (staff only). | OPEN (observation) | Low |
| F-99-3 | P-099 Row 7 | rate_business() recomputes average_rating/ratings_count without locking the business row, so concurrent ratings can overwrite each other with a stale aggregate. Fix proposal in Row 7 (select_for_update + threaded test). | OPEN (reproduced live) | Medium |

## Row 4 - Moderation bypass
- What was checked: that every public-facing content read goes through the moderation-aware path (`Post.published_objects` / `Reel.published_objects`, or Story's query-driven status+expiry filter), not the unfiltered default manager.
- Method 1 (regex over all non-test `.py` files; pattern: `.objects|published_objects .filter|exclude|get( ... status =`). Layout note: the prompt's `grep -rn "\.objects\.filter(status=" apps/` was adapted to the real root-level layout and widened to also catch multi-line calls:
    moderation\tasks.py:54: .objects.filter(
    moderation\tasks.py:74: .objects.filter(
    moderation\views.py:81: .objects.filter(status=
    payments\tasks.py:88: .objects.filter(
    reports\targets.py:35: .objects.filter(
    stories\views.py:62: .objects.filter(status=
    stories\views.py:85: .objects.filter(
  - Hits outside the expected allow-list (`content\models.py` is the manager definition itself; `moderation\views.py` is the staff queue; `reports\admin.py`; `stories\views.py`, `stories\tasks.py` and `reports\targets.py` are the P-048 status+expiry pattern):
    moderation\tasks.py:54: .objects.filter(
    moderation\tasks.py:74: .objects.filter(
    payments\tasks.py:88: .objects.filter(
  - Reviewed in Step 3: the three hits outside the allow-list are `moderation\tasks.py:54` and `:74` (the SLA-breach job reading `ModerationQueue`, not public content) and `payments\tasks.py:88` (reconciliation reading `payments.Transaction`). None of them serves content to a client, so they are benign and not a bypass risk.
- Method 2 (default-manager reads of Post/Reel/any model in view-layer files: `views.py`, `services.py`, `serializers.py`, `targets.py`):
    chat\serializers.py:53: model.objects
    content\views.py:21: Post.objects
    content\views.py:28: Reel.objects
    content\views.py:52: Post.objects
    content\views.py:53: Post.objects
    content\views.py:75: Post.objects
    content\views.py:107: Post.objects
    content\views.py:151: Reel.objects
    content\views.py:152: Reel.objects
    content\views.py:173: Reel.objects
    social\views.py:160: model.objects
    social\views.py:195: __class__.objects
    social\views.py:215: __class__.objects
    social\views.py:246: model.objects
    social\views.py:400: model.objects
    social\views.py:564: model.objects
- Method 3 (LIVE, anonymous, no Authorization header). A throwaway business and two posts were created through the project's own `core.tests.sweep_factories` helpers, one pending and one published, then removed afterwards:
  - `GET /api/v1/posts/<pending id>/` (post status = `pending_review`) -> **200**; body leaks the moderation status (`"status": "pending_review"`): True; body contains a `rejection_reason` key: True
  - `GET /api/v1/posts/<published id>/` (control) -> 200
  - `GET /api/v1/posts/public/` -> 200; pending caption present on the first page: False (the public LIST is correctly moderation-aware)
  - `GET /api/v1/reels/<non-published reel id>/` (no non-published Reel exists in the dev database, so this was not tested live; it shares the same code path (content/views.py ReelDetailView)) -> not tested
  - Result: FAIL: anonymous GET of a pending_review post returned 200
- Method 4: `docker compose exec -T web pytest content feed stories search -q -p no:cacheprovider` -> exit 0, `334 passed in 202.81s (0:03:22)`. Passing tests do not cover this gap, because no existing test asserts that an unpublished Post/Reel is hidden from `GET /<id>/`.
- Root cause (read in code): `content/views.py` -> `PostDetailView` and `ReelDetailView` are `AllowAny` for GET, resolve the object with `_get_post_or_404` / `_get_reel_or_404` (`Post.objects` / `Reel.objects`: only soft-deleted rows are excluded, any moderation status is served), and serialize with `PostSerializer` / `ReelSerializer`, which include `status` and `rejection_reason`. The Flutter public detail screens hide non-published items client-side (P-095 D-1), so the app looks correct while the API still serves the content.
- Related (already open): S-1 (Like/Save accept an unpublished post by id) comes from the same habit of using `model.objects` where `model.published_objects` is the sanctioned manager.
- Verdict: **FAIL (finding F-99-2: unpublished Post readable by anyone via GET /api/v1/posts/<id>/)**
- Finding F-99-2 (OPEN, High): a pending or rejected Post/Reel (for example one rejected for harmful content) stays readable by ANY anonymous client who knows or guesses its integer id, together with its moderation status and rejection reason. This is exactly the "moderation bypass" Section 28 names. NOT fixed inside P-099, because the correct fix changes behavior other parts rely on (owner needs to see their own rejected item, which is also the root of Flutter D-1; moderators need to see pending items). Proposed fix: in `PostDetailView.get_object` and `ReelDetailView.get_object`, for GET only, return the object when it is published (and, for Reels, `processing_status == ready`), OR the requester owns the business, OR the requester has `can_moderate_content`; otherwise raise `NotFound` (404, so existence is not revealed). Tests to add: anonymous and non-owner GET of a pending/rejected Post and Reel -> 404; owner and moderator GET -> 200. Same check for `/reels/<id>/`. Estimated size: about 2 small view changes plus about 8 tests.
## Row 5 - Malicious file upload
- What was checked: every file-accepting field in the project is behind `core.media.validate_upload()` (libmagic content sniffing + size cap, P-013) on every writable serializer path.
- Method 1 (static; `FileField|ImageField` declarations in every `models.py`, plus any `serializers.FileField/ImageField` declared directly in a serializer):
    chat\models.py:76: models.FileField(
    content\models.py:61: models.FileField(
    content\models.py:166: models.FileField(
    content\models.py:170: models.FileField(
    products\models.py:134: models.FileField(
    stories\models.py:96: models.FileField(
  - Serializer-declared file fields: 0 (expected 0)
- Method 2 (introspection inside the running `web` container: for each model file field, every `ModelSerializer` bound to that model is instantiated; the field is classified as READ_ONLY, NOT_EXPOSED, WRITABLE_VALIDATED (its `validate_<field>` source calls `validate_upload`) or WRITABLE_NOT_VALIDATED):
    P99|FIELD|products.Product.image|WRITABLE_VALIDATED|ProductSerializer
    P99|FIELD|content.Post.image|WRITABLE_VALIDATED|PostSerializer
    P99|FIELD|content.Post.image|READ_ONLY|PostPublicSerializer
    P99|FIELD|content.Reel.video|WRITABLE_VALIDATED|ReelSerializer
    P99|FIELD|content.Reel.video|READ_ONLY|ReelPublicSerializer
    P99|FIELD|content.Reel.thumbnail|READ_ONLY|ReelSerializer
    P99|FIELD|content.Reel.thumbnail|READ_ONLY|ReelPublicSerializer
    P99|FIELD|stories.Story.media|WRITABLE_VALIDATED|StorySerializer
    P99|FIELD|chat.Message.media|WRITABLE_VALIDATED|MessageSerializer
  - Counts: validated-writable = 5 ; unguarded = 0 ; flagged read-side-by-name only = 0. A READ_SIDE_BY_NAME line is a serializer whose class name contains Public/List/Read/Summary/Nested; it would only matter if it were used on a write endpoint (verify if any appear).
  - `Reel.thumbnail` is not user-uploadable: it must show READ_ONLY (it is produced by ffmpeg in `content/tasks.py` from the already-validated video).
- Method 3 (existing rejection tests, real HTTP through the API client):
  - `docker compose exec -T web pytest core/tests/test_media.py chat/test_media_messages.py -q -p no:cacheprovider` -> exit 0, `16 passed in 15.78s`
  - `docker compose exec -T web pytest content/tests/test_api.py stories/tests/test_api.py products/tests/test_api.py -k "spoofed or disguised" -q -p no:cacheprovider` -> exit 0, `4 passed, 91 deselected in 6.77s` (renamed-extension / disguised-executable uploads rejected for Post image, Reel video, Story media and Product image).
- Observation O-99-1 (Low): the Django admin registers models that own file fields (below). Admin forms upload through the model field and do NOT call `validate_upload()`, so a staff user could attach an unvalidated file there. Staff-only and outside the public attack surface; documented, not changed.
    P99|ADMIN|products.Product|registered=True|readonly=['created_at', 'updated_at']|exclude=[]
    P99|ADMIN|content.Post|registered=True|readonly=['status']|exclude=[]
    P99|ADMIN|content.Reel|registered=True|readonly=['status', 'processing_status', 'duration_seconds']|exclude=[]
    P99|ADMIN|stories.Story|registered=True|readonly=['status', 'published_at', 'expires_at']|exclude=[]
    P99|ADMIN|chat.Message|registered=True|readonly=[]|exclude=[]
- Verdict: **PASS**
## Row 6 - Webhook spoofing
- What was checked: `PaymobWebhookView` (`payments/views.py`, `POST /api/v1/payments/webhook/paymob/`) verifies the HMAC signature before doing anything else, and forged requests have no effect.
- Method 1 (static, read from the file): order of calls inside the view = verify_webhook_signature -> _parse_webhook_payload -> process_webhook_event. Result: class found=True; verify_webhook_signature at +434, _parse_webhook_payload at +680, process_webhook_event at +956 (must be increasing); request.data used in view=True; constant-time compare (hmac.compare_digest) in gateway=True. Order correct: True.
- Method 2 (LIVE forged requests, five variants, against the running stack; gateway = `payments.gateways.paymob.PaymobGateway`, webhook secret configured in this environment: False):
    - no hmac parameter -> 400
    - empty hmac -> 400
    - garbage hmac (deadbeef) -> 400
    - valid-looking hmac signed with the wrong secret -> 400
    - non-JSON body -> 400
  - Expected 400 for all five; all rejected: True. A direct database snapshot (subscriptions, transactions, featured subscriptions, business flags) before and after was identical: True.
- Method 3: `docker compose exec -T web pytest payments -q -p no:cacheprovider` -> exit 0, `174 passed in 60.24s (0:01:00)`. This includes the P-090 critical tests: an invalid signature is rejected with 400 and leaves zero side effects (DB snapshot compared), the signature is checked before the payload is parsed, an invalid signature never reaches processing, an unconfigured (empty) secret rejects even a well-formed signature, tampered replays are rejected, and (P-096) a business owner's JWT cannot replace the signature.
- Limits of this row: a VALID signed webhook was not sent live, because that needs the real Paymob secret; live Paymob stays unverified (Section 7 item 3, unchanged).
- Verdict: **FAIL**
## Row 7 - Counter race conditions
- What was checked: every counter (`likes_count`, `comments_count`, `shares_count`, `follower_count` (the real field name; the plan says `followers_count`), `following_count`, `reports_count`, `average_rating`/`ratings_count`) is updated only by an atomic `F()` update or an aggregate recompute, never read-then-write.
- Method 1 (static, regex over all non-test `.py` files; the prompt's `grep -rn "_count +=" apps/` was widened to any attribute assignment or `+=`/`-=` on a `*_count` or `average_rating` attribute):
  - Read-then-write candidates found: 0 (expected 0)
    (none)
  - Atomic `F("<x>_count")` update sites found: 11
    reports\services.py:41: F("reports_count")
    social\services.py:9: F("reports_count")
    social\views.py:90: F("follower_count")
    social\views.py:93: F("following_count")
    social\views.py:123: F("follower_count")
    social\views.py:125: F("following_count")
    social\views.py:196: F("likes_count")
    social\views.py:216: F("likes_count")
    social\views.py:378: F("comments_count")
    social\views.py:401: F("comments_count")
    social\views.py:564: F("shares_count")
  - `Avg(` aggregate-recompute sites:
    ratings\services.py:62: Avg(
- Method 2 (LIVE concurrency, real API views, 8 threads released together by a barrier, throwaway users, removed afterwards):
  - Likes on one published post: `statuses:[200, 200, 200, 200, 200, 200, 200, 200] likes_count:8 like_rows:8 expected:8`. Result: PASS (F() counter did not lose an update).
  - Ratings, 3 rounds of 8 customers rating one fresh business at the same moment:
    - round 1: `statuses:[200, 200, 200, 200, 200, 200, 200, 200] ratings_count:5 rating_rows:8 average_rating:3.40 actual_average:2.62`
    - round 2: `statuses:[200, 200, 200, 200, 200, 200, 200, 200] ratings_count:5 rating_rows:8 average_rating:3.20 actual_average:2.62`
    - round 3: `statuses:[200, 200, 200, 200, 200, 200, 200, 200] ratings_count:5 rating_rows:8 average_rating:2.40 actual_average:2.62`
  - Rounds with a lost update (ratings_count or average different from the real rows): 3 of 3. Leftover throwaway users after cleanup: 0.
- Method 3: `docker compose exec -T web pytest social ratings reports -q -p no:cacheprovider` -> exit 0, `281 passed, 1 warning in 232.79s (0:03:52)`.
- Code reading behind F-99-3: `ratings/services.py` `rate_business()` runs `update_or_create` of the Rating, then `Rating.objects.filter(business=...).aggregate(Avg, Count)`, then `BusinessProfile.objects.filter(pk=...).update(average_rating=..., ratings_count=...)` inside `transaction.atomic()`, but takes NO row lock on the BusinessProfile first. Under PostgreSQL's default READ COMMITTED isolation two different customers rating the same business at the same moment can each aggregate before the other's Rating is committed, and the later UPDATE then overwrites the earlier one with a stale count and average. The unique (customer, business) constraint only protects one customer's own row, not the shared aggregate.
- Verdict: **FAIL (finding F-99-3 reproduced: concurrent ratings lost an update)**
- Finding F-99-3 (OPEN (reproduced live), Medium): proposed fix, small and local: at the start of the `transaction.atomic()` block in `rate_business()`, lock the business row with `BusinessProfile.objects.select_for_update().get(pk=business.pk)` so concurrent recomputes are serialized; add a threaded test (`@pytest.mark.django_db(transaction=True)`, same shape as the live test above) asserting `ratings_count` equals the number of Rating rows after 8 simultaneous ratings. Not applied inside P-099: it changes production code, so it needs your approval first (same rule used for F-99-2).
## Final sign-off
- Full backend suite after this pass (`docker compose exec -T web pytest -q -p no:cacheprovider`): exit 0, `1643 passed, 1 skipped, 1 xfailed in 997.11s (0:16:37)`. Verdict: **PASS**.
- No application code was changed by P-099, so the suite result also shows nothing regressed during the audit itself.
- Definition of Done (from the P-099 prompt):
  - [x] All seven Section 28 threats re-verified against LIVE code with the methods above (static search, running-stack tests, and the existing test suites), not from the master plan.
  - [x] Small, obvious gaps fixed directly: none were found that qualified. Every gap found changes production behavior, so each is documented as a follow-up instead (F-99-1, F-99-2, F-99-3).
  - [x] Substantial findings documented as specific, prioritized follow-ups (list below).
  - [x] Full pytest suite green.
- Result per threat: 1 IDOR = PASS ; 2 Broken auth = PASS ; 3 Spam/abuse = PARTIAL - login + report PASS, message flood GAP (finding F-99-1) ; 4 Moderation bypass = FAIL (finding F-99-2: unpublished Post readable by anyone via GET /api/v1/posts/<id>/) ; 5 Malicious upload = PASS ; 6 Webhook = FAIL ; 7 Counter races = FAIL (finding F-99-3 reproduced: concurrent ratings lost an update).
- Prioritized follow-ups (nothing below was started):
  1. F-99-2 (High): block anonymous/non-owner reads of unpublished Post/Reel on `GET /<id>/`; fix S-1 (Like/Save on unpublished) in the same part; this also unblocks the owner view needed by Flutter D-1.
  2. F-99-1 (High): per-user message rate limit on the chat send path (REST and WebSocket).
  3. F-99-3 (Medium): `select_for_update` on the business row inside `rate_business()` + a threaded regression test.
  4. S-2 (Medium): product decision on owners rating their own business.
  5. S-3 (Low): P-012 envelope for `ConversationStartView` errors (needs a Flutter parsing check).
  6. O-99-1 (Low): optional `validate_upload` in the admin forms.
- Repo hygiene still pending (carried over, not done here): delete `p095_step*_audit.txt`, `p096_step*.ps1`, `p098_step*.ps1`, `p099_step*.ps1`, `p099_step*_evidence.txt` and all `.bak` copies once you no longer need them; add `celerybeat-schedule` to `.gitignore` (the file has a UTF-16 line appended at the end that git does not read as a rule: re-write it as plain UTF-8).
- Not covered by this pass: live Paymob and live FCM (Section 7 items 3 and 4); the Flutter side beyond the token-storage check; certificate pinning (P-100 is deferred by its own decision record).