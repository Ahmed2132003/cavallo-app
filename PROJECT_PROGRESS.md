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