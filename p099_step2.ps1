# =====================================================================
# p099_step2.ps1  --  PART P-099, STEP 2 of 3
# Scope: threats 4-5 of Section 28 (Moderation bypass, Malicious upload)
#
# What this script does:
#   - CREATES  p099_step2_evidence.txt                       (raw evidence, throwaway)
#   - MODIFIES SECTION_28_THREAT_MODEL_VERIFICATION.md       (surgical: fills Row 4 and
#              Row 5, flips their summary-table rows, updates the Status line, adds
#              findings rows; a .step2.bak copy is written first)
#   - Creates and then DELETES one throwaway business user + 2 posts (live test).
#   - Changes NO application code. Nothing is "fixed" in this step: see the
#     report for why finding F-99-2 is documented, not patched.
#
# Run from: D:\Cavallo\scd-backend   (PowerShell, Docker stack already up)
#   powershell -ExecutionPolicy Bypass -File .\p099_step2.ps1
#
# Refuses to run twice (Row 4 section already present) unless -Force.
# =====================================================================
param([switch]$Force)

$ErrorActionPreference = "Continue"
$BackendDir = "D:\Cavallo\scd-backend"
$BaseUrl    = "http://localhost:8095"
$ReportFile = "SECTION_28_THREAT_MODEL_VERIFICATION.md"
$EvidenceFile = "p099_step2_evidence.txt"

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
function Invoke-DjangoShell([string]$PyCode) {
    return ($PyCode | & docker compose exec -T web python manage.py shell 2>&1 | Out-String)
}
function Invoke-Get([string]$Url) {
    $curlArgs = @('-s', '-m', '20', '-w', "`n%{http_code}", $Url)
    $raw = (& curl.exe @curlArgs 2>&1 | Out-String).TrimEnd()
    $nl = $raw.LastIndexOf("`n")
    $status = 0
    $body = ""
    if ($nl -ge 0) {
        [int]::TryParse($raw.Substring($nl + 1).Trim(), [ref]$status) | Out-Null
        $body = $raw.Substring(0, $nl)
    }
    return [pscustomobject]@{ Status = $status; Body = $body }
}
function Find-Regex([string]$Pattern, $Files) {
    $res = @()
    foreach ($f in $Files) {
        $text = [System.IO.File]::ReadAllText($f.FullName)
        foreach ($m in [regex]::Matches($text, $Pattern)) {
            $line = ($text.Substring(0, $m.Index).Split("`n")).Count
            $first = ($m.Value -split "`r?`n")[0].Trim()
            $res += [pscustomobject]@{ File = $f.FullName.Replace("$BackendDir\", ""); Line = $line; Text = $first }
        }
    }
    return $res
}
function Format-Hits($hits) {
    if (-not $hits -or @($hits).Count -eq 0) { return "(none)" }
    return ((@($hits) | ForEach-Object { "$($_.File):$($_.Line): $($_.Text)" }) -join "`r`n")
}
function Get-Verdict-Line([string]$text) {
    $m = ([regex]::Matches($text, '(?m)^.*\b(passed|failed|error)\b.*\bin [\d\.]+s.*$') | Select-Object -Last 1)
    if ($m) { return $m.Value.Trim() } else { return "(no pytest summary line found - see evidence file)" }
}

# ---------------------------------------------------------------------
# 0. Preflight
# ---------------------------------------------------------------------
Write-Section "0. Preflight"
if (-not (Test-Path ".\manage.py")) { Write-Host "STOP: run from $BackendDir" -ForegroundColor Red; exit 1 }
if (-not (Test-Path ".\$ReportFile")) { Write-Host "STOP: $ReportFile not found. Step 1 must be run first." -ForegroundColor Red; exit 1 }
$doc = [System.IO.File]::ReadAllText((Join-Path $BackendDir $ReportFile))
if (($doc -match '(?m)^## Row 4 - Moderation bypass') -and (-not $Force)) {
    Write-Host "STOP: Row 4 is already in the report (Step 2 already applied). Use -Force to redo it from the .step2.bak." -ForegroundColor Red
    exit 1
}
$anchors = @(
    'Status: IN PROGRESS - Step 1 of 3 done (rows 1-3). Rows 4-7 are filled by Steps 2 and 3.',
    '| 4 | Moderation bypass | PENDING (Step 2) | Row 4 |',
    '| 5 | Malicious file upload | PENDING (Step 2) | Row 5 |'
)
foreach ($a in $anchors) {
    if (-not $doc.Contains($a)) { Write-Host "STOP: anchor text not found in the report (was it edited?): $a" -ForegroundColor Red; exit 1 }
}
if (-not ($doc -match '(?m)^<!-- ROW4_PLACEHOLDER:.*-->$') -or -not ($doc -match '(?m)^<!-- ROW5_PLACEHOLDER:.*-->$') -or -not ($doc -match '(?m)^\| F-99-1 \|.*$')) {
    Write-Host "STOP: ROW4/ROW5 placeholder or the F-99-1 table row is missing from the report." -ForegroundColor Red
    exit 1
}
$health = (& curl.exe -s -o NUL -w '%{http_code}' "$BaseUrl/health/" 2>&1 | Out-String).Trim()
Write-Host "Health check -> $health"
if ($health -ne "200") { Write-Host "STOP: backend not healthy. Run: docker compose up -d" -ForegroundColor Red; exit 1 }
Copy-Item ".\$ReportFile" ".\$ReportFile.step2.bak" -Force
if (Test-Path ".\$EvidenceFile") { Remove-Item ".\$EvidenceFile" -Force }
$stamp = Get-Date -Format "yyyyMMddHHmmss"
$commit = (& git rev-parse --short HEAD 2>&1 | Out-String).Trim()
Add-Evidence "RUN HEADER" "stamp=$stamp backend_commit=$commit health=$health"

$pyFiles = Get-ChildItem . -Recurse -Filter *.py -File | Where-Object { $_.FullName -notmatch '\\(tests|migrations|\.pytest_cache|__pycache__|testapp)\\' -and $_.Name -notmatch '^test_' -and $_.Name -ne 'conftest.py' }

# ---------------------------------------------------------------------
# ROW 4 - Moderation bypass
# ---------------------------------------------------------------------
Write-Section "Row 4: Moderation bypass"

# 4a. raw status filters
$patA = '\.(objects|published_objects)\.(filter|exclude|get)\(\s*[^)]*\bstatus(__in)?\s*='
$hitsA = @(Find-Regex $patA $pyFiles)
$allowRe = '^(content\\models\.py|moderation\\views\.py|reports\\admin\.py|stories\\views\.py|stories\\tasks\.py|reports\\targets\.py)$'
$unexpectedA = @($hitsA | Where-Object { $_.File -notmatch $allowRe })
Add-Evidence "ROW 4a - raw .objects.filter/exclude/get(... status ...) in non-test code" (Format-Hits $hitsA)
Add-Evidence "ROW 4a - hits OUTSIDE the allow-list (content\models.py = the manager itself, moderation\views.py = staff queue, reports\admin.py, stories\* = P-048 query-driven pattern, reports\targets.py)" (Format-Hits $unexpectedA)
Write-Host "4a raw status filters: $(@($hitsA).Count) hit(s), $(@($unexpectedA).Count) outside the allow-list"

# 4b. places that read Post/Reel/any model via the unfiltered default manager
$patB = '\b(Post|Reel)\.objects\b|\bmodel\.objects\b|__class__\.objects\b'
$hitsB = @(Find-Regex $patB $pyFiles | Where-Object { $_.File -match '\\(views|services|serializers|targets)\.py$' })
Add-Evidence "ROW 4b - Post/Reel/model default-manager reads in views/services/serializers/targets" (Format-Hits $hitsB)
Write-Host "4b default-manager reads in view-layer code: $(@($hitsB).Count)"

# 4c. LIVE test: can an anonymous client read a PENDING post by id?
$py4 = @'
from core.tests.sweep_factories import make_business, make_post
from content.models import Reel
b = make_business("P99 Throwaway __STAMP__")
p = make_post(b, published=False, caption="p99-pending-__STAMP__")
q = make_post(b, published=True, caption="p99-published-__STAMP__")
print("P99|owner_email=" + b.user.email)
print("P99|pending_id=" + str(p.pk))
print("P99|pending_status=" + str(p.status))
print("P99|published_id=" + str(q.pk))
print("P99|published_status=" + str(q.status))
r = Reel.objects.exclude(status="published").first()
print("P99|reel_unpublished_id=" + (str(r.pk) if r else "none"))
print("P99|reel_unpublished_status=" + (str(r.status) if r else "none"))
'@
$py4 = $py4.Replace("__STAMP__", $stamp)
$out4 = Invoke-DjangoShell $py4
Add-Evidence "ROW 4c - throwaway data created for the live test" $out4
$pendingId = Get-Kv $out4 "pending_id"; $pendingStatus = Get-Kv $out4 "pending_status"
$publishedId = Get-Kv $out4 "published_id"; $ownerEmail = Get-Kv $out4 "owner_email"
$reelId = Get-Kv $out4 "reel_unpublished_id"; $reelStatus = Get-Kv $out4 "reel_unpublished_status"

$liveState = "INCONCLUSIVE (could not create test data - see evidence)"
$pendCode = 0; $pendBody = ""; $pubCode = 0; $listCode = 0; $listHasPending = $false; $reelCode = 0; $reelBody = ""
$leaksStatus = $false; $leaksReason = $false
if ($pendingId -and $publishedId) {
    $r1 = Invoke-Get "$BaseUrl/api/v1/posts/$pendingId/"
    $pendCode = $r1.Status; $pendBody = $r1.Body
    $r2 = Invoke-Get "$BaseUrl/api/v1/posts/$publishedId/"
    $pubCode = $r2.Status
    $r3 = Invoke-Get "$BaseUrl/api/v1/posts/public/"
    $listCode = $r3.Status
    $listHasPending = ($r3.Body -match "p99-pending-$stamp")
    if ($reelId -and $reelId -ne "none") {
        $r4 = Invoke-Get "$BaseUrl/api/v1/reels/$reelId/"
        $reelCode = $r4.Status; $reelBody = $r4.Body
    }
    $leaksStatus = ($pendBody -match '"status"\s*:\s*"pending"')
    $leaksReason = ($pendBody -match '"rejection_reason"')
    Add-Evidence "ROW 4c - anonymous GET (no Authorization header) results" ("GET /api/v1/posts/$pendingId/ (status=$pendingStatus) -> $pendCode`r`nbody: $pendBody`r`nGET /api/v1/posts/$publishedId/ (published control) -> $pubCode`r`nGET /api/v1/posts/public/ -> $listCode ; pending caption present in first page: $listHasPending`r`nGET /api/v1/reels/$reelId/ (status=$reelStatus) -> $reelCode`r`nbody: $reelBody")
    if ($pendCode -eq 200) { $liveState = "FAIL: anonymous GET of a $pendingStatus post returned 200" }
    elseif ($pendCode -eq 404) { $liveState = "PASS: anonymous GET of a $pendingStatus post returned 404" }
    else { $liveState = "CHECK: anonymous GET of a $pendingStatus post returned $pendCode" }
}
Write-Host "4c live: $liveState ; published control=$pubCode ; public list shows pending=$listHasPending ; leaks status=$leaksStatus rejection_reason key=$leaksReason ; unpublished reel $reelId -> $reelCode"

# 4d. cleanup
$pyClean = @'
from django.contrib.auth import get_user_model
U = get_user_model()
try:
    res = U.objects.filter(email__iexact="__EMAIL__").delete()
    print("P99|cleanup=" + str(res))
except Exception as exc:
    print("P99|cleanup=ERROR " + repr(exc))
'@
if ($ownerEmail) {
    $outClean = Invoke-DjangoShell ($pyClean.Replace("__EMAIL__", $ownerEmail))
    Add-Evidence "ROW 4d - cleanup" $outClean
    Write-Host ("4d cleanup -> " + (Get-Kv $outClean "cleanup"))
}

# 4e. existing tests around the moderation visibility rules
$cmd4 = "docker compose exec -T web pytest content feed stories search -q -p no:cacheprovider"
Write-Host "Running: $cmd4  (a few minutes)"
$out4t = & docker compose exec -T web pytest content feed stories search -q -p no:cacheprovider 2>&1 | Out-String
$code4 = $LASTEXITCODE
$sum4 = Get-Verdict-Line $out4t
Add-Evidence "ROW 4e - $cmd4 (exit $code4)" $out4t
Write-Host "4e tests: exit=$code4 | $sum4"

$row4Verdict = "PASS"
if (@($unexpectedA).Count -gt 0) { $row4Verdict = "CHECK (raw status filter outside the allow-list)" }
if ($pendCode -eq 200) { $row4Verdict = "FAIL (finding F-99-2: unpublished Post readable by anyone via GET /api/v1/posts/<id>/)" }
elseif (-not ($pendCode -eq 404)) { $row4Verdict = "INCONCLUSIVE (live test did not run - see evidence)" }
if ($code4 -ne 0 -and $row4Verdict -eq "PASS") { $row4Verdict = "CHECK (existing tests failed)" }
Write-Host "Row 4 -> $row4Verdict"

# ---------------------------------------------------------------------
# ROW 5 - Malicious file upload
# ---------------------------------------------------------------------
Write-Section "Row 5: Malicious file upload"

# 5a. static: every FileField/ImageField declaration, and any serializer-level file field
$ffDecl = @(Find-Regex '\b(models\.)?(FileField|ImageField)\(' ($pyFiles | Where-Object { $_.Name -eq 'models.py' }))
$serFileFields = @(Find-Regex 'serializers\.(FileField|ImageField)\(' $pyFiles)
Add-Evidence "ROW 5a - FileField/ImageField declarations in models.py files" (Format-Hits $ffDecl)
Add-Evidence "ROW 5a - serializers.FileField/ImageField declared directly in serializers (outside ModelSerializer auto-fields)" (Format-Hits $serFileFields)
Write-Host "5a model file fields: $(@($ffDecl).Count) ; serializer-declared file fields: $(@($serFileFields).Count)"

# 5b. introspection inside the container: for each model file field, is each writable serializer path guarded by validate_upload?
$py5 = @'
import importlib, inspect, re
from django.apps import apps
from django.contrib import admin
from django.db import models
from rest_framework import serializers as drf

READ_SIDE = re.compile(r"Public|List|Read|Summary|Nested")
for model in apps.get_models():
    mod_root = model.__module__.split(".")[0]
    if mod_root in ("django", "rest_framework", "rest_framework_simplejwt", "django_celery_beat", "django_celery_results"):
        continue
    if model.__module__.startswith(("core.tests", "moderation.tests")):
        continue
    ffs = [f for f in model._meta.get_fields() if isinstance(f, models.FileField)]
    if not ffs:
        continue
    label = model._meta.app_label + "." + model.__name__
    try:
        smod = importlib.import_module(model._meta.app_label + ".serializers")
    except ImportError:
        smod = None
    sers = []
    if smod is not None:
        for name, cls in vars(smod).items():
            if inspect.isclass(cls) and issubclass(cls, drf.ModelSerializer) and getattr(getattr(cls, "Meta", None), "model", None) is model:
                sers.append(cls)
    reg = admin.site.is_registered(model)
    ma = admin.site._registry.get(model)
    print("P99|ADMIN|%s|registered=%s|readonly=%s|exclude=%s" % (label, reg, list(getattr(ma, "readonly_fields", []) or []), list(getattr(ma, "exclude", None) or [])))
    for f in ffs:
        if not sers:
            print("P99|FIELD|%s.%s|NO_SERIALIZER|-" % (label, f.name))
            continue
        for s in sers:
            try:
                fld = s().fields.get(f.name)
            except Exception as exc:
                print("P99|FIELD|%s.%s|ERROR %r|%s" % (label, f.name, exc, s.__name__))
                continue
            if fld is None:
                state = "NOT_EXPOSED"
            elif fld.read_only:
                state = "READ_ONLY"
            else:
                meth = getattr(s, "validate_" + f.name, None)
                src = inspect.getsource(meth) if meth else ""
                if "validate_upload" in src:
                    state = "WRITABLE_VALIDATED"
                elif READ_SIDE.search(s.__name__):
                    state = "WRITABLE_NOT_VALIDATED_READ_SIDE_BY_NAME"
                else:
                    state = "WRITABLE_NOT_VALIDATED"
            print("P99|FIELD|%s.%s|%s|%s" % (label, f.name, state, s.__name__))
'@
$out5 = Invoke-DjangoShell $py5
Add-Evidence "ROW 5b - serializer introspection inside the web container" $out5
$fieldLines = @([regex]::Matches($out5, '(?m)^P99\|FIELD\|[^\r\n]*') | ForEach-Object { $_.Value.Trim() })
$adminLines = @([regex]::Matches($out5, '(?m)^P99\|ADMIN\|[^\r\n]*') | ForEach-Object { $_.Value.Trim() })
$bad = @($fieldLines | Where-Object { $_ -match '\|(WRITABLE_NOT_VALIDATED|NO_SERIALIZER|ERROR)' -and $_ -notmatch 'READ_SIDE_BY_NAME' })
$readSide = @($fieldLines | Where-Object { $_ -match 'READ_SIDE_BY_NAME' })
$validated = @($fieldLines | Where-Object { $_ -match '\|WRITABLE_VALIDATED\|' })
$adminOpen = @($adminLines | Where-Object { $_ -match 'registered=True' })
Write-Host "5b introspection: $($fieldLines.Count) field/serializer pair(s); validated-writable=$($validated.Count); read-only/not-exposed=$(@($fieldLines | Where-Object { $_ -match 'READ_ONLY|NOT_EXPOSED' }).Count); unguarded=$($bad.Count); read-side-by-name=$($readSide.Count); admin-registered file models=$($adminOpen.Count)"

# 5c. existing upload-rejection tests
$cmd5a = "docker compose exec -T web pytest core/tests/test_media.py chat/test_media_messages.py -q -p no:cacheprovider"
$out5a = & docker compose exec -T web pytest core/tests/test_media.py chat/test_media_messages.py -q -p no:cacheprovider 2>&1 | Out-String
$code5a = $LASTEXITCODE
$sum5a = Get-Verdict-Line $out5a
Add-Evidence "ROW 5c - $cmd5a (exit $code5a)" $out5a
$cmd5b = 'docker compose exec -T web pytest content/tests/test_api.py stories/tests/test_api.py products/tests/test_api.py -k "spoofed or disguised" -q -p no:cacheprovider'
$out5b = & docker compose exec -T web pytest content/tests/test_api.py stories/tests/test_api.py products/tests/test_api.py -k "spoofed or disguised" -q -p no:cacheprovider 2>&1 | Out-String
$code5b = $LASTEXITCODE
$sum5b = Get-Verdict-Line $out5b
Add-Evidence "ROW 5c - $cmd5b (exit $code5b)" $out5b
Write-Host "5c tests: media+chat exit=$code5a | $sum5a"
Write-Host "5c tests: spoofed/disguised exit=$code5b | $sum5b"

$row5Verdict = "FAIL"
if (($bad.Count -eq 0) -and ($validated.Count -ge 5) -and ($code5a -eq 0) -and ($code5b -eq 0)) { $row5Verdict = "PASS" }
elseif ($bad.Count -gt 0) { $row5Verdict = "FAIL (unguarded writable file field - see report)" }
elseif ($validated.Count -lt 5) { $row5Verdict = "CHECK (fewer validated paths than the 5 expected: Post.image, Reel.video, Story.media, Message.media, Product.image)" }
Write-Host "Row 5 -> $row5Verdict"

# ---------------------------------------------------------------------
# Update the report (surgical)
# ---------------------------------------------------------------------
Write-Section "Updating $ReportFile"

$hitsAList = ((@($hitsA) | ForEach-Object { "    $($_.File):$($_.Line): $($_.Text)" }) -join "`n"); if (-not $hitsAList) { $hitsAList = "    (none)" }
$hitsBList = ((@($hitsB) | ForEach-Object { "    $($_.File):$($_.Line): $($_.Text)" }) -join "`n"); if (-not $hitsBList) { $hitsBList = "    (none)" }
$unexpList = ((@($unexpectedA) | ForEach-Object { "    $($_.File):$($_.Line): $($_.Text)" }) -join "`n"); if (-not $unexpList) { $unexpList = "    (none)" }
$fieldList = (($fieldLines | ForEach-Object { "    $_" }) -join "`n"); if (-not $fieldList) { $fieldList = "    (no output - see evidence)" }
$adminList = (($adminLines | ForEach-Object { "    $_" }) -join "`n"); if (-not $adminList) { $adminList = "    (none)" }
$ffDeclList = ((@($ffDecl) | ForEach-Object { "    $($_.File):$($_.Line): $($_.Text)" }) -join "`n"); if (-not $ffDeclList) { $ffDeclList = "    (none)" }

$row4Tpl = @'
## Row 4 - Moderation bypass
- What was checked: that every public-facing content read goes through the moderation-aware path (`Post.published_objects` / `Reel.published_objects`, or Story's query-driven status+expiry filter), not the unfiltered default manager.
- Method 1 (regex over all non-test `.py` files; pattern: `.objects|published_objects .filter|exclude|get( ... status =`). Layout note: the prompt's `grep -rn "\.objects\.filter(status=" apps/` was adapted to the real root-level layout and widened to also catch multi-line calls:
@@HITSA@@
  - Hits outside the expected allow-list (`content\models.py` is the manager definition itself; `moderation\views.py` is the staff queue; `reports\admin.py`; `stories\views.py`, `stories\tasks.py` and `reports\targets.py` are the P-048 status+expiry pattern):
@@UNEXPECTED@@
- Method 2 (default-manager reads of Post/Reel/any model in view-layer files: `views.py`, `services.py`, `serializers.py`, `targets.py`):
@@HITSB@@
- Method 3 (LIVE, anonymous, no Authorization header). A throwaway business and two posts were created through the project's own `core.tests.sweep_factories` helpers, one pending and one published, then removed afterwards:
  - `GET /api/v1/posts/<pending id>/` (post status = `@@PENDSTATUS@@`) -> **@@PENDCODE@@**; body leaks `"status": "pending"`: @@LEAKSTATUS@@; body contains a `rejection_reason` key: @@LEAKREASON@@
  - `GET /api/v1/posts/<published id>/` (control) -> @@PUBCODE@@
  - `GET /api/v1/posts/public/` -> @@LISTCODE@@; pending caption present on the first page: @@LISTHAS@@ (the public LIST is correctly moderation-aware)
  - `GET /api/v1/reels/<non-published reel id>/` (@@REELNOTE@@) -> @@REELCODE@@
  - Result: @@LIVESTATE@@
- Method 4: `docker compose exec -T web pytest content feed stories search -q -p no:cacheprovider` -> exit @@CODE4@@, `@@SUM4@@`. Passing tests do not cover this gap, because no existing test asserts that an unpublished Post/Reel is hidden from `GET /<id>/`.
- Root cause (read in code): `content/views.py` -> `PostDetailView` and `ReelDetailView` are `AllowAny` for GET, resolve the object with `_get_post_or_404` / `_get_reel_or_404` (`Post.objects` / `Reel.objects`: only soft-deleted rows are excluded, any moderation status is served), and serialize with `PostSerializer` / `ReelSerializer`, which include `status` and `rejection_reason`. The Flutter public detail screens hide non-published items client-side (P-095 D-1), so the app looks correct while the API still serves the content.
- Related (already open): S-1 (Like/Save accept an unpublished post by id) comes from the same habit of using `model.objects` where `model.published_objects` is the sanctioned manager.
- Verdict: **@@ROW4@@**
- Finding F-99-2 (OPEN, High): a pending or rejected Post/Reel (for example one rejected for harmful content) stays readable by ANY anonymous client who knows or guesses its integer id, together with its moderation status and rejection reason. This is exactly the "moderation bypass" Section 28 names. NOT fixed inside P-099, because the correct fix changes behavior other parts rely on (owner needs to see their own rejected item, which is also the root of Flutter D-1; moderators need to see pending items). Proposed fix: in `PostDetailView.get_object` and `ReelDetailView.get_object`, for GET only, return the object when it is published (and, for Reels, `processing_status == ready`), OR the requester owns the business, OR the requester has `can_moderate_content`; otherwise raise `NotFound` (404, so existence is not revealed). Tests to add: anonymous and non-owner GET of a pending/rejected Post and Reel -> 404; owner and moderator GET -> 200. Same check for `/reels/<id>/`. Estimated size: about 2 small view changes plus about 8 tests.
'@
$row5Tpl = @'
## Row 5 - Malicious file upload
- What was checked: every file-accepting field in the project is behind `core.media.validate_upload()` (libmagic content sniffing + size cap, P-013) on every writable serializer path.
- Method 1 (static; `FileField|ImageField` declarations in every `models.py`, plus any `serializers.FileField/ImageField` declared directly in a serializer):
@@FFDECL@@
  - Serializer-declared file fields: @@SERFF@@ (expected 0)
- Method 2 (introspection inside the running `web` container: for each model file field, every `ModelSerializer` bound to that model is instantiated; the field is classified as READ_ONLY, NOT_EXPOSED, WRITABLE_VALIDATED (its `validate_<field>` source calls `validate_upload`) or WRITABLE_NOT_VALIDATED):
@@FIELDS@@
  - Counts: validated-writable = @@NVALID@@ ; unguarded = @@NBAD@@ ; flagged read-side-by-name only = @@NREAD@@. A READ_SIDE_BY_NAME line is a serializer whose class name contains Public/List/Read/Summary/Nested; it would only matter if it were used on a write endpoint (verify if any appear).
  - `Reel.thumbnail` is not user-uploadable: it must show READ_ONLY (it is produced by ffmpeg in `content/tasks.py` from the already-validated video).
- Method 3 (existing rejection tests, real HTTP through the API client):
  - `@@CMD5A@@` -> exit @@CODE5A@@, `@@SUM5A@@`
  - `@@CMD5B@@` -> exit @@CODE5B@@, `@@SUM5B@@` (renamed-extension / disguised-executable uploads rejected for Post image, Reel video, Story media and Product image).
- Observation O-99-1 (Low): the Django admin registers models that own file fields (below). Admin forms upload through the model field and do NOT call `validate_upload()`, so a staff user could attach an unvalidated file there. Staff-only and outside the public attack surface; documented, not changed.
@@ADMIN@@
- Verdict: **@@ROW5@@**
'@
$row4 = $row4Tpl
$m4 = @{
    "@@HITSA@@" = $hitsAList; "@@UNEXPECTED@@" = $unexpList; "@@HITSB@@" = $hitsBList
    "@@PENDSTATUS@@" = [string]$pendingStatus; "@@PENDCODE@@" = [string]$pendCode
    "@@LEAKSTATUS@@" = [string]$leaksStatus; "@@LEAKREASON@@" = [string]$leaksReason
    "@@PUBCODE@@" = [string]$pubCode; "@@LISTCODE@@" = [string]$listCode; "@@LISTHAS@@" = [string]$listHasPending
    "@@REELNOTE@@" = $(if ($reelId -and $reelId -ne "none") { "reel id $reelId, status $reelStatus" } else { "no non-published Reel exists in the dev database, so this was not tested live; it shares the same code path (content/views.py ReelDetailView)" })
    "@@REELCODE@@" = $(if ($reelCode -ne 0) { [string]$reelCode } else { "not tested" })
    "@@LIVESTATE@@" = $liveState; "@@CODE4@@" = [string]$code4; "@@SUM4@@" = $sum4; "@@ROW4@@" = $row4Verdict
}
foreach ($k in $m4.Keys) { $row4 = $row4.Replace($k, $m4[$k]) }
$row5 = $row5Tpl
$m5 = @{
    "@@FFDECL@@" = $ffDeclList; "@@SERFF@@" = [string]@($serFileFields).Count; "@@FIELDS@@" = $fieldList
    "@@NVALID@@" = [string]$validated.Count; "@@NBAD@@" = [string]$bad.Count; "@@NREAD@@" = [string]$readSide.Count
    "@@CMD5A@@" = $cmd5a; "@@CODE5A@@" = [string]$code5a; "@@SUM5A@@" = $sum5a
    "@@CMD5B@@" = $cmd5b; "@@CODE5B@@" = [string]$code5b; "@@SUM5B@@" = $sum5b
    "@@ADMIN@@" = $adminList; "@@ROW5@@" = $row5Verdict
}
foreach ($k in $m5.Keys) { $row5 = $row5.Replace($k, $m5[$k]) }

$doc = $doc.Replace('Status: IN PROGRESS - Step 1 of 3 done (rows 1-3). Rows 4-7 are filled by Steps 2 and 3.', 'Status: IN PROGRESS - Steps 1 and 2 of 3 done (rows 1-5). Rows 6-7 are filled by Step 3.')
$doc = $doc.Replace('| 4 | Moderation bypass | PENDING (Step 2) | Row 4 |', "| 4 | Moderation bypass | $row4Verdict | Row 4 |")
$doc = $doc.Replace('| 5 | Malicious file upload | PENDING (Step 2) | Row 5 |', "| 5 | Malicious file upload | $row5Verdict | Row 5 |")
$doc = [regex]::Replace($doc, '(?m)^<!-- ROW4_PLACEHOLDER:.*-->$', { param($mm) $row4 })
$doc = [regex]::Replace($doc, '(?m)^<!-- ROW5_PLACEHOLDER:.*-->$', { param($mm) $row5 })

$f992State = "OPEN"
if ($pendCode -eq 404) { $f992State = "NOT REPRODUCED" }
elseif ($pendCode -ne 200) { $f992State = "UNCONFIRMED" }
$newRows = "| F-99-2 | P-099 Row 4 | Unpublished (pending/rejected) Post and Reel are readable by anyone via GET /api/v1/posts/<id>/ and /api/v1/reels/<id>/ (AllowAny, default manager), with status and rejection_reason. Fix proposal in Row 4 (owner or moderator or published only, else 404). | $f992State | High |`n| O-99-1 | P-099 Row 5 | Django admin file uploads bypass validate_upload() (staff only). | OPEN (observation) | Low |"
$doc = [regex]::Replace($doc, '(?m)^\| F-99-1 \|.*$', { param($mm) $mm.Value + "`n" + $newRows })

[System.IO.File]::WriteAllText((Join-Path $BackendDir $ReportFile), $doc, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "Updated $ReportFile ($((Get-Item $ReportFile).Length) bytes). Backup: $ReportFile.step2.bak"

# ---------------------------------------------------------------------
# Console summary to paste back
# ---------------------------------------------------------------------
Write-Section "STEP 2 RESULT - paste everything below this line back to Claude"
Write-Host "backend_commit          : $commit"
Write-Host "ROW4 verdict            : $row4Verdict"
Write-Host "ROW4 raw status filters : total=$(@($hitsA).Count) outside_allowlist=$(@($unexpectedA).Count)"
if (@($unexpectedA).Count -gt 0) { foreach ($u in $unexpectedA) { Write-Host "    UNEXPECTED $($u.File):$($u.Line): $($u.Text)" } }
Write-Host "ROW4 view-layer default-manager reads: $(@($hitsB).Count)"
Write-Host "ROW4 live anon GET pending post : $pendCode (pending status=$pendingStatus) | published control=$pubCode | public list has pending=$listHasPending | body leaks status=$leaksStatus rejection_reason=$leaksReason"
Write-Host "ROW4 live anon GET unpublished reel: id=$reelId status=$reelStatus code=$reelCode"
Write-Host "ROW4 tests              : exit=$code4 | $sum4"
Write-Host "ROW5 verdict            : $row5Verdict"
Write-Host "ROW5 introspection      : validated-writable=$($validated.Count) unguarded=$($bad.Count) read-side-by-name=$($readSide.Count) admin-registered=$($adminOpen.Count)"
foreach ($l in $fieldLines) { Write-Host "    $l" }
Write-Host "ROW5 tests media+chat   : exit=$code5a | $sum5a"
Write-Host "ROW5 tests spoofed      : exit=$code5b | $sum5b"
Write-Host "Files created           : $EvidenceFile , $ReportFile.step2.bak"
Write-Host "Files modified          : $ReportFile"
Write-Host "(if anything says FAIL/CHECK/INCONCLUSIVE, also send the matching section of $EvidenceFile)"