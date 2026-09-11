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