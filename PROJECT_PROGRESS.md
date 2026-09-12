# Project Progress — Social Commerce Discovery Platform (Backend)

## Part P-000 — Backend Repository Skeleton + Docker Compose Dev Environment

**Status: PARTIALLY VALIDATED**

Per the "No Fake Completion" rule: this cannot be marked COMPLETE because the validation environment used to build this part has no Docker daemon available, so `docker compose up` itself was never actually run. Everything `docker compose` would orchestrate was instead validated directly against real Postgres 16 and Redis 7 instances (installed locally, not via Docker) to confirm the Django/Celery configuration itself is correct:

| Check                                                                                                                     | Result                                                 |
| ------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------ |
| `python manage.py check`                                                                                                  | ✅ Passed, 0 issues                                     |
| `python manage.py migrate` against real Postgres 16                                                                       | ✅ Applied all 17 built-in migrations cleanly           |
| `python manage.py createsuperuser` (non-interactive)                                                                      | ✅ Created                                              |
| Dev server boots, `GET /admin/login/`                                                                                     | ✅ HTTP 200, "Django site admin" login page             |
| `celery -A config worker` against real Redis 7                                                                            | ✅ Connected, registered `debug_task`, reported `ready` |
| Settings read `SECRET_KEY` / `DATABASE_URL` / `REDIS_URL` / etc. only from env (via django-environ), no hardcoded secrets | ✅                                                      |

**Not validated:** `docker compose up` / `docker compose ps` / the Dockerfile build itself, since no Docker daemon was available in this environment. The compose file and Dockerfile follow standard, well-tested patterns (official `postgres:16` / `redis:7` images, healthchecks gating `depends_on` for the three Django-based services), but you should run `docker compose up -d && docker compose ps` yourself once to confirm the 5 containers actually come up healthy on your machine before treating this part as fully done.

### Port Configuration

The Docker Compose development environment uses the following host-to-container port mappings:

| Service   | Host Port | Container Port | Mapping     |
| --------- | --------: | -------------: | ----------- |
| **web**   |    `8090` |         `8000` | `8090:8000` |
| **redis** |    `6381` |         `6379` | `6381:6379` |
| **db**    |    `5435` |         `5432` | `5435:5432` |

The custom host ports are intentional to avoid conflicts with services already using the standard ports on the host machine.

**Important:** These mappings only affect access from the host machine. Docker-internal service communication continues to use the container ports:

* Django `web` → PostgreSQL `db:5432`
* Django/Celery → Redis `redis:6379`
* Host → Django web: `localhost:8090`
* Host → PostgreSQL: `localhost:5435`
* Host → Redis: `localhost:6381`

## What now exists

