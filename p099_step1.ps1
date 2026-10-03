# =====================================================================
# p099_step1.ps1  --  PART P-099, STEP 1 of 3
# Scope: threats 1-3 of Section 28 (IDOR, Broken auth, Spam/abuse)
#
# What this script does:
#   - CREATES  SECTION_28_THREAT_MODEL_VERIFICATION.md   (new file)
#   - CREATES  p099_step1_evidence.txt                   (raw evidence, throwaway)
#   - MODIFIES nothing that already exists. No application code is touched.
#
# Run from: D:\Cavallo\scd-backend   (PowerShell, Docker stack already up)
#   powershell -ExecutionPolicy Bypass -File .\p099_step1.ps1
#
# Safe to re-run only with -Force (it refuses to overwrite the report
# otherwise, and writes a .bak first when -Force is used).
# =====================================================================
param([switch]$Force)

$ErrorActionPreference = "Continue"
$BackendDir = "D:\Cavallo\scd-backend"
$MobileDir  = "D:\Cavallo\social_commerce_app"
$BaseUrl    = "http://localhost:8095"
$ReportFile = "SECTION_28_THREAT_MODEL_VERIFICATION.md"
$EvidenceFile = "p099_step1_evidence.txt"

Set-Location $BackendDir

function Write-Section([string]$t) { Write-Host ""; Write-Host "=== $t ===" -ForegroundColor Cyan }
function Add-Evidence([string]$title, [string]$text) {
    $sep = "`r`n" + ("=" * 70) + "`r`n" + $title + "`r`n" + ("=" * 70) + "`r`n"
    [System.IO.File]::AppendAllText((Join-Path $BackendDir $EvidenceFile), $sep + $text + "`r`n")
}
function Get-Kv([string]$text, [string]$key) {
    $m = [regex]::Match($text, "P99\|" + [regex]::Escape($key) + "=([^\r\n]*)")
    if ($m.Success) { return $m.Groups[1].Value.Trim() } else { return $null }
}
function Invoke-PostJson([string]$Url, [string]$JsonBody, [string]$Token) {
    $tmp = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($tmp, $JsonBody, (New-Object System.Text.UTF8Encoding($false)))
    $curlArgs = @('-s', '-m', '20', '-X', 'POST', '-H', 'Content-Type: application/json', '-d', "@$tmp", '-w', "`n%{http_code}")
    if ($Token) { $curlArgs += @('-H', "Authorization: Bearer $Token") }
    $curlArgs += $Url
    $raw = (& curl.exe @curlArgs 2>&1 | Out-String).TrimEnd()
    Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    $nl = $raw.LastIndexOf("`n")
    $status = 0
    $body = ""
    if ($nl -ge 0) {
        [int]::TryParse($raw.Substring($nl + 1).Trim(), [ref]$status) | Out-Null
        $body = $raw.Substring(0, $nl)
    }
    return [pscustomobject]@{ Status = $status; Body = $body }
}
function Invoke-DjangoShell([string]$PyCode) {
    return ($PyCode | & docker compose exec -T web python manage.py shell 2>&1 | Out-String)
}

# ---------------------------------------------------------------------
# 0. Preflight
# ---------------------------------------------------------------------
Write-Section "0. Preflight"
if (-not (Test-Path ".\manage.py")) { Write-Host "STOP: manage.py not found. Run from $BackendDir" -ForegroundColor Red; exit 1 }
if ((Test-Path ".\$ReportFile") -and (-not $Force)) {
    Write-Host "STOP: $ReportFile already exists. Re-run with -Force to overwrite (a .bak is written first)." -ForegroundColor Red
    exit 1
}
if (Test-Path ".\$ReportFile") { Copy-Item ".\$ReportFile" ".\$ReportFile.step1.bak" -Force }
if (Test-Path ".\$EvidenceFile") { Remove-Item ".\$EvidenceFile" -Force }

