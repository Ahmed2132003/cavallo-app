# INTEGRATION_TEST_REPORT_PHASE17 - Part P-094 End-to-End Integration Pass

Status: IN PROGRESS (script step 1 of 3 applied)

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
| 1 | Register Business, onboarding (P-028), Admin verify | automated (test_integration_phase17.py) | PENDING RUN | Admin verify is an ORM update in the test; real Django Admin toggle to be checked manually |
| 2 | Create Product, Post, Story | automated | PENDING RUN | |
| 3 | Moderator approves | automated | PENDING RUN | Products are not moderated (F-1) |
| 4 | Register Customer | automated | PENDING RUN | |
| 5 | Search (text + filter) and Discover | script step 2 | NOT STARTED | |
| 6 | Follow, Home feed hybrid algorithm on fresh fetch | script step 2 | NOT STARTED | |
| 7 | Like / Comment / Save / Share | script step 2 | NOT STARTED | |
| 8 | Rating + average_rating | script step 2 | NOT STARTED | |
| 9 | Chat: text, image, product share | script step 3 | NOT STARTED | |
| 10 | Real-time delivery + status progression | script step 3 | NOT STARTED | |
| 11 | Notifications + deep links | script step 3 | NOT STARTED | |
| 12 | Featured ranking in Search and Feed | script step 3 | NOT STARTED | |

## 2. Findings
- F-1 (plan vs code, not a bug): P-094 step 3 says a Moderator approves Product, Post and Story,
  but `products.Product` is not a `moderation.Moderatable`; products are visible immediately
  (`is_active`) and only Admin can hide/remove them (matches the product deck, Admin Dashboard
  "Products: Hide / Remove"). Post and Story are moderated.

## 3. Bugs found and fixed
None yet.

## 4. Open follow-up items
None yet.