* Django project `config` (single project, no apps yet — per scope).
* `docker-compose.yml`: `db` (`postgres:16`) exposed on host port `5435` → container port `5432`; `redis` (`redis:7`) exposed on host port `6381` → container port `6379`; `web` (`manage.py runserver`) exposed on host port `8090` → container port `8000`; `celery_worker`; `celery_beat` — the three Django-based services wait on `db`/`redis` healthchecks.
* `Dockerfile`: `python:3.12-slim`, installs `requirements.txt`, default CMD is `runserver` (overridden per-service in compose for the Celery services).
* `requirements.txt`: Django 5.2 LTS (current LTS as of Sep 2026, supported through Apr 2028 — Django 6.1 exists but is not LTS; 6.2 LTS isn't out until ~Apr 2027), DRF, simplejwt, Celery, redis, psycopg2-binary, django-cors-headers, channels, channels-redis, sentry-sdk, django-environ.
* `config/settings.py`: single file (base/dev/staging/prod split is P-004), but every value is read from the environment via `django-environ`, grouped into clearly separated sections (DB, Redis/cache/channels, Celery, DRF/JWT, CORS, Sentry) so that later split is a mechanical extraction, not a rewrite. No media/local-disk storage setting was added, per the architecture rule.
* `config/celery.py` + `config/__init__.py`: standard Django+Celery wiring, includes one `debug_task` for smoke-testing the worker.
* `.env.example`: every var the compose stack needs, including the `POSTGRES_*` vars the `db` service itself reads and the configured host/container port values where applicable.
* `.gitignore`: excludes `.env`, `db.sqlite3`, `staticfiles/`, `media/`, caches, editor/OS junk.

## What the next part (P-001) can assume is available

* A Django project named `config` at the repo root with `manage.py` next to it (i.e. `docker compose exec web python manage.py ...` is the standard command pattern for everything going forward).
* Settings are env-driven; any new setting P-001 needs should be added to both `config/settings.py` (via `env(...)`) and `.env.example`, not hardcoded.
* `DATABASES["default"]` is already wired to Postgres via `DATABASE_URL`; P-001 can add its app(s) to `INSTALLED_APPS` and run `makemigrations` immediately.
* Redis is already configured for both the cache backend and the Channels layer (`CHANNEL_LAYERS`) under the same `REDIS_URL`.
* Celery is wired (`config/celery.py`) and autodiscovers `tasks.py` in any app added to `INSTALLED_APPS` — no further Celery setup needed to add the first real task.
* No app beyond the default project exists yet — P-001 is the first part that creates one.
* From the host machine, the Django development server is available at `http://localhost:8090/`; PostgreSQL is exposed at `localhost:5435`; Redis is exposed at `localhost:6381`.

---

## Part P-001 — Flutter Project Skeleton + Folder Structure

**Status: VALIDATED ON ANDROID — iOS NOT VALIDATED (no Mac available)**

The skeleton was originally hand-authored in an environment with no Flutter SDK and no network access to `pub.dev`. The user then took the delivered files to a real Windows machine with Flutter 3.29.3 installed and **actually completed setup and validation end-to-end on Android**, fixing several real issues along the way (documented below so nobody re-hits them). iOS could not be validated because iOS builds require a Mac/Xcode, which wasn't available.

### Validation results (real, on user's machine)

| Check                                                              | Result                                                                 |
| -------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| Feature-first folder skeleton matches architecture Section 12 exactly | ✅ Confirmed present and unchanged (`lib/core/*`, 9 `lib/features/*/{data,domain,presentation}/`, `lib/routing/`) |
| `flutter create --platforms=ios,android` run on a real machine, `android/`/`ios/`/`test/` merged in | ✅ Done |
| `flutter pub get`                                                    | ✅ Resolved successfully after downgrading `flutter_riverpod` from `^3.4.3` to `^3.3.2` (the SDK-installed Flutter 3.29.3 / Dart 3.7.2 couldn't satisfy `^3.4.3`, which needs Flutter ≥3.47.3) |
| `flutter analyze`                                                     | ✅ **No issues found!** |
| `flutter run` on Android emulator (Pixel 2, Android 16 / API 36)      | ✅ **App launched successfully** — placeholder screen shows "Social Commerce Discovery Platform" |
| `flutter run` on iOS simulator                                        | ❌ Not run — no Mac/Xcode available to the user |

### Issues hit during real-machine setup, and the fixes (for anyone repeating this)

1. **`flutter_riverpod: ^3.4.3` failed to resolve** against the user's Flutter 3.29.3 (needs Flutter ≥3.47.3). Fixed by running `flutter pub add flutter_riverpod:^3.3.2`, which Flutter's own error message suggested. **`pubspec.yaml` now pins `flutter_riverpod: ^3.3.2`**, not `^3.4.3` — anyone with an older Flutter SDK should keep it at `^3.3.2` until they upgrade Flutter.
2. **`test/widget_test.dart`**, copied over from the throwaway `flutter create` scaffold project, still imported the scaffold's own package name and referenced a nonexistent `MyApp` class. Rewritten to import `package:social_commerce_app/main.dart` and test for `SocialCommerceApp` / the placeholder text, with an unused `material.dart` import removed.
3. **`lib/main.dart` went missing** after copying files onto the real machine (never made it into the working copy). Recreated from the original source — no logic changes from what P-001 specified.
4. **`flutter_secure_storage` requires a higher Android SDK/NDK than the default `flutter create` scaffold sets.** `android/app/build.gradle.kts` needed:
   - `compileSdk = 36` (was `flutter.compileSdkVersion`, which resolved lower)
   - `ndkVersion = "27.0.12077973"` (was `flutter.ndkVersion`)
   - `minSdk = 23` (was `flutter.minSdkVersion` / effectively 21) — `flutter_secure_storage`'s manifest requires `minSdk ≥ 23`, and the manifest merger fails below that.
5. **Package name mismatch (`com.example.social_commerce_app_tmp` vs `com.example.social_commerce_app`).** Because the native `android/` folder was generated from a *differently-named* throwaway scaffold project (`social_commerce_app_tmp`) and then copied over, both `build.gradle.kts` (`namespace` / `applicationId`) and `MainActivity.kt` (its `package` declaration **and** its folder path under `android/app/src/main/kotlin/com/example/...`) still referenced the old `_tmp` name. This caused a `ClassNotFoundException` for `MainActivity` at runtime even though the build succeeded. Fixed by:
   - Setting `namespace` and `applicationId` to `com.example.social_commerce_app` in `build.gradle.kts`.
   - Moving `MainActivity.kt` from `.../kotlin/com/example/social_commerce_app_tmp/` to `.../kotlin/com/example/social_commerce_app/`.
   - Updating the `package` declaration inside `MainActivity.kt` to match.
   - Uninstalling the stale APK from the emulator (`adb uninstall com.example.social_commerce_app`) before reinstalling, since Android was still running the old mismatched build.

   **Lesson for future parts:** when generating the native scaffold via a throwaway `flutter create <name>_tmp` project and copying `android/`/`ios/` into the real project, always rename the package/namespace/bundle-id throughout (`build.gradle.kts`, `MainActivity.kt`'s path and `package` line, and the iOS equivalent — bundle identifier in Xcode project settings) — don't just copy the folders as-is.

### What now exists (Flutter side)

* `social_commerce_app/pubspec.yaml`: package name `social_commerce_app`, deps `flutter_riverpod: ^3.3.2` (downgraded per above), `dio: ^5.11.1` (resolved), `flutter_secure_storage: ^10.3.2` (resolved), `cupertino_icons: ^1.0.8`; dev dep `flutter_lints: ^5.0.0`.
* `lib/main.dart`: app wrapped in `ProviderScope`, placeholder `MaterialApp` showing "Social Commerce Discovery Platform" — confirmed rendering correctly on-device. No routing, theming, networking, or storage logic (out of scope for this part).
* `lib/core/{network,storage,config,widgets}/`: empty, `.gitkeep` placeholders — P-010 onward builds directly inside these.
* `lib/features/{auth,feed,stories,search,business_profile,products,chat,notifications,business_console}/{data,domain,presentation}/`: empty, `.gitkeep` placeholders — matches architecture Section 12 exactly.
* `lib/routing/`: empty, `.gitkeep` placeholder.
* `test/widget_test.dart`: passes `flutter analyze`; a real smoke test for the placeholder screen (not yet run via `flutter test`, only `flutter analyze` — see follow-up below).
* `analysis_options.yaml`: standard `flutter_lints` include.
* `.gitignore`: excludes `.dart_tool/`, `build/`, `pubspec.lock`, editor/OS junk.
* `android/`, `ios/`, native scaffold: now exist (generated via `flutter create` on the user's machine), with `android/app/build.gradle.kts` and `MainActivity.kt` corrected per issue #5 above.

### Still open before this part is 100% closed

* **iOS validation is outstanding** — needs a Mac with Xcode to run `flutter run` on an iOS Simulator. Until then this part stays short of fully COMPLETE per the original acceptance criteria ("launches on both an iOS simulator and an Android emulator").
* `flutter test` (actually running the widget test, not just `flutter analyze` on it) hasn't been confirmed — worth a quick `flutter test` run to be sure.
* Double-check the **iOS** equivalent of issue #5 (bundle identifier / Xcode project still possibly referencing `social_commerce_app_tmp`) once a Mac is available — the same rename mistake likely exists there too and hasn't been touched yet.

## What the next Flutter part can assume is available

* `lib/main.dart` already wraps the app in `ProviderScope` — no retrofit needed for the first real provider.
* The exact feature folder set from Section 12 already exists (empty) under `lib/features/`; new feature code goes inside the matching `data/domain/presentation/` subfolders, not into new top-level folders.
* `lib/core/network/`, `lib/core/storage/`, `lib/core/config/`, `lib/core/widgets/` exist empty — P-010 onward (Dio client setup, secure-storage wrapper, app config, shared widgets) builds directly inside these, no restructuring needed.
* `pubspec.yaml` already declares `flutter_riverpod` (`^3.3.2`), `dio`, `flutter_secure_storage` as dependencies, already resolved and building on Android — do not re-add them or bump `flutter_riverpod` past `^3.3.2` without first upgrading the project's Flutter SDK to ≥3.47.3.
* `android/` is real, builds, and runs — package name is consistently `com.example.social_commerce_app` throughout (`build.gradle.kts` namespace/applicationId and `MainActivity.kt`'s path/package). Any future native Android changes should keep using this exact package name.
* `ios/` exists but has **not** been verified to build/run or renamed away from the throwaway scaffold's identifiers — treat it as unverified until someone with a Mac checks and fixes it the same way Android was fixed.

## Part P-002 — CI Skeleton (Lint/Test Pipelines) for Both Repos

**Status: COMPLETE**

Both workflows were pushed to their respective repos and validated live on GitHub Actions — both runs completed green.

### Backend (`cavallo-app` repo)

* `.github/workflows/backend-ci.yml`: on push/PR — checkout, Python 3.12 (matches Dockerfile's `python:3.12-slim`), install `requirements.txt`, spin up real `postgres:16` + `redis:7` service containers (health-checked), `flake8`, `black --check`, `python manage.py check`, `pytest`.
* `flake8` and `black` are installed directly in the CI step (not added to `requirements.txt`, which stays runtime-only).
* **Issue hit + fix:** `flake8`'s default max line length (79) conflicted with `black`'s (88), which caused false-positive `E501` failures on lines `black` had already formatted correctly. Fixed by adding a `.flake8` config file at the repo root:
```ini
  [flake8]
  max-line-length = 88
  extend-ignore = E203, W503
```
* **Issue hit + fix:** one line in `config/settings.py` (`AUTH_PASSWORD_VALIDATORS` → `UserAttributeSimilarityValidator`) is a single unbreakable string that stays at 90 characters even after `black` explodes the dict onto multiple lines — `black` cannot split string literals. Suppressed with an inline `# noqa: E501` on that specific line, since it's an unavoidable long line, not a real style violation.
* `pytest`: no test suite exists yet (Phase 0), so the step is configured to accept pytest's exit code 5 ("no tests collected") as a pass: `pytest || [ "$?" -eq 5 ]`. Any future part that adds real tests plugs directly into this step — no pipeline changes needed.
* Validated live: `https://github.com/Ahmed2132003/cavallo-app/actions` — **Backend CI run #4, green, all steps passed** (checkout, install deps, flake8, black --check, Django system check, pytest).

### Flutter (`cavallo-mobile` repo)

* `.github/workflows/flutter-ci.yml`: on push/PR — checkout, Flutter pinned to `3.29.3` (the exact version P-001 was validated against on the real machine, per P-001's handoff notes — not just `>=3.27.0` from `pubspec.yaml`'s loose constraint), `flutter pub get`, `flutter analyze`, `flutter test`.
* No issues hit — passed on the first run.
* Validated live: `https://github.com/Ahmed2132003/cavallo-mobile/actions` — **Flutter CI run #1, green, all steps passed**.

### Repository note (deviation from original plan)

The two repos now live at:
* Backend: `https://github.com/Ahmed2132003/cavallo-app`
* Flutter: `https://github.com/Ahmed2132003/cavallo-mobile`

(Original plan referred to the backend repo generically as `scd-backend` — the actual GitHub repo name is `cavallo-app`. Functionally identical, just noting the real name for anyone following the docs literally.)

### What the next parts (P-003 onward) can assume about CI being in place

* Every push/PR to either repo automatically triggers its respective workflow — no manual trigger needed.
* Backend: any new Django app/module just needs to pass `flake8`/`black --check`/`manage.py check` to merge cleanly; real `pytest` tests can be dropped in anywhere and will be picked up automatically, no CI file changes required.
* Backend: if a new setting or code line is provably unavoidable in length (e.g., a long fully-qualified import path or URL), the precedent is an inline `# noqa: E501` on that exact line — not raising the global `max-line-length` further.
* Flutter: any new widget/feature code just needs to pass `flutter analyze` and existing/new tests under `test/` to merge cleanly.
* Neither workflow has a deploy step yet (that's Phase 21 / P-220+) and neither references any secrets — both are pure lint/test gates.

## Part P-003 — Environment/Config Management & Secrets Handling (Both Sides)

**Status: COMPLETE**

Validated live on both repos — pushed and confirmed present on GitHub via a fresh clone
of each after push (not just checked locally).

### Backend (`cavallo-app`)

* `.env.example` extended with every category from the plan's Section 7: `JWT_ACCESS_TTL_MINUTES` (default `15`), `JWT_REFRESH_TTL_DAYS` (default `7`), `SENTRY_DSN`, `OBJECT_STORAGE_PROVIDER` / `OBJECT_STORAGE_BUCKET` / `OBJECT_STORAGE_KEY` / `OBJECT_STORAGE_SECRET` / `OBJECT_STORAGE_REGION`, `FCM_PROJECT_ID` / `FCM_SERVICE_ACCOUNT_JSON_PATH`, `PAYMOB_API_KEY` / `PAYMOB_WEBHOOK_SECRET`. Each blocked category is commented inline with `# INPUT REQUIRED — see architecture Section 7`.
* `CONFIG.md` created at the repo root: documents every env var, which part of the codebase consumes it, and whether it's required to run the app locally (only `SECRET_KEY`/`DEBUG`/`ALLOWED_HOSTS`/`DATABASE_URL`/`REDIS_URL`/the `POSTGRES_*` init vars are required — everything else is optional in dev, with the consequence of leaving it blank spelled out per category). Includes an explicit cross-check confirming every Section 7 blocked item has a corresponding named placeholder.
* No SDK wiring was added (no `sentry_sdk.init()`, no storage backend, no FCM/Paymob client code) — per scope, that happens in each value's own later part (Sentry in P-024, etc.). This part only reserves where the values will live.
* Confirmed via a fresh `git clone` after push that `.env.example` and `CONFIG.md` are both live on `main` with the expected content.

### Flutter (`cavallo-mobile`)

* `lib/core/config/app_config.dart` created: `AppConfig.environment` (enum `dev`/`staging`/`prod`, read via `String.fromEnvironment('ENVIRONMENT', defaultValue: 'dev')`) and `AppConfig.apiBaseUrl`.
* `apiBaseUrl` always honors an explicit `--dart-define=API_BASE_URL=...` override first. Without one, in `dev` it auto-picks a platform-correct default against the real backend port documented in P-000 (host port `8090`, not the plan's literal `8000` placeholder — corrected here since `8090` is what's actually running): `http://10.0.2.2:8090` on the Android emulator (Android's documented host-machine alias), `http://localhost:8090` everywhere else (iOS Simulator, web, desktop). In `staging`/`prod` it deliberately **throws `StateError`** if `API_BASE_URL` wasn't passed explicitly, so a release build can never silently fall back to a local dev URL.
* `CONFIG.md` created at the repo root: documents both `--dart-define` values, the auto-detection table above, exactly how to reach a real physical device on the same Wi-Fi (LAN IP override — neither `localhost` nor `10.0.2.2` works from a real device), and the `flutter build ... --dart-define=...` invocation pattern for staging/prod.
* Validated live on the user's machine: `flutter pub get` + `flutter analyze` → **No issues found!**
* Confirmed via a fresh `git clone` after push that `CONFIG.md` and `lib/core/config/app_config.dart` are both live on `main`.

### What's still pending real values (blocked on external input)

Every one of these already has a named, documented placeholder — nothing here blocks any other part from proceeding. They just need real values before the parts that actually consume them can be fully validated end-to-end:

* `SENTRY_DSN` — needed before **P-024 (monitoring)** can call `sentry_sdk.init()` for real.
* `OBJECT_STORAGE_PROVIDER` / `OBJECT_STORAGE_BUCKET` / `OBJECT_STORAGE_KEY` / `OBJECT_STORAGE_SECRET` / `OBJECT_STORAGE_REGION` — needed before **Phase 2 (storage)** can be fully validated; until then, dev falls back to local disk and product/post/reel/story media won't survive a container rebuild.
* `FCM_PROJECT_ID` / `FCM_SERVICE_ACCOUNT_JSON_PATH` — needed before **Phase 13 (notifications)** can send real push notifications.
* `PAYMOB_API_KEY` / `PAYMOB_WEBHOOK_SECRET` — needed before **Phase 15 (payments)**, if/when that phase is actually built (the current MVP scope has no in-app payment/checkout at all — deals happen outside the platform).

**Ask Ahmed for these specific values before treating those specific later parts as done** — nothing to ask for right now to unblock P-004 onward.

### What the next parts can assume is available

* Every config value any future part needs already has a reserved name in `.env.example` (backend) or a documented `--dart-define` flag (`CONFIG.md`, Flutter) — add new values to these two docs, don't invent an ad hoc read.
* No code anywhere in either repo reads an environment variable or `String.fromEnvironment` key that isn't listed in its repo's `.env.example`/`CONFIG.md`.
* Flutter feature code should call `AppConfig.apiBaseUrl` (once the Dio client exists, from P-010 onward) rather than hardcoding a URL or reading `String.fromEnvironment` directly.

Part P-004 — Core Network Layer (Dio Client, Interceptors, Error Mapping)

Status: VALIDATED

Actually run on the real machine (Flutter 3.29.3 / Dart 3.7.2, Windows, D:\Cavallo\social_commerce_app):

Check	Result
flutter pub get	✅ Changed 2 dependencies! — http_mock_adapter 0.6.1 resolved cleanly against flutter_riverpod 3.3.2, no conflict
flutter test test/core/network/	✅ +14: All tests passed! (after two fixes below)
flutter analyze	✅ No issues found!

Two fixes made during real-machine validation:

lib/core/network/interceptors/error_interceptor.dart — dio 5.11.1 (resolved version) adds a DioExceptionType.transformTimeout enum value that didn't exist when this part was authored, so the switch in _mapToFailure wasn't exhaustive and failed to compile. Fixed by adding case DioExceptionType.transformTimeout: to the same case group as connectionTimeout/sendTimeout/receiveTimeout/connectionError (all map to NetworkFailure, since a transform failure is a connection-layer problem from the caller's point of view).
test/core/network/dio_client_test.dart — the "3 interceptors in order" test asserted dio.interceptors.length == 3, but Dio() itself prepends an internal ImplyContentTypeInterceptor on construction, making the real count 4. Fixed by checking that LoggingInterceptor/AuthInterceptor/ErrorInterceptor are present (via indexWhere) and appear in that relative order, instead of asserting an exact total count.

Confirmed present and correct on disk during validation: lib/core/network/{api_failure.dart, dio_client.dart}, lib/core/network/interceptors/{auth_interceptor.dart, error_interceptor.dart, logging_interceptor.dart}, test/core/network/{auth_interceptor_test.dart, dio_client_test.dart, error_interceptor_test.dart}, and the http_mock_adapter: ^0.6.1 dev dependency line in pubspec.yaml.

What now exists
lib/core/network/api_failure.dart — sealed ApiFailure with final class variants ValidationFailure(message, fields), AuthFailure(message), NetworkFailure(message), ServerFailure(message), UnknownFailure(message).
lib/core/network/interceptors/logging_interceptor.dart — LoggingInterceptor (a real Interceptor subclass, not a bare LogInterceptor() instance) that checks AppConfig.environment == AppEnvironment.dev itself on every request/response/error, so it's always safe to attach unconditionally — staging/prod builds silently no-op instead of ever leaking a request/response body (which can contain tokens) into a release log.
lib/core/network/interceptors/auth_interceptor.dart — AuthInterceptor takes Future<String?> Function() getToken in its constructor, attaches Authorization: Bearer <token> only when the token is non-null and non-empty.
lib/core/network/interceptors/error_interceptor.dart — ErrorInterceptor catches every DioException, parses the backend's {"error": {"code","message","fields"}} envelope (Section 10), maps by status/type (400→Validation, 401/403→Auth, timeout/connection error/connection-error-adjacent (incl. transformTimeout)/no-response→Network, 5xx→Server, else→Unknown), and rethrows a DioException carrying the mapped ApiFailure in .error. An empty/missing error.message in the envelope falls back to a sane default string rather than surfacing an empty string.
lib/core/network/dio_client.dart — dioClientProvider (Provider<Dio>), baseUrl from AppConfig.apiBaseUrl, interceptors attached in order logging → auth → error exactly as specified. authTokenGetterProvider (Provider<AuthTokenGetter>, where typedef AuthTokenGetter = Future<String?> Function()) currently resolves to a no-op () async => null placeholder — this is the exact signature P-005 must override, e.g. authTokenGetterProvider.overrideWithValue(secureStorage.readAuthToken), no changes to this file needed.
test/core/network/error_interceptor_test.dart — 8 tests covering the acceptance criteria plus two extra edge cases: 400+fields→ValidationFailure(fields correct), 401→AuthFailure, 403→AuthFailure, 5xx→ServerFailure, connection timeout→NetworkFailure, connection error→NetworkFailure, unparseable body→UnknownFailure, 400 with a non-envelope-shaped body→ValidationFailure with empty fields (doesn't crash parsing).
test/core/network/auth_interceptor_test.dart — 3 tests: header attached with a token, header omitted when token is null, header omitted when token is empty string.
test/core/network/dio_client_test.dart — 3 tests: the default provider wires LoggingInterceptor → AuthInterceptor → ErrorInterceptor in that relative order (checked via indexWhere, not an exact .length, since Dio() itself prepends an internal ImplyContentTypeInterceptor that isn't ours); dioClientProvider is overridable with a fake Dio in a bare ProviderContainer() (proves it's usable with zero feature code, per the Definition of Done); authTokenGetterProvider defaults to a null-returning getter.
pubspec.yaml — added http_mock_adapter: ^0.6.1 as a dev dependency (mocks Dio's HTTP adapter for the tests above; this is the package the part's own execution prompt named as an acceptable option alongside raw Dio test utilities).
Still open before this part is 100% closed
 flutter pub get — resolved cleanly, no conflicts.
 flutter test test/core/network/ — all 14 tests green.
 flutter analyze — clean, no issues.
 Pushed to github.com/Ahmed2132003/cavallo-mobile — commit 2751e02 on main. Independently verified via a fresh git clone after push: all 9 files present at the correct paths, http_mock_adapter in pubspec.yaml, and both real-machine fixes (transformTimeout case, indexWhere-based interceptor-order test) confirmed in the pushed content. This part is fully closed.
Auth-token-getter interface (for P-005 and P-022 to match exactly)
dart
typedef AuthTokenGetter = Future<String?> Function();

P-005's secure-storage wrapper should expose a method with this exact shape (e.g. Future<String?> readAuthToken()), and the app's composition root should override authTokenGetterProvider with it:

dart
ProviderScope(
  overrides: [
    authTokenGetterProvider.overrideWithValue(secureStorage.readAuthToken),
  ],
  child: const SocialCommerceApp(),
)

P-022 (token refresh) should implement its retry-on-401 logic as a separate interceptor added after AuthInterceptor (or as a wrapper around the same getToken function passed in) — nothing in P-004 needs to change to accommodate it, per the original scope note.

What the next parts can assume is available
Every feature's repository implementation should depend on dioClientProvider (never construct its own Dio) and catch DioException from it, reading .error as ApiFailure for a typed, pattern-matchable failure — never re-parse a raw response body for errors.
ApiFailure is a sealed class, so a switch over it in Dart 3 is exhaustiveness-checked by the analyzer — feature code should switch on it rather than using is chains.
P-005 only needs to provide a Future<String?> Function() and override authTokenGetterProvider — it does not need to touch dio_client.dart or any interceptor.
No feature-specific logic exists anywhere in lib/core/network/ — it stays feature-agnostic per the architecture rule; feature-specific error handling (e.g. "this 400 means the product is out of stock") belongs in the feature's own repository/use-case layer, built on top of the generic ValidationFailure this layer already produces.
Content
PROJECT IMPLEMENTATION MASTER PLAN.docx

DOCX

PDF

PROJECT_PROGRESS.md

MD

PART P-004 — Core Network Layer (Dio Client, Interceptors, Error Mapping) Part Metadata: Phase 1 | Priority: Critical | Complexity: Medium | Dependencies: P-001, P-003 | Parallelizable: No | Backend dependency: No | External input required: No Objective: A single, reusable Dio client in lib/core/n

PASTED

## Part P-005 — Core Storage (Secure Storage + Cache Abstraction)

**Status: VALIDATED (pending push confirmation)**

Actually run on the real machine (Flutter, Windows, D:\Cavallo\social_commerce_app):

| Check                                | Result                                                                 |
| ------------------------------------- | ------------------------------------------------------------------------ |
| `flutter pub get`                     | ✅ Changed 7 dependencies! — `shared_preferences 2.5.3` (+ its 6 platform/interface packages) resolved cleanly alongside the existing `flutter_riverpod 3.3.2` / `flutter_secure_storage 10.3.2` pins |
| `flutter test test/core/storage/`     | ✅ +16: All tests passed! (first run, no fixes needed)                    |
| `flutter analyze`                     | ✅ No issues found! (after one fix below)                                 |

### One fix made during real-machine validation

`lib/core/storage/secure_token_storage.dart` — the doc comment on `getAccessToken()`
had an inline code span (`` `Future<String?> Function()` ``) split across two comment
lines. The doc-comment parser doesn't treat a code span as closed until it sees the
closing backtick, so the unclosed `<String?>` on the first line was read as literal
HTML, tripping the `unintended_html_in_doc_comment` info-level lint. Fixed by
reflowing the comment so the whole code span (opening and closing backtick) sits on
one line:

```dart
/// Matches the `AuthTokenGetter` typedef (`Future<String?> Function()`)
/// from `lib/core/network/dio_client.dart` exactly — see the P-020
/// wiring note on this class.
```

**Lesson for future parts:** any doc comment containing a generic type in backticks
(`` `Future<T>` ``, `` `List<T>` ``, `` `Map<K, V>` ``, etc.) must keep the opening and
closing backtick on the same line — never let the type expression wrap onto the next
line, even if a formatter or IDE tries to.

### Real-version issue caught before it could even reach `flutter analyze`

The part spec (and the master plan) call for `AndroidOptions(encryptedSharedPreferences:
true)`. Checked against the actual `flutter_secure_storage` source at the version this
project's pubspec.yaml pins (`^10.3.1`, resolved `10.3.2`): that parameter is
**deprecated and ignored** as of 10.x — "EncryptedSharedPreferences is deprecated and
will be removed in v11 ... Remove this parameter - it will be ignored." Using it would
have compiled fine but thrown a second `deprecated_member_use` warning on `flutter
analyze` for zero actual effect. Used `AndroidOptions.defaultOptions` instead, which
already gets KeyStore-backed AES-GCM + RSA-OAEP encryption by default on 10.x — a
stronger, non-deprecated equivalent — so the architecture rule (KeyStore, never
plaintext SharedPreferences) is still fully satisfied.

### `shared_preferences` version pin

Not previously a dependency. Added `shared_preferences: ^2.5.3` — checked against this
project's pinned Flutter (3.29.3): `shared_preferences` 2.5.4+ requires Flutter
`>=3.35.0`, so pinning below that avoids the same class of resolution mismatch P-001
hit with `flutter_riverpod`. Confirmed live: `flutter pub get` resolved exactly `2.5.3`
(the newest version compatible with this SDK), not a newer incompatible release.
`^2.5.3` will still let `flutter pub get` pick up a newer compatible release
automatically once the project's Flutter SDK is upgraded past 3.35.

### What now exists

* `lib/core/storage/secure_token_storage.dart` — `SecureTokenStorage` class:
  `saveTokens({required String access, required String refresh})`,
  `getAccessToken()`, `getRefreshToken()`, `clear()`. Backed by
  `FlutterSecureStorage(aOptions: AndroidOptions.defaultOptions)`. Exposed as
  `secureTokenStorageProvider` (`Provider<SecureTokenStorage>`).
  `getAccessToken()`'s signature (`Future<String?> Function()`) matches P-004's
  `AuthTokenGetter` typedef exactly — confirmed directly against the real
  `dio_client.dart` in the repo, no adapter method needed.
* `lib/core/storage/cache_storage.dart` — abstract `CacheStorage` interface
  (`get<T>`, `set<T>`, `remove`, `clear`) plus `SharedPreferencesCacheStorage`
  implementation (JSON encode/decode via `dart:convert`). Exposed as
  `cacheStorageProvider` (`FutureProvider<CacheStorage>`, since
  `SharedPreferences.getInstance()` is itself async). Class-level doc comment
  states the "tokens never go through here" rule explicitly, plus the
  architectural reasoning (this file doesn't import `flutter_secure_storage`,
  and `secure_token_storage.dart` doesn't import `shared_preferences`).
* `test/core/storage/secure_token_storage_test.dart` — 8 tests: save→read
  round-trip, both null before anything saved, `clear()` wipes both, `clear()`
  is a no-op on empty storage, `saveTokens` overwrites a previous pair,
  `getAccessToken` is directly assignable to `AuthTokenGetter`, provider
  resolves a `SecureTokenStorage`, provider is overridable. Uses
  `FlutterSecureStorage.setMockInitialValues({})` per the part spec.
* `test/core/storage/cache_storage_test.dart` — 8 tests: Map round-trip, List
  round-trip, primitive round-trip, missing key returns null, `remove` deletes
  only the given key, `clear` wipes everything, `set` overwrites, provider
  resolves a `SharedPreferencesCacheStorage`. Uses
  `SharedPreferences.setMockInitialValues({})` per the part spec.
* `pubspec.yaml` — added `shared_preferences: ^2.5.3` (see version-pin note
  above).
* `lib/core/storage/.gitkeep` removed (folder is no longer empty).

### Still open before this part is 100% closed

* [ ] Push to `github.com/Ahmed2132003/cavallo-mobile` and confirm via a fresh
      `git clone` that all 4 files (2 lib + 2 test) and the `pubspec.yaml`
      change are present on `main` — same closing check every prior part in
      this file has used.

### What the next parts can assume is available

* `SecureTokenStorage.getAccessToken` can be handed straight to
  `authTokenGetterProvider.overrideWithValue(...)` in Part P-020 with zero
  adapter code — its signature already matches `AuthTokenGetter` from
  `dio_client.dart` exactly.
* `SecureTokenStorage.clear()` is what Part P-022 (refresh-token
  reuse/theft-detection) and the logout flow should call — no other part
  should touch `flutter_secure_storage` directly.
* `CacheStorage`/`SharedPreferencesCacheStorage` is ready for any
  non-sensitive cached data (e.g. the category tree). No feature-specific
  cache keys exist yet — each feature that uses it should namespace its own
  keys (e.g. `'categories_tree'`), since `CacheStorage` itself has no
  built-in namespacing.
* Nothing in `lib/core/network/` needed to change — P-004's
  `authTokenGetterProvider` placeholder is untouched until P-020 explicitly
  overrides it, exactly as P-004's own handoff note specified.
* `shared_preferences: ^2.5.3` is now a real dependency of this project —
  future parts needing it should not re-add it or bump it past `^2.5.3`
  without first upgrading the project's Flutter SDK to ≥3.35.0.

Part P-006 — Shared UI Widget Library Baseline (Design Tokens, Theming)

Status: COMPLETE — pushed to GitHub and validated

Confirmed by the user: this part was implemented, tested successfully on a real machine, and pushed to main.

Definition of Done
 All five widgets implemented and tested
 Theme applied in main.dart
 Placeholder-theme comment present and clear
 No feature-specific widget snuck into this part's scope
 Pushed to GitHub and confirmed present on main
What now exists
lib/core/config/app_theme.dart — AppTheme built via ColorScheme.fromSeed, neutral placeholder seed color, with the required // PLACEHOLDER THEME comment flagging it as pending real brand assets (project plan, Section 7 item 5).
lib/core/widgets/app_button.dart — AppButton (wraps ElevatedButton/FilledButton, label + onPressed + loading flag).
lib/core/widgets/app_text_field.dart — AppTextField (wraps TextFormField, label + controller + validator + obscureText + keyboardType).
lib/core/widgets/loading_indicator.dart — centered CircularProgressIndicator wrapper.
lib/core/widgets/error_state_widget.dart — error message + retry button (onRetry callback).
lib/core/widgets/empty_state_widget.dart — "nothing here yet" message + optional icon.
lib/main.dart updated to apply AppTheme via MaterialApp(theme: ...).
test/core/widgets/ — widget tests confirming each of the five widgets renders and its callbacks fire (e.g. AppButton.onPressed fires unless loading: true; ErrorStateWidget's retry button calls onRetry).

Note: the actual flutter test / flutter analyze output and any real-machine fixes weren't relayed for this part, so they aren't recorded here the way P-001/P-005 document theirs blow-by-blow. Send those over (pasted terminal output is enough) and I'll fold them into this section the same way.

What the next parts can assume is available
Every later feature-presentation part should use AppButton, AppTextField, LoadingIndicator, ErrorStateWidget, and EmptyStateWidget from lib/core/widgets/ instead of raw ElevatedButton/TextField/CircularProgressIndicator — flag any part that deviates from this.
AppTheme is neutral/placeholder — a future restyle part should be scheduled once real brand assets (Section 7, item 5) are available; no part before that restyle pass should hardcode colors that assume a final brand palette.
Content
PROJECT IMPLEMENTATION MASTER PLAN.docx

DOCX

PDF

PROJECT_PROGRESS.md

MD

PART P-006 — Shared UI Widget Library Baseline (Design Tokens, Theming) Part Metadata: Phase 1 | Priority: Medium | Complexity: Low | Dependencies: P-001 | Parallelizable: Yes | Backend dependency: No | External input required: Yes (brand/design assets — Section 7 item 5) Objective: A minimal Them

PASTED

## Part P-007 — Routing Skeleton (go_router) + Navigation Guard Stub

**Status: COMPLETE**

| Check                                        | Result                                                        |
| --------------------------------------------- | --------------------------------------------------------------- |
| `flutter pub get`                             | ✅ `go_router 15.1.3` resolved cleanly, no conflict with `flutter_riverpod 3.3.2` |
| `flutter analyze`                             | ✅ No issues found!                                              |
| `flutter test test/routing/`                  | ✅ All 6 tests passed                                            |
| `flutter test` (full suite)                   | ✅ All 48 tests passed — nothing from prior parts broke          |
| `flutter run` on Android emulator (Pixel 2)   | ✅ App launched, no exceptions (only standard emulator-only `libEGL`/`OnBackInvokedCallback` warnings, unrelated to this part) |
| Manual 12-screen debug cycle                  | ✅ Confirmed by Ahmed: splash → login → register → home → discover → search → businessProfile → productDetail → chatList → chatThread → notifications → businessConsole → back to splash, with all 3 `:id` params displaying correctly |
| Push to `github.com/Ahmed2132003/cavallo-mobile` | ✅ Commit `1be5fd0` on `main`. Independently re-verified via a fresh download of `main` after push: all files present at the correct paths, including the final `discover` folder layout and the corrected import in `app_router.dart` |

### Resolved: `discover` folder placement
Originally placed under `lib/features/feed/presentation/` as a stopgap
(Section 12's feature set from P-001 had no `discover` folder). Ahmed
confirmed a dedicated folder was the right call — moved to its own
`lib/features/discover/{data,domain,presentation}/`, matching the
`data/domain/presentation` convention every other feature uses.
`app_router.dart`'s import updated accordingly; `flutter analyze` and
`flutter test test/routing/` re-confirmed clean after the move.

### What now exists
* `lib/routing/route_names.dart` — all 12 route names/paths as constants
  (`splash`, `login`, `register`, `home`, `discover`, `search`,
  `businessProfile` (`/business/:id`), `productDetail` (`/product/:id`),
  `chatList`, `chatThread` (`/chat/:id`), `notifications`,
  `businessConsole`), plus the shared `idParam` key.
* `lib/routing/app_router.dart` — `appRouterProvider` (`Provider<GoRouter>`)
  wired via Riverpod. Redirect guard stub always returns `null` (no
  redirect yet), with a `// TODO(Phase 3)` comment showing the intended
  real logic shape. Backing placeholder: `_sessionPlaceholderProvider`,
  type `Provider<AsyncValue<Object?>>`, currently always
  `AsyncValue.data(null)`.
* 12 placeholder screens, one per route, each a bare `Scaffold` (AppBar =
  route name, body = route label + one `AppButton` from P-006's widget
  library that navigates via `context.goNamed(...)` to the next route in a
  closed 12-route test cycle — see cycle order above). The three
  parameterized screens (`BusinessProfileScreen`, `ProductDetailScreen`,
  `ChatThreadScreen`) also display the received `:id` on-screen.
  `DiscoverScreen` now lives in its own `lib/features/discover/` folder
  (see "Resolved" note above); the other 11 live in their existing
  Section-12 feature folders.
* `lib/main.dart` — `SocialCommerceApp` is now a `ConsumerWidget`;
  `MaterialApp` → `MaterialApp.router(routerConfig: ref.watch(appRouterProvider))`.
  `AppTheme` application from P-006 unchanged.
* `test/widget_test.dart` — updated: now asserts `'Route: splash'` instead
  of the old static placeholder text, since `main.dart`'s behavior changed.
* `test/routing/app_router_test.dart` — 6 tests: splash resolves at
  initial location; all 8 non-parameterized named routes resolve; each of
  the 3 parameterized routes resolves and displays its `:id` correctly;
  one full-cycle test taps all 12 debug buttons and confirms it lands back
  on `splash`.
* `pubspec.yaml` — added `go_router: ^15.1.3`, pinned to match this
  project's own stated Flutter 3.27/Dart 3.6 floor rather than the newest
  available version (15.2.0+/16.x require Flutter 3.29/Dart 3.7 — also
  satisfied by this project's validated real-machine Flutter 3.29.3, so
  bumping later is safe but not required now).
* `.gitkeep` removed from `lib/routing/` and from the `presentation/`
  folder of every feature that now has a placeholder screen (`auth`,
  `feed`, `search`, `business_profile`, `products`, `chat`,
  `notifications`, `business_console`); added fresh to
  `lib/features/discover/data/` and `lib/features/discover/domain/`
  (its `presentation/` has the real placeholder screen instead).

### Issues hit during real-machine validation, and the fixes
None on the dependency/analyze/test front — `go_router: ^15.1.3` resolved
against `flutter_riverpod: 3.3.2` on the first try. The only actual hiccup
was a PowerShell-vs-CMD command issue (`type nul` doesn't work in
PowerShell — `New-Item -ItemType File ... -Force` does), not a code issue.

### What the next parts can assume is available
* Every feature screen from here on must be reached via a named route in
  `RouteNames` + `appRouterProvider` — no feature builds its own
  `Navigator`.
* `lib/features/discover/{data,domain,presentation}/` now exists as a
  first-class feature folder alongside the original Section-12 set — any
  future part touching discovery-related logic belongs there.
* When Phase 3 (Part P-018/P-021) builds the real session provider: the
  **only** file that needs to change is `lib/routing/app_router.dart` —
  replace `_sessionPlaceholderProvider` (currently
  `Provider<AsyncValue<Object?>>`, always `AsyncValue.data(null)`) with the
  real session provider, `ref.watch` it inside `appRouterProvider`, and
  fill in the `redirect` callback body per the inline TODO. Every feature
  screen already navigates through `RouteNames` and needs no changes.
* Part P-022 (token refresh / logout) should call
  `context.goNamed(RouteNames.login)` (or let the eventual redirect do it
  automatically) after `SecureTokenStorage.clear()` — no new route needs
  to be added for that.
* Each of the 12 placeholder screens is meant to be replaced file-by-file
  by its real feature part — the route wiring in `app_router.dart` does
  not need to change when that happens, only the `builder:` callback's
  constructor call if the real screen's constructor differs from the
  placeholder's.

Part P-008 — Riverpod DI Baseline + App Bootstrap

Status: COMPLETE

Actually run on the real machine (Flutter, Windows, D:\Cavallo\social_commerce_app):

Check	Result
flutter pub get	✅ Got dependencies! — no new dependency, no conflict (40 packages have newer versions available, unrelated/pre-existing)
flutter analyze	✅ No issues found! (40.7s)
flutter test	✅ +50: All tests passed! (48 from before P-008 + the 2 new error_reporting_test.dart tests)
flutter run on Android emulator (sdk gphone64 x86 64)	✅ Built, installed, and launched successfully — no exceptions. Only standard emulator-only noise (libEGL called unimplemented OpenGL ES API, a couple of dropped-frame Choreographer/Davey! warnings from the emulator's own renderer, WindowLayoutComponentImpl/ImeTracker framework chatter) — none of it related to this part. Lost connection to device at the end was the user detaching the session, not a crash.
Push to github.com/Ahmed2132003/cavallo-mobile	✅ Commit f9b8a8d on main (3 files changed, 69 insertions(+), 6 deletions(-))
Fresh-clone re-verification	✅ Independently re-cloned main after push: lib/main.dart, lib/core/error_reporting.dart, and test/core/error_reporting_test.dart all present with the exact expected content

No real-machine fixes were needed this time — everything matched on the first try.

P-007's router was already present and wired

Per the execution prompt's instruction to check PROJECT_PROGRESS.md and inspect lib/routing/app_router.dart: P-007 is COMPLETE and already fully wired — main.dart already had SocialCommerceApp as a ConsumerWidget using MaterialApp.router(routerConfig: ref.watch(appRouterProvider)). No placeholder MaterialApp was needed; this part only had to finalize the bootstrap sequence around the existing router wiring, not build or swap in the router itself.

What changed
Created lib/core/error_reporting.dart: void reportError(Object error, StackTrace stack) — the exact stable signature from the spec. Body is a trivial debugPrint, with the required // TODO(Phase 21): replace with Sentry.captureException — signature must not change. comment.
Modified lib/main.dart: main() now runs, in order —
WidgetsFlutterBinding.ensureInitialized()
FlutterError.onError = (details) => reportError(details.exception, details.stack ?? StackTrace.empty);
PlatformDispatcher.instance.onError = (error, stack) { reportError(error, stack); return true; };
runApp(const ProviderScope(child: SocialCommerceApp()));
SocialCommerceApp's body (the MaterialApp.router + appRouterProvider wiring from P-007, AppTheme from P-006) is untouched — only the bootstrap sequence above it changed.
Added test/core/error_reporting_test.dart: 2 unit tests — reportError returns normally for a normal Exception/StackTrace.current pair, and for a non-Exception error object with StackTrace.empty.
test/widget_test.dart needed no changes — it already pumps ProviderScope(child: SocialCommerceApp()) and asserts on 'Route: splash' (from P-007), which already satisfies this part's acceptance criteria ("app boots to the router's initial route" / "a basic widget test that pumps the app and confirms it builds without throwing").
No debug-only throw-button was added to the shipped code — the spec's own Definition of Done says to remove any such temporary trigger before finishing, so it was never added to the committed files in the first place; verifying the reportError funnel is instead covered by the two unit tests above (calling reportError directly, mirroring what a manual debug-button test would confirm) plus the two main.dart assignments compiling and matching the required signatures exactly.
Still open before this part is 100% closed

Nothing — all Definition of Done items are confirmed:

 main.dart matches the bootstrap sequence (ensureInitialized → FlutterError.onError → PlatformDispatcher.instance.onError → runApp)
 reportError has the exact stable signature specified (void reportError(Object     error, StackTrace stack))
 Widget test passes (test/widget_test.dart, unchanged from P-007, still asserts 'Route: splash') — plus the 2 new unit tests for reportError itself
 No debug-only trigger code left in the final commit (f9b8a8d's diff is exactly the 3 files listed above — main.dart, error_reporting.dart, error_reporting_test.dart — no throw-button scaffolding was ever committed)
 flutter analyze clean
 Pushed to github.com/Ahmed2132003/cavallo-mobile and confirmed present on main via a fresh git clone
What the next parts can assume is available
reportError(Object error, StackTrace stack) in lib/core/error_reporting.dart is the only place any future error/crash should be routed through — no part should call print/debugPrint directly for an error, or wire a second FlutterError.onError / PlatformDispatcher.instance.onError assignment (only one of each should ever exist, both already set in main.dart).
Part P-021 (Sentry / monitoring, Phase 21) touches only error_reporting.dart's function body — swap the debugPrint for Sentry.captureException(error, stackTrace: stack) — no changes needed to main.dart or any call site.
main()'s bootstrap order (ensureInitialized → error hooks → runApp) is now final; any future cross-cutting concern that needs to run at app startup (remote config, feature flags, etc.) should be added as an additional step inside this same main() function, not via a second main.dart rewrite.
Content
PROJECT_PROGRESS.md

MD

PROJECT IMPLEMENTATION MASTER PLAN.docx

DOCX

PDF

PART P-008 — Riverpod DI Baseline + App Bootstrap Part Metadata: Phase 1 | Priority: Critical | Complexity: Low | Dependencies: P-001, P-004, P-005 | Parallelizable: No | Backend dependency: No | External input required: No Objective: Wire main.dart into a proper bootstrap sequence: ProviderScope

PASTED

flutter pub get ┌─────────────────────────────────────────────────────────┐ │ A new version of Flutter is available! │ │ │ │ To update to the latest version, run "flutter upgrade". │ └────────────────────────────────────

## Part P-009 — Flutter Core-Layer Tests (Network/Storage Utilities Consolidation)

Status: COMPLETE

Actually run on the real machine (Flutter, Windows, D:\Cavallo\social_commerce_app):

Check	Result
flutter test test/core/integration_test.dart	✅ All 4 new tests passed (network+storage round-trip, no-token-omits-header, theme+router combined, reportError under a real widget-build error)
flutter test (full suite)	✅ +54: All tests passed! — exactly the 50 tests that existed after P-008, plus these 4 new ones; nothing from P-004–P-008 broke
flutter analyze	✅ No issues found! (56.3s)
Push to github.com/Ahmed2132003/cavallo-mobile	✅ Commit 64d3a64 on main (1 file changed, 206 insertions)
Fresh-clone re-verification	✅ Independently re-cloned main after push: test/core/integration_test.dart present at the correct path with content identical to what was authored (only difference is CRLF vs LF line endings, matching every other file in this repo that was edited on the Windows machine — not a real content difference)

No fixes were needed on the real machine — the file authored against the cloned repo (see below) matched real signatures/types on the first try, and no genuine integration bug turned up between P-004 through P-008 either. Per the part's own scope note ("no production code changes expected unless this pass surfaces a real integration bug"), no lib/ files were touched — this part is test-only.

How this was authored (before the real-machine run above)

Exactly like Part P-000 hit with no Docker daemon available, the authoring environment had no Flutter SDK installed and no network access to pub.dev — flutter test/flutter analyze could not be run there. Instead, the actual Ahmed2132003/cavallo-mobile repo (commit f9b8a8d, the tip after P-008) was cloned and inspected directly, and the new test file was written against the exact real signatures/types found there — not against the plan document's description of them. The real-machine run above is what actually validated it, and the fresh-clone check above is what confirmed the push.

What was checked by direct repo inspection (not by running anything)
Piece	Confirmed present, matches plan	Notes
authTokenGetterProvider / dioClientProvider (P-004)	✅	authTokenGetterProvider defaults to () async => null; overridable per-container, exactly as needed to wire a seeded token in without touching dio_client.dart
SecureTokenStorage.getAccessToken (P-005)	✅	Signature (Future<String?> Function()) matches AuthTokenGetter exactly, confirmed by reading both files side by side — no adapter needed, as P-005's own handoff note claimed
AppTheme.theme (P-006)	✅	Fresh ThemeData per call, ColorScheme.fromSeed(seedColor: Colors.indigo) — deterministic, so comparing .colorScheme.primary across two calls is safe
appRouterProvider (P-007)	✅	GoRouter via Riverpod Provider, initial route splash, screens render 'Route: <name>' text — same pattern P-007's own app_router_test.dart already relies on
reportError (P-008)	✅	Stable signature void reportError(Object error, StackTrace stack), trivial debugPrint body — safe to call from a test with a real captured error

No genuine integration bug was found between any of these pieces during this inspection — P-004 through P-008 look consistent with each other as written. Per the part's own instructions, since nothing was found to fix, no production code was touched — only the new test file was added.

What now exists
test/core/integration_test.dart — three groups, matching the three checks the execution prompt named exactly:
Network + storage round-trip. Seeds a real SecureTokenStorage (backed by FlutterSecureStorage.setMockInitialValues({}), same mechanism P-005's own suite uses) with a fake access token, overrides authTokenGetterProvider with tokenStorage.getAccessToken in a throwaway ProviderContainer, reads dioClientProvider from it, swaps in a DioAdapter (P-004's own http_mock_adapter dependency — no real network touched), and asserts the outgoing request's Authorization header is exactly Bearer seeded-access-token-p009. A second test in the same group confirms an unseeded storage correctly omits the header entirely, rather than sending a literal "Bearer null" — a real failure mode worth guarding against that the spec's single round-trip check wouldn't have caught on its own.
Theme + router. Pumps a real MaterialApp.router(theme: AppTheme.theme, routerConfig: ref.read(appRouterProvider)) tree, confirms it resolves to the splash route with zero thrown exceptions (tester.takeException() is null), and — going one step further than "doesn't crash" — reads Theme.of(context).colorScheme.primary off the actually-rendered screen and confirms it matches AppTheme.theme.colorScheme.primary, proving the theme genuinely reached the router's screens rather than the tree silently falling back to Flutter's own default ThemeData because of some wiring conflict.
Error reporting under a real widget-test error. Temporarily takes over FlutterError.onError (the same hook main.dart wires to reportError in P-008), pumps a widget whose build() genuinely throws, captures the real FlutterErrorDetails.exception/.stack the framework produces, and asserts reportError(capturedError, capturedStack) returnsNormally — a real thrown error, not a synthetic Exception('boom') constructed by hand the way P-008's own unit tests do it.
What now exists (confirmed, real-machine run + fresh-clone re-verification)
test/core/integration_test.dart — three groups (four tests total), described in detail above under "What now exists" — unchanged from what was authored against the cloned repo; the real-machine run confirmed it needed zero edits, and the fresh clone confirmed it's live on main.
No lib/ files changed — this part added tests only.
Still open before this part is 100% closed

Nothing — every Definition of Done item is confirmed:

 All three integration checks pass (four tests total — see table above)
 Full existing test suite (P-004 through P-009) passes together, not just individually — +54: All tests passed!
 No integration bug found between P-004–P-008 (none needed fixing/documenting)
 flutter analyze clean
 Pushed to github.com/Ahmed2132003/cavallo-mobile and confirmed present on main via a fresh git clone

Phase 1 (P-004 through P-009) is genuinely complete.

Note: an unrelated backend commit landed alongside this

The same session that pushed this part also pushed a separate commit (01839fd, "update", 1 file / 58 insertions) to github.com/Ahmed2132003/cavallo-app (the backend repo). That commit is outside this part's scope (P-009 is Flutter-only, Phase 1) and wasn't authored or reviewed as part of this part — if it's meant to be tracked here, send over what it contains and it'll get its own entry under the backend/Phase 2 section rather than folded into this one.

What Phase 3 (auth, both sides) can assume once this part is actually closed
Phase 1 (Flutter core: network, storage, theme/widgets, routing, bootstrap) and Phase 2 (backend core, independent — see P-000) are the only two prerequisites Phase 3 needs, per the master plan's phase dependency graph. Phase 3 is the first part that should replace P-004's authTokenGetterProvider placeholder and P-007's _sessionPlaceholderProvider with real values — both integration points are already proven to accept an override cleanly by this part's own first test group, so Phase 3 has a working example to copy the wiring pattern from directly.
No new pattern was introduced by this part that Phase 3 needs to learn — it reuses exactly the ProviderContainer(overrides: [...]) pattern every prior part's own tests already established.
Content
PROJECT IMPLEMENTATION MASTER PLAN.docx

DOCX

PDF

PROJECT_PROGRESS.md

MD

PART P-009 — Flutter Core-Layer Tests (Network/Storage Utilities Consolidation) Part Metadata: Phase 1 | Priority: Medium | Complexity: Low | Dependencies: P-004, P-005, P-006, P-007, P-008 | Parallelizable: No | Backend dependency: No | External input required: No Objective: A consolidation pass

PASTED

flutter test 00:02 +0: D:/Cavallo/social_commerce_app/test/core/error_reporting_test.dart: reportError does not throw for a normal error/stack pair [reportError] Exception: boom #0 main.<anonymous closure>.<anonymous closure> (file:///D:/Cavallo/social_commerce_app/test/core/error_reporting

## Part P-010 — Django Settings Split (base/dev/staging/prod)

**Status: COMPLETE**

Authored and first checked in an environment with no Docker daemon (same constraint as P-000), against a local virtualenv running Django's own tooling directly. Ahmed then took the files to the real Windows machine (`D:\Cavallo\scd-backend`) and validated the actual Docker Compose stack end-to-end — no fixes were needed on the real machine.

### Validation results (real, on Ahmed's machine)

| Check                                                                          | Result                                                                                                                    |
| --------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `docker compose down` then `docker compose up -d --build`                        | ✅ All 3 images rebuilt (`scd-backend-web`, `-celery_worker`, `-celery_beat`), all 5 containers came up                        |
| `docker compose ps`                                                               | ✅ `db` and `redis` **healthy**; `web`, `celery_worker`, `celery_beat` all `Up`, stable (no restart loop)                      |
| `docker compose exec web python manage.py check` (no `--settings` flag)          | ✅ `System check identified no issues (0 silenced)` — confirms `config.settings.dev` is picked up as the default inside the container |
| `docker compose exec web python manage.py migrate`                               | ✅ `No migrations to apply` — the existing DB connection/volume from before P-010 still works correctly against the split settings, nothing needed re-running |
| Dev server boots, `GET /admin/login/` in a real browser at `http://localhost:8090/admin/login/` | ✅ Django admin login page rendered normally                                                                    |

### Validation results (authoring environment, no Docker daemon — kept for reference)

| Check                                                                                          | Result                                                                                                  |
| ------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------- |
| `manage.py check --settings=config.settings.dev`                                                 | ✅ 0 issues                                                                                              |
| `manage.py check --settings=config.settings.staging` (with `ALLOWED_HOSTS` set)                  | ✅ 0 issues                                                                                              |
| `manage.py check --settings=config.settings.staging` (with `ALLOWED_HOSTS` **unset**)             | ✅ Fails loudly with `ImproperlyConfigured: Set the ALLOWED_HOSTS environment variable` — confirmed staging has no unsafe default |
| `manage.py check --deploy --settings=config.settings.prod` (with `ALLOWED_HOSTS` set)             | ✅ Only 2 expected warnings: `security.W004` (HSTS not configured — out of this part's scope on purpose) and `security.W009` (the dummy `SECRET_KEY` used for this local check looks auto-generated — not a real issue, a production deploy would use a real long secret) |
| `manage.py check` with no `--settings` flag at all                                               | ✅ 0 issues — confirms the new default (`config.settings.dev`) resolves correctly                       |
| `black --check` / `flake8` on all new/edited files                                               | ✅ Clean, no changes needed, no violations                                                               |
| `docker-compose.yml` parsed as YAML and confirmed `DJANGO_SETTINGS_MODULE: config.settings.dev` is present under `web`/`celery_worker`/`celery_beat`'s `environment:` | ✅                                                                        |

**Note:** staging/prod settings (`ALLOWED_HOSTS` required, prod's security headers) were only exercised in the authoring environment, not on the real machine — there's no staging/prod deployment target yet at this phase of the project. Nothing about that is a gap in this part; it just means the "required, no default" behavior for those two modules is proven correct but not yet exercised against a real staging/prod deploy (that happens naturally whenever Phase 21/P-220+ deployment work starts).

### What now exists

* `config/settings/__init__.py` — empty package marker (deliberately does not re-export anything; `DJANGO_SETTINGS_MODULE` must always name a concrete module like `config.settings.dev`, never bare `config.settings`).
* `config/settings/base.py` — everything environment-agnostic from the old single `config/settings.py`: `SECRET_KEY`, `INSTALLED_APPS` (framework apps only, unchanged), `MIDDLEWARE`, `TEMPLATES`, `ROOT_URLCONF`, `WSGI_APPLICATION`/`ASGI_APPLICATION`, `DATABASES` (from `DATABASE_URL`), `REDIS_URL` + `CACHES` + `CHANNEL_LAYERS`, all `CELERY_*` settings, `REST_FRAMEWORK`, the env-gated `SENTRY_DSN`/`sentry_sdk.init()` block (kept here rather than duplicated in staging.py/prod.py, since it's already a no-op unless `SENTRY_DSN` is set), `AUTH_PASSWORD_VALIDATORS`, i18n settings, `STATIC_URL`/`STATIC_ROOT`, `DEFAULT_AUTO_FIELD`. `BASE_DIR` was updated to `Path(__file__).resolve().parent.parent.parent` to account for the extra `settings/` nesting level. `DEBUG` and `ALLOWED_HOSTS` were deliberately **not** put here — they're the whole point of the per-environment files.
* `config/settings/dev.py` — `from .base import *`, `DEBUG = True`, `ALLOWED_HOSTS` defaults to `["localhost", "127.0.0.1"]` if unset, `CORS_ALLOW_ALL_ORIGINS = True`, `EMAIL_BACKEND` set to the console backend.
* `config/settings/staging.py` — `from .base import *`, `DEBUG = False`, `ALLOWED_HOSTS`/`CORS_ALLOWED_ORIGINS` read from env **with no default** (confirmed above: missing `ALLOWED_HOSTS` fails loudly instead of silently allowing any host).
* `config/settings/prod.py` — same env-required posture as staging, plus `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` all `True` (exactly the three settings this part's scope named — HSTS and other hardening deliberately left for a later dedicated part, per the `W004` warning above).
* Old single `config/settings.py` — deleted.
* `manage.py` — default `DJANGO_SETTINGS_MODULE` changed from `"config.settings"` to `"config.settings.dev"`.
* `config/wsgi.py`, `config/asgi.py`, `config/celery.py` — **beyond this part's literal file list, but necessary for correctness**: their hardcoded `os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")` fallbacks were updated to `"config.settings.dev"` too. Left unchanged, they'd have pointed at the now-empty `config/settings/__init__.py` (a package, not a real settings module) whenever `DJANGO_SETTINGS_MODULE` isn't already set in the process environment — e.g. running `celery -A config worker` locally outside Docker Compose without exporting the var first.
* `.env.example` — added `DJANGO_SETTINGS_MODULE=config.settings.dev` under a new "Settings module (Part P-010)" section.
* `docker-compose.yml` — `web`, `celery_worker`, `celery_beat` each got an explicit `environment: DJANGO_SETTINGS_MODULE: config.settings.dev` (in addition to their existing `env_file: .env`), so anyone with a pre-P-010 local `.env` (which won't automatically pick up the new `.env.example` line) still gets the correct settings module without regenerating their `.env` file.

### Still open before this part is 100% closed

Nothing — every Definition of Done item is confirmed, on the real machine:

* Four settings files exist with correct inheritance
* `check` passes against all three environment configs (dev on the real machine; staging/prod in the authoring environment, per the note above)
* Dev Docker Compose stack still fully functional — rebuilt, all 5 containers healthy/up, `migrate` clean, admin login page renders
* No app-specific (non-framework) settings present yet

Part P-010 is genuinely complete.

## What the next backend part can assume is available

* `DJANGO_SETTINGS_MODULE` is always one of `config.settings.dev` / `config.settings.staging` / `config.settings.prod` — never bare `config.settings`.
* Any new setting that doesn't vary by environment goes in `config/settings/base.py` (via `env(...)` + a new `.env.example` entry, same convention as before); anything that does vary by environment goes in the matching `dev.py`/`staging.py`/`prod.py`.
* Local dev continues to work exactly as before (`docker compose up`, `manage.py` commands) with zero extra steps — the new `DJANGO_SETTINGS_MODULE` var is already wired into both `.env.example` and `docker-compose.yml` directly, so a fresh `.env` copied from `.env.example` or an existing pre-P-010 `.env` both work.
* Staging and prod are intentionally strict: forgetting to set `ALLOWED_HOSTS` (or `CORS_ALLOWED_ORIGINS`, which just defaults to empty rather than failing) on either will make the app refuse to boot with a clear `ImproperlyConfigured` error, not a silently-insecure default.

---