$health = (& curl.exe -s -o NUL -w '%{http_code}' "$BaseUrl/health/" 2>&1 | Out-String).Trim()
Write-Host "Health check $BaseUrl/health/ -> $health"
if ($health -ne "200") {
    Write-Host "STOP: backend is not answering 200 on /health/. Run: docker compose up -d" -ForegroundColor Red
    exit 1
}
$stamp = Get-Date -Format "yyyyMMddHHmmss"
$commit = (& git rev-parse --short HEAD 2>&1 | Out-String).Trim()
$today = Get-Date -Format "yyyy-MM-dd"
Add-Evidence "RUN HEADER" "date=$today stamp=$stamp backend_commit=$commit health=$health"

# ---------------------------------------------------------------------
# ROW 1 - IDOR
# ---------------------------------------------------------------------
Write-Section "Row 1: IDOR (re-run the P-096 sweep)"
$sweepFiles = @(
    "businesses\tests\test_permission_sweep.py", "products\tests\test_permission_sweep.py",
    "moderation\tests\test_permission_sweep.py", "content\tests\test_permission_sweep.py",
    "stories\tests\test_permission_sweep.py", "social\tests\test_permission_sweep.py",
    "reports\tests\test_permission_sweep.py", "ratings\tests\test_permission_sweep.py",
    "feed\tests\test_permission_sweep.py", "search\tests\test_permission_sweep.py",
    "chat\test_permission_sweep.py", "notifications\tests\test_permission_sweep.py",
    "devices\tests\test_permission_sweep.py", "analytics\tests\test_permission_sweep.py",
    "monetization\tests\test_permission_sweep.py", "payments\tests\test_permission_sweep.py"
)
$missing = @($sweepFiles | Where-Object { -not (Test-Path ".\$_") })
$testDefs = 0
foreach ($f in $sweepFiles) {
    if (Test-Path ".\$f") { $testDefs += @(Select-String -Path ".\$f" -Pattern '^\s*def test_').Count }
}
$pinned = @(Select-String -Path ($sweepFiles | Where-Object { Test-Path ".\$_" }) -Pattern 'def test_finding_s[123]_')
Write-Host "Sweep files present: $($sweepFiles.Count - $missing.Count)/16 ; def test_ count: $testDefs ; pinned S-findings: $($pinned.Count)/3"

$cmd1 = "docker compose exec -T web pytest -k permission_sweep -q -p no:cacheprovider"
Write-Host "Running: $cmd1  (a few minutes)"
$out1 = & docker compose exec -T web pytest -k permission_sweep -q -p no:cacheprovider 2>&1 | Out-String
$code1 = $LASTEXITCODE
Add-Evidence "ROW 1 - $cmd1 (exit $code1)" $out1
$sum1 = ([regex]::Matches($out1, '(?m)^.*\b(passed|failed|error)\b.*\bin [\d\.]+s.*$') | Select-Object -Last 1).Value.Trim()
if (-not $sum1) { $sum1 = "(no pytest summary line found - see evidence file)" }
$passed1 = 0
$pm = [regex]::Match($sum1, '(\d+) passed')
if ($pm.Success) { $passed1 = [int]$pm.Groups[1].Value }
$row1Verdict = "FAIL"
if ($code1 -eq 0 -and $missing.Count -eq 0 -and $pinned.Count -eq 3) { $row1Verdict = "PASS" }
$row1Note = ""
if ($row1Verdict -eq "PASS" -and $passed1 -ne 266) { $row1Note = " (passed count is $passed1, P-096 recorded 266 for -k permission_sweep: CHECK whether tests were added/removed)" }
Write-Host "Row 1 -> $row1Verdict : $sum1$row1Note"

