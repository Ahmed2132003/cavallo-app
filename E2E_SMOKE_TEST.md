# E2E Smoke Test (manual)

**Loop under test:** Register a Business -> Create a Post -> Moderator approves -> Customer sees it published.

**Time budget:** under 15 minutes. **Scope:** catch a badly broken build before a release. This is NOT a regression suite (P-094 to P-097 cover that).

**Why manual:** Architecture Section 25 deliberately defers E2E automation for the MVP. Do not build a runner for this (patrol, integration_test, etc.).

**How to use it:** run the steps in order, with one fresh set of test accounts per run. For each step tick PASS or FAIL. On the first FAIL, stop, write what you saw in Notes, and treat the build as not releasable until it is understood.

---

## Run record

| Field | Value |
| --- | --- |
| Date / time | |
| Tester | |
| Backend commit (`git rev-parse --short HEAD` in `scd-backend`) | |
| Mobile commit (`git rev-parse --short HEAD` in `social_commerce_app`) | |
| Device / emulator | |
| RUN STATUS (ALL PASS / FAILED AT STEP N) | |

---

## 0. Before you start (about 3 minutes)

**0.1 Backend is up.** In PowerShell, from `D:\Cavallo\scd-backend`:

```powershell
docker compose up -d
docker compose ps
```

Every service must show `Up` (or `healthy`). Then open `http://localhost:8095/health/` in a browser. Expected: an OK response (HTTP 200), not an error page.

**0.2 App is running (dev build).** In PowerShell, from `D:\Cavallo\social_commerce_app`:

```powershell
flutter run
```

- Android emulator: the app reaches the backend through `10.0.2.2:8095` automatically, no extra flag.
- Real phone: `flutter run --dart-define=API_BASE_URL=http://<YOUR-PC-LAN-IP>:8095`.
- This checklist assumes a dev/debug build, because several screens are reached through the Home screen's debug menu (the three-dots menu with tooltip "Debug menu").

**0.3 Test image.** The device/emulator gallery must contain at least one picture (Step 3 picks one).

**0.4 Choose a run stamp.** Pick a short unique stamp for this run, for example today's date and time as `1003-0930`. Wherever you see `<STAMP>` below, use it. Using a new stamp each run avoids "email already exists" errors and makes the Step 7 search unambiguous.

**0.5 Test data for this run** (fill in the stamp, then use these values verbatim):

| Item | Value |
| --- | --- |
| Business email | `smoke-biz-<STAMP>@example.com` |
| Customer email | `smoke-cust-<STAMP>@example.com` |
| Password (both accounts) | `SmokeTest!2026pass` |
| Business name | `Smoke Biz <STAMP>` |
| Business type | Trader |
| Country / City | Egypt / Cairo |
| Post caption | `Smoke post <STAMP>` |
| Moderator account | created once in the "Moderator account" section below (`smoke-mod@example.com`, same password) |

If the password is rejected by the app's password rules, use any stronger password and write it down in Notes of Step 1.

---

## Step 1 - Register a new Business account

**Action:**
1. On the login screen tap **Don't have an account? Register**. The screen is titled "Register".
2. Enter Email `smoke-biz-<STAMP>@example.com`, Password and Confirm password `SmokeTest!2026pass`.
3. In "Account type" select **Business**.
4. Tap **Create account**.

**Expected:** registration succeeds, you are signed in automatically, and you land on the screen titled **"Complete your business profile"**. No red error text on the form.

- [ ] PASS
- [ ] FAIL

Notes: ______________________________________________

---

## Step 2 - Complete business onboarding

**Action:**
1. Business name: `Smoke Biz <STAMP>`.
2. Business type: **Trader**.
3. Country: `Egypt`. City: `Cairo`.
4. Leave phone and description empty (both are optional).
5. Tap **Complete profile**.

**Expected:** you land on the **Home** screen (title "Home"). You are not sent back to the onboarding screen.

- [ ] PASS
- [ ] FAIL

Notes: ______________________________________________

---

## Step 3 - Create a Post with an image

**Action:**
1. On Home tap the three-dots menu at the top right (tooltip "Debug menu") and choose **Business Console (debug)**. You land on the Products tab.
2. In the bottom bar tap **Posts/Reels**. The screen is titled **"My Content"**.
3. Tap **New Post**. The screen is titled "New post".
4. Caption: `Smoke post <STAMP>`.
5. Tap **Choose image** and pick any picture from the gallery. A preview appears and the button changes to "Change image".
6. Tap **Post**.

**Expected:** you return to "My Content" and the new post is listed with an amber **"Under review"** badge. Pull down to refresh: it is still "Under review" (nobody has approved it yet).

- [ ] PASS
- [ ] FAIL

Notes: ______________________________________________

---

## Moderator account (one-time setup, about 1 minute)

The moderator is a normal user flagged as moderator in the database. It is created once and reused by every run (the command is safe to repeat: it resets the same account).

In PowerShell, from `D:\Cavallo\scd-backend`, run this as ONE line:

```powershell
docker compose exec web python manage.py shell -c "from django.contrib.auth import get_user_model; from django.contrib.auth.models import Group; U=get_user_model(); e='smoke-mod@example.com'; u=U.objects.filter(email__iexact=e).first() or U(username=e,email=e,account_type='customer'); u.set_password('SmokeTest!2026pass'); u.is_moderator=True; u.save(); u.groups.add(Group.objects.get(name='Moderator')); v=U.objects.get(pk=u.pk); print('moderator ready', v.email, v.is_moderator, v.has_perm('accounts.can_moderate_content'))"
```

**Expected output (last line):** `moderator ready smoke-mod@example.com True True`

If the second value is `False`, the "Moderator" group has no permission: run `docker compose exec web python manage.py migrate` and repeat. If you get `Group matching query does not exist`, the same migrate fixes it.

Moderator login: email `smoke-mod@example.com`, password `SmokeTest!2026pass`.

---

## Step 4 - Moderator approves the pending post

**Action:**
1. Sign out the Business user: on Home open the three-dots menu (tooltip "Debug menu") and choose **Logout (debug)**. You return to the "Login" screen.
2. Log in as the moderator: Email `smoke-mod@example.com`, Password `SmokeTest!2026pass`, tap **Log in**. You land on Home.
3. Open the three-dots menu and choose **Moderation queue (debug)**. The screen is titled **"Moderation queue"**.
4. Find the row whose preview text is `Smoke post <STAMP>` (its type line says Post). If it is not listed, tap the **Refresh** icon in the top bar once.
5. Tap that row. The screen is titled **"Review content"** and the Preview shows `Smoke post <STAMP>`.
6. Tap **Approve**.

**Expected:** a snackbar says **"Item approved"** and you are back on the "Moderation queue". The row for `Smoke post <STAMP>` is gone from the list (tap **Refresh** to confirm it stays gone).

- [ ] PASS
- [ ] FAIL

Notes: ______________________________________________

---

## Step 5 - Business sees the post as Live

**Action:**
1. Sign out the moderator: three-dots menu -> **Logout (debug)**.
2. Log in as the Business: Email `smoke-biz-<STAMP>@example.com`, Password `SmokeTest!2026pass`, tap **Log in**.
3. Three-dots menu -> **Business Console (debug)** -> bottom bar **Posts/Reels** ("My Content").
4. Pull down on the list to refresh.

**Expected:** the post `Smoke post <STAMP>` now shows a green **"Live"** badge (no longer amber "Under review").

- [ ] PASS
- [ ] FAIL

Notes: ______________________________________________

---


## Step 6 - Register a new Customer account

**Action:**
1. Sign out the Business user: three-dots menu (tooltip "Debug menu") -> **Logout (debug)**.
2. On the login screen tap **Don't have an account? Register**.
3. Email `smoke-cust-<STAMP>@example.com`, Password and Confirm password `SmokeTest!2026pass`.
4. In "Account type" keep **Customer** selected (it is the default).
5. Tap **Create account**.

**Expected:** registration succeeds, you are signed in automatically and you land on **Home**. You are NOT sent to "Complete your business profile" (that screen is for Business accounts only). The three-dots menu has no "Business Console (debug)" and no "Moderation queue (debug)" entry.

- [ ] PASS
- [ ] FAIL

Notes: ______________________________________________

---

## Step 7 - Customer finds the Business through Search

**Action:**
1. Three-dots menu -> **Search (debug - no nav entry point yet)**. The screen is titled **"Search"**.
2. In the box with the hint "Search traders, factories, products..." type `Smoke Biz <STAMP>`.
3. Wait about one second (the search starts by itself after you stop typing).

**Expected:** the results list shows a business row named **`Smoke Biz <STAMP>`**. It is not the "No results found" message.

- [ ] PASS
- [ ] FAIL

Notes: ______________________________________________

---

## Step 8 - Customer sees the published Post on the Business profile

**Action:**
1. Tap the `Smoke Biz <STAMP>` result. The screen is titled **"Business"** and shows the business name.
2. Scroll to the **Posts** section.

**Expected:** the section lists a post card with the caption **`Smoke post <STAMP>`** and the picture you chose in Step 3, rendered normally (no broken-image icon, no error message). It is not the "This business hasn't shared any posts yet." message.

- [ ] PASS
- [ ] FAIL

Notes: ______________________________________________

---

## Result of this run

Fill in the "Run record" table at the top of this file. Then:

- **ALL 8 steps PASS:** the build passes the smoke test. Write `ALL PASS` in RUN STATUS.
- **ANY step FAIL:** write `FAILED AT STEP N` in RUN STATUS, describe what you saw in that step's Notes (screen, exact text, and the backend log if useful), and do not release until it is understood.

Backend log while reproducing a failure (from `D:\Cavallo\scd-backend`):

```powershell
docker compose logs --tail=100 web
```

## Known limits of this checklist

- Dev/debug build only: several screens are reached through the Home three-dots "Debug menu". When real navigation replaces those entries, update Steps 3, 4, 5 and 7.
- It does not cover live Paymob payments or live push notifications (those stay open items in `PROJECT_PROGRESS.md`).
- It checks the happy path only. Rejection, IDOR and permission cases are covered by the automated suites from P-096 and P-097.