# ---------------------------------------------------------------------
# ROW 2 - Broken auth (JWT theft)
# ---------------------------------------------------------------------
Write-Section "Row 2: Broken auth (SIMPLE_JWT + secure storage)"
$py2 = @'
from django.conf import settings as s
j = s.SIMPLE_JWT
rf = s.REST_FRAMEWORK
print("P99|settings_module=" + str(s.SETTINGS_MODULE))
print("P99|rotate=" + str(j.get("ROTATE_REFRESH_TOKENS")))
print("P99|blacklist_after_rotation=" + str(j.get("BLACKLIST_AFTER_ROTATION")))
print("P99|access_minutes=" + str(int(j["ACCESS_TOKEN_LIFETIME"].total_seconds() // 60)))
print("P99|refresh_days=" + str(int(j["REFRESH_TOKEN_LIFETIME"].total_seconds() // 86400)))
print("P99|algorithm=" + str(j.get("ALGORITHM", "HS256 (simplejwt default)")))
print("P99|blacklist_app_installed=" + str("rest_framework_simplejwt.token_blacklist" in s.INSTALLED_APPS))
print("P99|auth_classes=" + str(rf.get("DEFAULT_AUTHENTICATION_CLASSES")))
print("P99|default_permission=" + str(rf.get("DEFAULT_PERMISSION_CLASSES")))
print("P99|secret_key_len=" + str(len(s.SECRET_KEY)))
print("P99|secret_key_insecure_prefix=" + str(s.SECRET_KEY.startswith("django-insecure")))
print("P99|debug=" + str(s.DEBUG))
print("P99|login_rate=" + str(rf.get("DEFAULT_THROTTLE_RATES", {}).get("login")))
print("P99|default_throttle_classes=" + str(rf.get("DEFAULT_THROTTLE_CLASSES")))
print("P99|report_rate_setting=" + str(getattr(s, "REPORT_THROTTLE_RATE", "(unset -> code default 10/hour)")))
'@
$out2a = Invoke-DjangoShell $py2
Add-Evidence "ROW 2a - effective settings inside the running web container" $out2a

$overrides = @()
foreach ($f in @("config\settings\dev.py", "config\settings\staging.py", "config\settings\prod.py", "config\settings\test.py")) {
    if (Test-Path ".\$f") {
        $hits = @(Select-String -Path ".\$f" -Pattern 'SIMPLE_JWT|ACCESS_TOKEN_LIFETIME|REFRESH_TOKEN_LIFETIME|ROTATE_REFRESH')
        $overrides += "$f : $($hits.Count) override line(s)"
        foreach ($h in $hits) { $overrides += "    $($h.LineNumber): $($h.Line.Trim())" }
    }
}
Add-Evidence "ROW 2b - SIMPLE_JWT overrides in per-environment settings files" ($overrides -join "`r`n")

$rotate = Get-Kv $out2a "rotate"; $bl = Get-Kv $out2a "blacklist_after_rotation"
$accMin = Get-Kv $out2a "access_minutes"; $refDays = Get-Kv $out2a "refresh_days"
$blApp = Get-Kv $out2a "blacklist_app_installed"; $insec = Get-Kv $out2a "secret_key_insecure_prefix"
$skLen = Get-Kv $out2a "secret_key_len"
$settingsOk = ($rotate -eq "True") -and ($bl -eq "True") -and ($blApp -eq "True") -and ($accMin -and [int]$accMin -le 30) -and ($refDays -and [int]$refDays -ge 7 -and [int]$refDays -le 30)
Write-Host "rotate=$rotate blacklist=$bl access_min=$accMin refresh_days=$refDays blacklist_app=$blApp insecure_secret_prefix=$insec"

$cmd2 = "docker compose exec -T web pytest accounts/tests/test_auth.py -q -p no:cacheprovider"
Write-Host "Running: $cmd2"
$out2b = & docker compose exec -T web pytest accounts/tests/test_auth.py -q -p no:cacheprovider 2>&1 | Out-String
$code2 = $LASTEXITCODE
Add-Evidence "ROW 2c - $cmd2 (exit $code2)" $out2b
$sum2 = ([regex]::Matches($out2b, '(?m)^.*\b(passed|failed|error)\b.*\bin [\d\.]+s.*$') | Select-Object -Last 1).Value.Trim()
if (-not $sum2) { $sum2 = "(no pytest summary line found - see evidence file)" }

# Mobile secure storage static check (P-005)
$mobileState = "SKIPPED (folder $MobileDir not found)"
$mobileOk = $null
$mobileLines = @()
if (Test-Path "$MobileDir\lib") {
    $libFiles = Get-ChildItem "$MobileDir\lib" -Recurse -Filter *.dart
    $spImports = @($libFiles | Select-String -Pattern "^\s*import\s+'package:shared_preferences" | ForEach-Object { $_.Path.Replace("$MobileDir\", "") + ":" + $_.LineNumber })
    $fssImports = @($libFiles | Select-String -Pattern "^\s*import\s+'package:flutter_secure_storage" | ForEach-Object { $_.Path.Replace("$MobileDir\", "") + ":" + $_.LineNumber })
    $fssCtors = @($libFiles | Select-String -Pattern "FlutterSecureStorage\(" | Where-Object { $_.Line -notmatch '^\s*///' } | ForEach-Object { $_.Path.Replace("$MobileDir\", "") + ":" + $_.LineNumber })
    $mobileLines += "shared_preferences imports (expected: only lib\core\storage\cache_storage.dart):"
    $mobileLines += ($spImports | ForEach-Object { "    $_" })
    $mobileLines += "flutter_secure_storage imports (expected: only lib\core\storage\secure_token_storage.dart):"
    $mobileLines += ($fssImports | ForEach-Object { "    $_" })
    $mobileLines += "FlutterSecureStorage( constructions (non-comment):"
    $mobileLines += ($fssCtors | ForEach-Object { "    $_" })
    $spOk = ($spImports.Count -ge 1) -and (@($spImports | Where-Object { $_ -notmatch 'cache_storage\.dart' }).Count -eq 0)
    $fssOk = ($fssImports.Count -ge 1) -and (@($fssImports | Where-Object { $_ -notmatch 'secure_token_storage\.dart' }).Count -eq 0)
    $mobileOk = $spOk -and $fssOk
    if ($mobileOk) { $mobileState = "PASS" } else { $mobileState = "CHECK (an unexpected importer exists - see evidence)" }
} else {
    $mobileLines += $mobileState
}
Add-Evidence "ROW 2d - mobile token-storage static check" ($mobileLines -join "`r`n")
Write-Host "Mobile storage static check: $mobileState"

$row2Verdict = "FAIL"
if ($settingsOk -and $code2 -eq 0 -and $mobileOk) { $row2Verdict = "PASS" }
elseif ($settingsOk -and $code2 -eq 0 -and $null -eq $mobileOk) { $row2Verdict = "PARTIAL (mobile check skipped)" }
Write-Host "Row 2 -> $row2Verdict"

# ---------------------------------------------------------------------
# ROW 3 - Spam / abuse (live throttles)
# ---------------------------------------------------------------------
Write-Section "Row 3: Spam/abuse (LIVE login + report throttles)"
$email = "p099-throttle-$stamp@example.com"

$pyMake = @'
from django.contrib.auth import get_user_model
from accounts import services
U = get_user_model()
e = "__EMAIL__"
u = U.objects.filter(email__iexact=e).first() or U(username=e, email=e, account_type="customer")
u.set_password("P99Throwaway!2026pass")
u.save()
print("P99|token=" + services.issue_token_pair(u)["access"])
'@
$pyMake = $pyMake.Replace("__EMAIL__", $email)
$outMake = Invoke-DjangoShell $pyMake
$token = Get-Kv $outMake "token"
Add-Evidence "ROW 3a - throwaway customer created for the live test ($email); token redacted" ($outMake -replace 'P99\|token=\S+', 'P99|token=<redacted>')
if (-not $token) { Write-Host "WARNING: could not mint a throwaway token; report-throttle live test will be skipped. See evidence." -ForegroundColor Yellow }

# 3b. Login throttle (scope "login", 5/min, per IP)
$loginCodes = @()
$loginLast = ""
for ($i = 1; $i -le 8; $i++) {
    $r = Invoke-PostJson "$BaseUrl/api/v1/auth/login/" '{"email":"p99-nobody@example.com","password":"wrong-password-123"}' $null
    $loginCodes += $r.Status
    $loginLast = $r.Body
}
$loginCodeText = ($loginCodes -join ", ")
$first429 = [array]::IndexOf($loginCodes, 429)
$before = @()
if ($first429 -gt 0) { $before = $loginCodes[0..($first429 - 1)] }
$loginOk = ($first429 -ge 1) -and ($first429 -le 5) -and (@($before | Where-Object { $_ -ne 401 -and $_ -ne 400 }).Count -eq 0)
$loginThrottledCode = ""
if ($loginLast -match '"code"\s*:\s*"([A-Z_]+)"') { $loginThrottledCode = $Matches[1] }
Add-Evidence "ROW 3b - 8 bad logins in a row to $BaseUrl/api/v1/auth/login/" ("status codes: $loginCodeText`r`nlast body: $loginLast")
Write-Host "Login attempts -> $loginCodeText ; envelope code on last: $loginThrottledCode"

# 3c. Report throttle (scope "report", 10/hour, per user). Invalid bodies still consume quota (P-057 test).
$reportCodes = @()
$reportLast = ""
if ($token) {
    for ($i = 1; $i -le 11; $i++) {
        $r = Invoke-PostJson "$BaseUrl/api/v1/reports/" '{"content_type":"post","object_id":1,"reason":"bogus","details":""}' $token
        $reportCodes += $r.Status
        $reportLast = $r.Body
    }
}
$reportCodeText = ($reportCodes -join ", ")
$reportFirst429 = [array]::IndexOf($reportCodes, 429)
$reportBefore = @()
if ($reportFirst429 -gt 0) { $reportBefore = $reportCodes[0..($reportFirst429 - 1)] }
$reportThrottledCode = ""
if ($reportLast -match '"code"\s*:\s*"([A-Z_]+)"') { $reportThrottledCode = $Matches[1] }
$reportOk = ($reportCodes.Count -eq 11) -and ($reportFirst429 -eq 10) -and (@($reportBefore | Where-Object { $_ -ne 400 }).Count -eq 0) -and ($reportThrottledCode -eq "THROTTLED")
Add-Evidence "ROW 3c - 11 report POSTs (bogus reason) as one throwaway user to $BaseUrl/api/v1/reports/" ("status codes: $reportCodeText`r`nlast body: $reportLast")
Write-Host "Report attempts -> $reportCodeText ; envelope code on last: $reportThrottledCode"

# 3d. Cleanup of the throwaway user
$pyClean = @'
from django.contrib.auth import get_user_model
U = get_user_model()
res = U.objects.filter(email__iexact="__EMAIL__").delete()
print("P99|cleanup=" + str(res))
'@
$pyClean = $pyClean.Replace("__EMAIL__", $email)
$outClean = Invoke-DjangoShell $pyClean
Add-Evidence "ROW 3d - cleanup of throwaway user" $outClean
Write-Host ("Cleanup -> " + (Get-Kv $outClean "cleanup"))

# 3e. Static inventory: where is throttling actually attached? (message flood question)
$pyFiles = Get-ChildItem . -Recurse -Filter *.py -File | Where-Object { $_.FullName -notmatch '\\(tests|migrations|\.pytest_cache|__pycache__)\\' -and $_.Name -notmatch '^test_' -and $_.Name -ne 'conftest.py' }
$thrHits = @($pyFiles | Select-String -Pattern 'throttle_classes\s*=' | ForEach-Object { $_.Path.Replace("$BackendDir\", "") + ":" + $_.LineNumber + ": " + $_.Line.Trim() })
$chatFiles = @($pyFiles | Where-Object { $_.FullName -match '\\chat\\' })
$chatHits = @($chatFiles | Select-String -Pattern 'throttl|rate_limit|ratelimit|flood|cooldown|per_minute|too many' -CaseSensitive:$false | ForEach-Object { $_.Path.Replace("$BackendDir\", "") + ":" + $_.LineNumber + ": " + $_.Line.Trim() })
Add-Evidence "ROW 3e - every throttle_classes assignment in non-test code" ($thrHits -join "`r`n")
Add-Evidence "ROW 3e - chat flood-control keyword hits in non-test chat code" ($(if ($chatHits.Count -eq 0) { "(none)" } else { $chatHits -join "`r`n" }))
$chatGap = ($chatHits.Count -eq 0)
Write-Host "throttle_classes assignments found: $($thrHits.Count) ; chat flood-control hits: $($chatHits.Count)"

$row3Verdict = "FAIL"
if ($loginOk -and $reportOk -and (-not $chatGap)) { $row3Verdict = "PASS" }
elseif ($loginOk -and $reportOk -and $chatGap) { $row3Verdict = "PARTIAL - login + report PASS, message flood GAP (finding F-99-1)" }

# ---------------------------------------------------------------------
# Build the report file
# ---------------------------------------------------------------------
Write-Section "Writing $ReportFile"

$thrList = ($thrHits | ForEach-Object { "- ``$_``" }) -join "`n"
if (-not $thrList) { $thrList = "- (none found)" }
$mobileList = ($mobileLines | ForEach-Object { "    $_" }) -join "`n"
$overrideList = ($overrides | ForEach-Object { "    $_" }) -join "`n"

$tpl = @'
# SECTION 28 THREAT-MODEL VERIFICATION (Part P-099)

Status: IN PROGRESS - Step 1 of 3 done (rows 1-3). Rows 4-7 are filled by Steps 2 and 3.
Run date: @@DATE@@ | Backend commit at run time: @@COMMIT@@ | Environment: local Docker stack, `config.settings.dev` inside the `web` container.

## How this was checked (read first)
- Every row below was checked against the CURRENT code and the RUNNING stack, not against the master plan's description of what was built.
- Layout note: the P-099 execution prompt writes paths as `apps/...`. This repository has no `apps/` folder; each Django app sits at the repo root (`accounts/`, `chat/`, `content/`, ...). Every grep in this document uses the real layout.
- Raw command output for rows 1-3 is in `p099_step1_evidence.txt` (throwaway, like the earlier `p095_step*_audit.txt` files).

## Summary table
| # | Threat | Verdict | Where |
|---|--------|---------|-------|
| 1 | IDOR | @@ROW1@@ | Row 1 |
| 2 | Broken auth (JWT theft) | @@ROW2@@ | Row 2 |
| 3 | Spam/abuse (report/message flood) | @@ROW3@@ | Row 3 |
| 4 | Moderation bypass | PENDING (Step 2) | Row 4 |
| 5 | Malicious file upload | PENDING (Step 2) | Row 5 |
| 6 | Webhook spoofing | PENDING (Step 3) | Row 6 |
| 7 | Counter race conditions | PENDING (Step 3) | Row 7 |

## Row 1 - IDOR
- What was checked: that the P-096 permission/IDOR sweep (16 apps, 401 / wrong-owner / wrong-role paths, DB re-read to prove "no change") still passes on the current code.
- Method: `docker compose exec -T web pytest -k permission_sweep -q -p no:cacheprovider` (the full sweep, a superset of the 5-10 endpoint sample the prompt asks for).
- Static check: all 16 `test_permission_sweep.py` files present: @@SWEEP_PRESENT@@/16; `def test_` definitions across them: @@TESTDEFS@@; pinned S-finding characterisation tests present: @@PINNED@@/3.
- Result: exit code @@CODE1@@. Pytest summary: `@@SUM1@@`@@ROW1NOTE@@
- Verdict: **@@ROW1@@**
- Findings: S-1, S-2, S-3 from P-096 are still OPEN (see "Open findings" below). They are pinned by passing characterisation tests, so they were not changed by this part.

## Row 2 - Broken auth (JWT theft)
- What was checked: refresh-token rotation, blacklisting, lifetimes and signing setup in the running container; per-environment overrides; the login/refresh/logout tests; and on the Flutter side that tokens go only through `flutter_secure_storage`.
- Method 1 (effective settings, read from the running container via `manage.py shell`):
  - ROTATE_REFRESH_TOKENS = @@ROTATE@@ ; BLACKLIST_AFTER_ROTATION = @@BL@@ ; token_blacklist app installed = @@BLAPP@@
  - Access lifetime = @@ACCMIN@@ min ; Refresh lifetime = @@REFDAYS@@ days (Section 14 range: 7-30 days)
  - SECRET_KEY length = @@SKLEN@@ ; starts with "django-insecure" = @@INSEC@@ (the key itself is never printed)
- Method 2 (override scan, `Select-String` over `config\settings\*.py` for SIMPLE_JWT / lifetime / rotate keys):
@@OVERRIDES@@
- Method 3: `docker compose exec -T web pytest accounts/tests/test_auth.py -q -p no:cacheprovider` -> exit @@CODE2@@, `@@SUM2@@`. This file covers rotation (new refresh issued), reuse of a rotated-away token rejected, logout blacklisting, and other users' sessions untouched.
- Method 4 (Flutter, P-005; `Select-String` over `@@MOBILEDIR@@\lib` for imports and constructions):
@@MOBILELIST@@
  - Mobile static check: @@MOBILESTATE@@
- Limits of this row: it checks configuration plus tests plus imports. It does not prove on a device that the Keystore/Keychain is used at runtime.
- Verdict: **@@ROW2@@**

## Row 3 - Spam/abuse (report flood, message flood)
- What was checked: that rate limiting is genuinely active (a live 429), not just present in settings, and where else throttling exists.
- Live test A, login throttle (scope `login`, configured @@LOGINRATE@@, per client IP): 8 bad logins in a row to `POST /api/v1/auth/login/` returned: `@@LOGINCODES@@`. Envelope code on the last response: `@@LOGINENV@@`. Result: @@LOGINRESULT@@.
- Live test B, report throttle (scope `report`, code default 10/hour, per user): one throwaway customer (`@@EMAIL@@`, deleted afterwards) sent 11 `POST /api/v1/reports/` requests with an invalid `reason` (invalid requests count toward the quota by design, P-057), returned: `@@REPORTCODES@@`. Envelope code on the last response: `@@REPORTENV@@`. Result: @@REPORTRESULT@@. Note: the first 10 are 400 because the body is deliberately invalid; the point is the 11th is 429 THROTTLED.
- Inventory of every `throttle_classes =` assignment in non-test code:
@@THRLIST@@
- Message-flood check: keyword search (`throttl|rate_limit|ratelimit|flood|cooldown|per_minute|too many`) over non-test files in `chat\` found @@CHATHITS@@ hit(s).
- Verdict: **@@ROW3@@**
- Finding F-99-1 (@@F991STATE@@): Section 28 lists "Report/Message flood". Report flood and login brute force are throttled. @@F991TEXT@@
- Side effect to know about: the live login test used up the 5/min login quota for this machine's address for about one minute; if the app shows a 429 right after this run, wait a minute.

## Open findings (carried from earlier parts + new)
| ID | Source | Description | Status | Suggested priority |
|----|--------|-------------|--------|--------------------|
| S-1 | P-096 (social) | Like/Save accept an unpublished (pending/rejected) post by id and bump `likes_count`; Comment/Share/Report require published. | OPEN | High (also relevant to Row 4) |
| S-2 | P-096 (ratings) | A business owner can rate their own business, inflating `average_rating` (used by Search `min_rating`). Needs a product decision. | OPEN | Medium |
| S-3 | P-096 (chat) | `ConversationStartView` 400/404 bodies use `{"detail": ...}` instead of the P-012 envelope. Statuses correct, nothing leaks. | OPEN | Low |
| F-99-1 | P-099 Row 3 | @@F991TABLE@@ | @@F991STATE@@ | @@F991PRIO@@ |

<!-- ROW4_PLACEHOLDER: Step 2 replaces this line with "## Row 4 - Moderation bypass" -->
<!-- ROW5_PLACEHOLDER: Step 2 replaces this line with "## Row 5 - Malicious file upload" -->
<!-- ROW6_PLACEHOLDER: Step 3 replaces this line with "## Row 6 - Webhook spoofing" -->
<!-- ROW7_PLACEHOLDER: Step 3 replaces this line with "## Row 7 - Counter race conditions" -->
<!-- FINAL_PLACEHOLDER: Step 3 adds the full-suite pytest result and the final sign-off here -->
'@

if ($loginOk) { $loginResult = "PASS (429 reached at attempt $($first429 + 1); all earlier responses were 401/400)" } else { $loginResult = "FAIL or INCONCLUSIVE - see evidence file" }
if ($reportOk) { $reportResult = "PASS (429 THROTTLED on the 11th request)" } elseif (-not $token) { $reportResult = "SKIPPED (no throwaway token)" } else { $reportResult = "FAIL or INCONCLUSIVE - see evidence file" }

if ($chatGap) {
    $f991State = "OPEN"
    $f991Text = "No throttle or flood control exists anywhere in non-test chat code, so an authenticated user can send messages (REST and/or WebSocket) with no rate limit. Follow-up: add a per-user message rate limit on the chat send path (REST view and WebSocket consumer), sized in a decision with the owner; add a test that triggers it."
    $f991Table = "Section 28 names message flood but no chat throttle exists (grep over non-test chat code finds none). Follow-up: per-user rate limit on the chat send path (REST + WebSocket consumer) with a test."
    $f991Prio = "High"
} else {
    $f991State = "CHECK"
    $f991Text = "Some flood-control keywords were found in chat code; review them in the evidence file to confirm they actually limit message sending."
    $f991Table = "Chat flood-control keyword hits found; confirm in p099_step1_evidence.txt that they really limit sending."
    $f991Prio = "To confirm"
}

$map = @{
    "@@DATE@@" = $today; "@@COMMIT@@" = $commit
    "@@ROW1@@" = $row1Verdict; "@@ROW2@@" = $row2Verdict; "@@ROW3@@" = $row3Verdict
    "@@SWEEP_PRESENT@@" = [string]($sweepFiles.Count - $missing.Count); "@@TESTDEFS@@" = [string]$testDefs; "@@PINNED@@" = [string]$pinned.Count
    "@@CODE1@@" = [string]$code1; "@@SUM1@@" = $sum1; "@@ROW1NOTE@@" = $row1Note
    "@@ROTATE@@" = [string]$rotate; "@@BL@@" = [string]$bl; "@@BLAPP@@" = [string]$blApp
    "@@ACCMIN@@" = [string]$accMin; "@@REFDAYS@@" = [string]$refDays; "@@SKLEN@@" = [string]$skLen; "@@INSEC@@" = [string]$insec
    "@@OVERRIDES@@" = $overrideList; "@@CODE2@@" = [string]$code2; "@@SUM2@@" = $sum2
    "@@MOBILEDIR@@" = $MobileDir; "@@MOBILELIST@@" = $mobileList; "@@MOBILESTATE@@" = $mobileState
    "@@LOGINRATE@@" = [string](Get-Kv $out2a "login_rate"); "@@LOGINCODES@@" = $loginCodeText; "@@LOGINENV@@" = $loginThrottledCode; "@@LOGINRESULT@@" = $loginResult
    "@@EMAIL@@" = $email; "@@REPORTCODES@@" = $reportCodeText; "@@REPORTENV@@" = $reportThrottledCode; "@@REPORTRESULT@@" = $reportResult
    "@@THRLIST@@" = $thrList; "@@CHATHITS@@" = [string]$chatHits.Count
    "@@F991STATE@@" = $f991State; "@@F991TEXT@@" = $f991Text; "@@F991TABLE@@" = $f991Table; "@@F991PRIO@@" = $f991Prio
}
$doc = $tpl
foreach ($k in $map.Keys) { $doc = $doc.Replace($k, $map[$k]) }
[System.IO.File]::WriteAllText((Join-Path $BackendDir $ReportFile), $doc.Replace("`r`n", "`n"), (New-Object System.Text.UTF8Encoding($false)))
Write-Host "Created $ReportFile ($((Get-Item $ReportFile).Length) bytes)"

# ---------------------------------------------------------------------
# Console summary to paste back
# ---------------------------------------------------------------------
Write-Section "STEP 1 RESULT - paste everything below this line back to Claude"
Write-Host "backend_commit        : $commit"
Write-Host "health                : $health"
Write-Host "ROW1 IDOR sweep       : $row1Verdict | exit=$code1 | $sum1 | files=$($sweepFiles.Count - $missing.Count)/16 pinned=$($pinned.Count)/3$row1Note"
Write-Host "ROW2 broken auth      : $row2Verdict | rotate=$rotate blacklist=$bl access_min=$accMin refresh_days=$refDays blacklist_app=$blApp insecure_key=$insec"
Write-Host "ROW2 test_auth.py     : exit=$code2 | $sum2"
Write-Host "ROW2 mobile storage   : $mobileState"
Write-Host "ROW3 login throttle   : $loginCodeText | env=$loginThrottledCode | ok=$loginOk"
Write-Host "ROW3 report throttle  : $reportCodeText | env=$reportThrottledCode | ok=$reportOk"
Write-Host "ROW3 verdict          : $row3Verdict"
Write-Host "ROW3 chat flood hits  : $($chatHits.Count) (0 means finding F-99-1 is OPEN)"
Write-Host "throttle_classes found: $($thrHits.Count)"
Write-Host "Files created         : $ReportFile , $EvidenceFile"
Write-Host "(if anything says FAIL/CHECK, also send the matching section of $EvidenceFile)"