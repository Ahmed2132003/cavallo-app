# =====================================================================
# p099_step3.ps1  --  PART P-099, STEP 3 of 3 (final)
# Scope: threats 6-7 of Section 28 (Webhook spoofing, Counter races),
#        cleanup of Step 2's leftover test data, full backend pytest run,
#        final sign-off in the report, and the PROJECT_PROGRESS.md entry.
#
# What this script does:
#   - CLEANS UP the throwaway business/user/posts that Step 2 could not
#     delete (ProtectedError), including their ModerationQueue rows.
#   - CREATES  p099_step3_evidence.txt
#   - MODIFIES SECTION_28_THREAT_MODEL_VERIFICATION.md (surgical: Rows 6-7,
#              final sign-off, summary table, Status line, findings rows, two
#              small corrections to the Row 4 text) - .step3.bak written first.
#   - APPENDS  one P-099 section to the END of PROJECT_PROGRESS.md
#              (nothing above it is touched) - .step3.bak written first.
#   - Creates and deletes throwaway users/business/posts for the live tests.
#   - Changes NO application code.
#
# Run from: D:\Cavallo\scd-backend   (PowerShell, Docker stack already up)
#   powershell -ExecutionPolicy Bypass -File .\p099_step3.ps1
# Takes about 25 minutes (the full backend suite is ~16 min).
# Refuses to run twice (Row 6 already in the report) unless -Force.
# =====================================================================
param([switch]$Force)

$ErrorActionPreference = "Continue"
$BackendDir = "D:\Cavallo\scd-backend"
$BaseUrl    = "http://localhost:8095"
$ReportFile = "SECTION_28_THREAT_MODEL_VERIFICATION.md"
$ProgressFile = "PROJECT_PROGRESS.md"
$EvidenceFile = "p099_step3_evidence.txt"
$Step2Evidence = "p099_step2_evidence.txt"

Set-Location $BackendDir

function Write-Section([string]$t) { Write-Host ""; Write-Host "=== $t ===" -ForegroundColor Cyan }
function Add-Evidence([string]$title, [string]$text) {
    $sep = "`r`n" + ("=" * 70) + "`r`n" + $title + "`r`n" + ("=" * 70) + "`r`n"
    [System.IO.File]::AppendAllText((Join-Path $BackendDir $EvidenceFile), $sep + $text + "`r`n")
}
function Get-Kv([string]$text, [string]$key) {
    if (-not $text) { return $null }
    $m = [regex]::Match($text, "P99\|" + [regex]::Escape($key) + "=([^\r\n]*)")
    if ($m.Success) { return $m.Groups[1].Value.Trim() } else { return $null }
}
function Invoke-DjangoShell([string]$PyCode) {
    return ($PyCode | & docker compose exec -T web python manage.py shell 2>&1 | Out-String)
}
function Invoke-PostFile([string]$Url, [string]$BodyText) {
    $tmp = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($tmp, $BodyText, (New-Object System.Text.UTF8Encoding($false)))
    $curlArgs = @('-s', '-m', '20', '-X', 'POST', '-H', 'Content-Type: application/json', '-d', "@$tmp", '-w', "`n%{http_code}", $Url)
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
function Indent-Hits($hits) {
    $t = ((@($hits) | ForEach-Object { "    $($_.File):$($_.Line): $($_.Text)" }) -join "`n")
    if (-not $t) { $t = "    (none)" }
    return $t
}
function Get-Verdict-Line([string]$text) {
    $m = ([regex]::Matches($text, '(?m)^.*\b(passed|failed|error)\b.*\bin [\d\.]+s.*$') | Select-Object -Last 1)
    if ($m) { return $m.Value.Trim() } else { return "(no pytest summary line found - see evidence file)" }
}

# Shared python helper: hard-delete throwaway users together with everything that PROTECTs them.
$pyPurgeLib = @'
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db.models.deletion import ProtectedError
from content.models import Post, Reel
from moderation.models import ModerationQueue
U = get_user_model()

def purge(obj, depth=0):
    if depth > 8:
        return False
    for _ in range(6):
        try:
            if hasattr(obj, "hard_delete"):
                obj.hard_delete()
            else:
                obj.delete()
            return True
        except ProtectedError as exc:
            for p in list(exc.protected_objects):
                purge(p, depth + 1)
    return False

def purge_users(qs):
    done = 0
    for u in list(qs):
        bp = getattr(u, "business_profile", None)
        if bp is not None:
            for model in (Post, Reel):
                objs = list(model.all_objects.filter(business=bp))
                ct = ContentType.objects.get_for_model(model)
                ModerationQueue.objects.filter(content_type=ct, object_id__in=[o.pk for o in objs]).delete()
                for o in objs:
                    purge(o)
        if purge(u):
            done += 1
    return done
'@

# ---------------------------------------------------------------------
# 0. Preflight
# ---------------------------------------------------------------------
Write-Section "0. Preflight"
if (-not (Test-Path ".\manage.py")) { Write-Host "STOP: run from $BackendDir" -ForegroundColor Red; exit 1 }
foreach ($f in @($ReportFile, $ProgressFile, $Step2Evidence)) {
    if (-not (Test-Path ".\$f")) { Write-Host "STOP: $f not found. Steps 1 and 2 must be run first." -ForegroundColor Red; exit 1 }
}
$doc = [System.IO.File]::ReadAllText((Join-Path $BackendDir $ReportFile))
if (($doc -match '(?m)^## Row 6 - Webhook spoofing') -and (-not $Force)) {
    Write-Host "STOP: Row 6 is already in the report (Step 3 already applied). Use -Force only after restoring the .step3.bak copies." -ForegroundColor Red
    exit 1
}
$anchorsNeeded = @(
    'Status: IN PROGRESS - Steps 1 and 2 of 3 done (rows 1-5). Rows 6-7 are filled by Step 3.',
    '| 6 | Webhook spoofing | PENDING (Step 3) | Row 6 |',
    '| 7 | Counter race conditions | PENDING (Step 3) | Row 7 |'
)
foreach ($a in $anchorsNeeded) {
    if (-not $doc.Contains($a)) { Write-Host "STOP: anchor not found in the report: $a" -ForegroundColor Red; exit 1 }
}
foreach ($rx in @('(?m)^<!-- ROW6_PLACEHOLDER:.*-->$', '(?m)^<!-- ROW7_PLACEHOLDER:.*-->$', '(?m)^<!-- FINAL_PLACEHOLDER:.*-->$', '(?m)^\| O-99-1 \|.*$')) {
    if (-not ($doc -match $rx)) { Write-Host "STOP: report anchor missing: $rx" -ForegroundColor Red; exit 1 }
}
$health = (& curl.exe -s -o NUL -w '%{http_code}' "$BaseUrl/health/" 2>&1 | Out-String).Trim()
Write-Host "Health check -> $health"
if ($health -ne "200") { Write-Host "STOP: backend not healthy. Run: docker compose up -d" -ForegroundColor Red; exit 1 }

Copy-Item ".\$ReportFile" ".\$ReportFile.step3.bak" -Force
Copy-Item ".\$ProgressFile" ".\$ProgressFile.step3.bak" -Force
if (Test-Path ".\$EvidenceFile") { Remove-Item ".\$EvidenceFile" -Force }
$stamp = Get-Date -Format "yyyyMMddHHmmss"
$today = Get-Date -Format "yyyy-MM-dd"
$commit = (& git rev-parse --short HEAD 2>&1 | Out-String).Trim()
Add-Evidence "RUN HEADER" "date=$today stamp=$stamp backend_commit=$commit health=$health"

$pyFiles = Get-ChildItem . -Recurse -Filter *.py -File | Where-Object { $_.FullName -notmatch '\\(tests|migrations|\.pytest_cache|__pycache__|testapp)\\' -and $_.Name -notmatch '^test_' -and $_.Name -ne 'conftest.py' }

# ---------------------------------------------------------------------
# A. Clean up Step 2's leftover throwaway data
# ---------------------------------------------------------------------
Write-Section "A. Cleanup of Step 2 leftovers"
$s2 = [System.IO.File]::ReadAllText((Join-Path $BackendDir $Step2Evidence))
$ownerEmail = Get-Kv $s2 "owner_email"
$cleanupNote = "no owner_email found in $Step2Evidence (nothing to clean)"
if ($ownerEmail) {
    $pyA = $pyPurgeLib + @'

qs = U.objects.filter(email__iexact="__EMAIL__")
print("P99|before=" + str(qs.count()))
print("P99|purged=" + str(purge_users(qs)))
print("P99|after=" + str(U.objects.filter(email__iexact="__EMAIL__").count()))
'@
    $outA = Invoke-DjangoShell ($pyA.Replace("__EMAIL__", $ownerEmail))
    Add-Evidence "A - cleanup of Step 2 leftovers ($ownerEmail)" $outA
    $cleanupNote = "$ownerEmail : before=$(Get-Kv $outA 'before') purged=$(Get-Kv $outA 'purged') after=$(Get-Kv $outA 'after')"
}
Write-Host "Cleanup -> $cleanupNote"

# ---------------------------------------------------------------------
# ROW 6 - Webhook spoofing
# ---------------------------------------------------------------------
Write-Section "Row 6: Webhook spoofing"

# 6a. static ordering inside PaymobWebhookView.post
$viewText = [System.IO.File]::ReadAllText((Join-Path $BackendDir "payments\views.py"))
$clsIdx = $viewText.IndexOf("class PaymobWebhookView")
$cls = ""
if ($clsIdx -ge 0) { $cls = $viewText.Substring($clsIdx) }
$iSig = $cls.IndexOf("verify_webhook_signature(")
$iParse = $cls.IndexOf("_parse_webhook_payload(")
$iProc = $cls.IndexOf("process_webhook_event(")
$usesRequestData = ($cls -match 'request\.data')
$orderOk = ($iSig -ge 0) -and ($iParse -gt $iSig) -and ($iProc -gt $iParse)
$authOff = ($cls -match 'authentication_classes\s*=\s*\[\]')
$gwText = ""
$gwFiles = Get-ChildItem ".\payments\gateways" -Filter *.py -File -ErrorAction SilentlyContinue
foreach ($g in $gwFiles) { $gwText += [System.IO.File]::ReadAllText($g.FullName) }
$constTime = ($gwText -match 'compare_digest')
$staticSummary = "class found=$($clsIdx -ge 0); verify_webhook_signature at +$iSig, _parse_webhook_payload at +$iParse, process_webhook_event at +$iProc (must be increasing); request.data used in view=$usesRequestData; constant-time compare (hmac.compare_digest) in gateway=$constTime"
Add-Evidence "ROW 6a - static ordering of PaymobWebhookView.post" $staticSummary
Write-Host "6a static: order ok=$orderOk ; request.data used=$usesRequestData ; compare_digest=$constTime"

# 6b. live forged requests
$py6 = @'
import json
from django.conf import settings as s
from payments.tests.webhook_helpers import build_payload, sign, db_snapshot
p = build_payload()
print("P99|gateway=" + str(s.PAYMENT_GATEWAY))
print("P99|secret_configured=" + str(bool(s.PAYMOB_WEBHOOK_SECRET)))
print("P99|body=" + json.dumps(p))
print("P99|sig_wrong_secret=" + sign(p, "attacker-secret"))
print("P99|snapshot=" + repr(db_snapshot()))
'@
$out6 = Invoke-DjangoShell $py6
Add-Evidence "ROW 6b - setup (secret value is never printed)" $out6
$body6 = Get-Kv $out6 "body"; $sigWrong = Get-Kv $out6 "sig_wrong_secret"; $snapBefore = Get-Kv $out6 "snapshot"
$secretCfg = Get-Kv $out6 "secret_configured"; $gateway = Get-Kv $out6 "gateway"
$webhookUrl = "$BaseUrl/api/v1/payments/webhook/paymob/"
$cases = @()
if ($body6 -and $sigWrong) {
    $cases += [pscustomobject]@{ Name = "no hmac parameter"; Url = $webhookUrl; Body = $body6 }
    $cases += [pscustomobject]@{ Name = "empty hmac"; Url = "$webhookUrl`?hmac="; Body = $body6 }
    $cases += [pscustomobject]@{ Name = "garbage hmac (deadbeef)"; Url = "$webhookUrl`?hmac=deadbeef"; Body = $body6 }
    $cases += [pscustomobject]@{ Name = "valid-looking hmac signed with the wrong secret"; Url = "$webhookUrl`?hmac=$sigWrong"; Body = $body6 }
    $cases += [pscustomobject]@{ Name = "non-JSON body"; Url = "$webhookUrl`?hmac=abc"; Body = "not json" }
}
$caseResults = @()
foreach ($c in $cases) {
    $r = Invoke-PostFile $c.Url $c.Body
    $caseResults += [pscustomobject]@{ Name = $c.Name; Status = $r.Status }
    Write-Host ("  {0} -> {1}" -f $c.Name, $r.Status)
}
$out6b = Invoke-DjangoShell $py6
$snapAfter = Get-Kv $out6b "snapshot"
$snapSame = ($snapBefore -and ($snapBefore -eq $snapAfter))
Add-Evidence "ROW 6c - forged requests" ((($caseResults | ForEach-Object { "$($_.Name) -> $($_.Status)" }) -join "`r`n") + "`r`nsnapshot unchanged: $snapSame`r`nbefore: $snapBefore`r`nafter : $snapAfter")
$all400 = ($caseResults.Count -eq 5) -and (@($caseResults | Where-Object { $_.Status -ne 400 }).Count -eq 0)
Write-Host "6c all five forged requests rejected with 400: $all400 ; DB snapshot unchanged: $snapSame ; secret configured in dev: $secretCfg"

# 6d. existing payments tests (includes the P-090 critical test: invalid signature, zero side effects)
$cmd6 = "docker compose exec -T web pytest payments -q -p no:cacheprovider"
Write-Host "Running: $cmd6"
$out6t = & docker compose exec -T web pytest payments -q -p no:cacheprovider 2>&1 | Out-String
$code6 = $LASTEXITCODE
$sum6 = Get-Verdict-Line $out6t
Add-Evidence "ROW 6d - $cmd6 (exit $code6)" $out6t
Write-Host "6d tests: exit=$code6 | $sum6"

$row6Verdict = "FAIL"
if ($orderOk -and (-not $usesRequestData) -and $all400 -and $snapSame -and ($code6 -eq 0)) { $row6Verdict = "PASS" }
Write-Host "Row 6 -> $row6Verdict"

# ---------------------------------------------------------------------
# ROW 7 - Counter race conditions
# ---------------------------------------------------------------------
Write-Section "Row 7: Counter race conditions"

# 7a. static: read-then-write patterns
$patRW = '(?m)^[ \t]*[^#\s][^\r\n]*?\.\w*(_count|average_rating)\s*(\+=|-=|=(?!=))'
$hitsRW = @(Find-Regex $patRW $pyFiles)
$patRW2 = '\bF\(\s*["'']\w*(_count)["'']\s*\)'
$hitsF = @(Find-Regex $patRW2 $pyFiles)
$hitsAgg = @(Find-Regex '\bAvg\(' $pyFiles)
Add-Evidence "ROW 7a - attribute assignment / += on a *_count or average_rating field (read-then-write candidates)" (Format-Hits $hitsRW)
Add-Evidence "ROW 7a - F('<x>_count') atomic updates" (Format-Hits $hitsF)
Add-Evidence "ROW 7a - Avg( aggregate recompute sites" (Format-Hits $hitsAgg)
Write-Host "7a static: read-then-write hits=$($hitsRW.Count) ; F() counter updates=$($hitsF.Count) ; Avg() recompute sites=$($hitsAgg.Count)"

# 7b. LIVE concurrency: 8 users like one post at once; 8 customers rate one business at once (3 rounds)
$prefix = "p99c-$stamp"
$py7 = $pyPurgeLib + @'

import threading
from django.db import connections
from rest_framework.test import APIClient
from businesses.services import create_business_profile
from businesses.models import BusinessProfile
from ratings.models import Rating
from social.models import Like

PREFIX = "__PREFIX__"
N = 8

def mk(i, kind):
    e = "%s-%s-%d@example.com" % (PREFIX, kind, i)
    return U.objects.create_user(
        username=e, email=e, password="P99Throwaway!2026pass",
        account_type=("business" if kind == "biz" else "customer"),
    )

def mk_business(i):
    return create_business_profile(
        user=mk(i, "biz"), business_name="P99 conc %d" % i,
        business_type="trader", country="Egypt", city="Cairo",
    )

def run(n, fn):
    barrier = threading.Barrier(n)
    results = [None] * n
    def worker(i):
        try:
            barrier.wait(timeout=30)
            results[i] = fn(i)
        except Exception as exc:
            results[i] = "ERR " + repr(exc)
        finally:
            connections.close_all()
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results

try:
    custs = [mk(i, "c") for i in range(N)]

    # A. likes (F() counter)
    b0 = mk_business(0)
    post = Post.objects.create(business=b0, caption=PREFIX + "-likes", status=Post.Status.PUBLISHED)
    def like(i):
        c = APIClient(HTTP_HOST="localhost")
        c.force_authenticate(user=custs[i])
        return c.post("/api/v1/likes/", {"content_type": "post", "object_id": post.pk}, format="json").status_code
    res = run(N, like)
    post.refresh_from_db()
    rows = Like.objects.filter(user__in=custs, object_id=post.pk).count()
    print("P99|likes=statuses:%s likes_count:%s like_rows:%s expected:%s" % (res, post.likes_count, rows, N))

    # B. ratings (aggregate recompute), 3 rounds on fresh businesses
    for rnd in range(1, 4):
        b = mk_business(rnd)
        def rate(i):
            c = APIClient(HTTP_HOST="localhost")
            c.force_authenticate(user=custs[i])
            return c.post("/api/v1/businesses/%d/rate/" % b.pk, {"score": 1 + (i % 5), "review_text": "p99"}, format="json").status_code
        res = run(N, rate)
        b.refresh_from_db()
        actual = Rating.objects.filter(business=b)
        n_rows = actual.count()
        avg_actual = round(sum(r.score for r in actual) / float(n_rows), 2) if n_rows else 0
        print("P99|rating_round%d=statuses:%s ratings_count:%s rating_rows:%s average_rating:%s actual_average:%s" % (rnd, res, b.ratings_count, n_rows, b.average_rating, avg_actual))
finally:
    purged = purge_users(U.objects.filter(email__startswith=PREFIX + "-"))
    print("P99|purged=" + str(purged))
    print("P99|remaining=" + str(U.objects.filter(email__startswith=PREFIX + "-").count()))
'@
$out7 = Invoke-DjangoShell ($py7.Replace("__PREFIX__", $prefix))
Add-Evidence "ROW 7b - live concurrency test (threads, real API views, throwaway data prefix $prefix)" $out7
$likesLine = Get-Kv $out7 "likes"
$r1 = Get-Kv $out7 "rating_round1"; $r2 = Get-Kv $out7 "rating_round2"; $r3 = Get-Kv $out7 "rating_round3"
$remaining = Get-Kv $out7 "remaining"
$likesOk = $false
if ($likesLine) {
    $mc = [regex]::Match($likesLine, 'likes_count:(\d+) like_rows:(\d+) expected:(\d+)')
    if ($mc.Success) { $likesOk = ($mc.Groups[1].Value -eq $mc.Groups[3].Value) -and ($mc.Groups[2].Value -eq $mc.Groups[3].Value) }
}
$httpBad = 0
foreach ($ln in @($likesLine, $r1, $r2, $r3)) {
    if ($ln) {
        $ms = [regex]::Match($ln, 'statuses:\[([^\]]*)\]')
        if ($ms.Success) {
            foreach ($tok in ($ms.Groups[1].Value -split ',')) { $t = $tok.Trim(); if ($t -ne '200' -and $t -ne '201') { $httpBad++ } }
        } else { $httpBad++ }
    }
}
Write-Host "7b requests that did not return 200/201: $httpBad"
$ratingMismatch = 0; $ratingRoundsRead = 0
foreach ($rr in @($r1, $r2, $r3)) {
    if ($rr) {
        $ratingRoundsRead++
        $mr = [regex]::Match($rr, 'ratings_count:(\d+) rating_rows:(\d+) average_rating:([\d\.]+) actual_average:([\d\.]+)')
        if ($mr.Success) {
            $avgEq = ([math]::Abs([double]$mr.Groups[3].Value - [double]$mr.Groups[4].Value) -lt 0.011)
            if (($mr.Groups[1].Value -ne $mr.Groups[2].Value) -or (-not $avgEq)) { $ratingMismatch++ }
        } else { $ratingMismatch++ }
    }
}
Write-Host "7b likes: $likesLine"
Write-Host "7b ratings: round1=$r1"
Write-Host "          round2=$r2"
Write-Host "          round3=$r3"
Write-Host "7b rounds read=$ratingRoundsRead ; rounds with a lost update=$ratingMismatch ; leftover throwaway users=$remaining"

# 7c. existing counter tests
$cmd7 = "docker compose exec -T web pytest social ratings reports -q -p no:cacheprovider"
Write-Host "Running: $cmd7"
$out7t = & docker compose exec -T web pytest social ratings reports -q -p no:cacheprovider 2>&1 | Out-String
$code7 = $LASTEXITCODE
$sum7 = Get-Verdict-Line $out7t
Add-Evidence "ROW 7c - $cmd7 (exit $code7)" $out7t
Write-Host "7c tests: exit=$code7 | $sum7"

$row7Verdict = "PASS"
$f993State = "OPEN"
if ($hitsRW.Count -gt 0) { $row7Verdict = "FAIL (read-then-write counter pattern found - see report)" }
elseif ($httpBad -gt 0) { $row7Verdict = "INCONCLUSIVE ($httpBad concurrent request(s) did not return 200/201 - see evidence)" }
elseif (-not $likesOk) { $row7Verdict = "FAIL (concurrent likes lost an update)" }
elseif ($ratingRoundsRead -lt 3) { $row7Verdict = "INCONCLUSIVE (concurrency test did not complete - see evidence)" }
elseif ($ratingMismatch -gt 0) { $row7Verdict = "FAIL (finding F-99-3 reproduced: concurrent ratings lost an update)" }
else { $row7Verdict = "PARTIAL (F()-counters PASS; finding F-99-3: rating recompute is not serialized, not reproduced in 3 rounds)" }
if ($code7 -ne 0 -and $row7Verdict -like "PARTIAL*") { $row7Verdict = "CHECK (existing tests failed)" }
if ($ratingMismatch -gt 0) { $f993State = "OPEN (reproduced live)" } else { $f993State = "OPEN (code-level risk, not reproduced in 3 rounds)" }
Write-Host "Row 7 -> $row7Verdict"

# ---------------------------------------------------------------------
# FULL BACKEND SUITE
# ---------------------------------------------------------------------
Write-Section "Full backend pytest suite (about 16 minutes)"
$cmdFull = "docker compose exec -T web pytest -q -p no:cacheprovider"
$outFull = & docker compose exec -T web pytest -q -p no:cacheprovider 2>&1 | Out-String
$codeFull = $LASTEXITCODE
$sumFull = Get-Verdict-Line $outFull
Add-Evidence "FULL SUITE - $cmdFull (exit $codeFull)" $outFull
$fullNote = ""
if ($codeFull -eq 0 -and $sumFull -notmatch '1643 passed, 1 skipped, 1 xfailed') { $fullNote = " (P-096 recorded 1643 passed, 1 skipped, 1 xfailed: the counts differ, check whether tests were added or removed since)" }
Write-Host "Full suite: exit=$codeFull | $sumFull$fullNote"

# ---------------------------------------------------------------------
# Row 4 corrections (data from Step 2 evidence)
# ---------------------------------------------------------------------
Write-Section "Updating $ReportFile"
$statusLeak = ($s2 -match '"status"\s*:\s*"pending_review"')
$oldLeak = 'body leaks `"status": "pending"`: False'
$newLeak = 'body leaks the moderation status (`"status": "pending_review"`): True'
if ($statusLeak -and $doc.Contains($oldLeak)) {
    $doc = $doc.Replace($oldLeak, $newLeak)
    Write-Host "Row 4 text corrected: the response body does leak status=pending_review (Step 2's check looked for the wrong literal)."
} else {
    Write-Host "Row 4 status-leak correction skipped (evidence did not confirm it, or the sentence was edited)." -ForegroundColor Yellow
}
$oldM2 = '- Method 2 (default-manager reads of Post/Reel/any model in view-layer files:'
$note4 = '  - Reviewed in Step 3: the three hits outside the allow-list are `moderation\tasks.py:54` and `:74` (the SLA-breach job reading `ModerationQueue`, not public content) and `payments\tasks.py:88` (reconciliation reading `payments.Transaction`). None of them serves content to a client, so they are benign and not a bypass risk.'
if ($doc.Contains($oldM2) -and -not $doc.Contains('Reviewed in Step 3: the three hits')) {
    $doc = $doc.Replace($oldM2, $note4 + "`n" + $oldM2)
}

# ---------------------------------------------------------------------
# Row 6 / Row 7 / final sign-off sections
# ---------------------------------------------------------------------
$caseList = (($caseResults | ForEach-Object { "    - $($_.Name) -> $($_.Status)" }) -join "`n"); if (-not $caseList) { $caseList = "    - (live requests did not run - see evidence)" }
$row6Tpl = @'
## Row 6 - Webhook spoofing
- What was checked: `PaymobWebhookView` (`payments/views.py`, `POST /api/v1/payments/webhook/paymob/`) verifies the HMAC signature before doing anything else, and forged requests have no effect.
- Method 1 (static, read from the file): order of calls inside the view = verify_webhook_signature -> _parse_webhook_payload -> process_webhook_event. Result: @@STATIC@@. Order correct: @@ORDEROK@@.
- Method 2 (LIVE forged requests, five variants, against the running stack; gateway = `@@GATEWAY@@`, webhook secret configured in this environment: @@SECRETCFG@@):
@@CASES@@
  - Expected 400 for all five; all rejected: @@ALL400@@. A direct database snapshot (subscriptions, transactions, featured subscriptions, business flags) before and after was identical: @@SNAPSAME@@.
- Method 3: `@@CMD6@@` -> exit @@CODE6@@, `@@SUM6@@`. This includes the P-090 critical tests: an invalid signature is rejected with 400 and leaves zero side effects (DB snapshot compared), the signature is checked before the payload is parsed, an invalid signature never reaches processing, an unconfigured (empty) secret rejects even a well-formed signature, tampered replays are rejected, and (P-096) a business owner's JWT cannot replace the signature.
- Limits of this row: a VALID signed webhook was not sent live, because that needs the real Paymob secret; live Paymob stays unverified (Section 7 item 3, unchanged).
- Verdict: **@@ROW6@@**
'@
$row6 = $row6Tpl
$m6 = @{
    "@@STATIC@@" = $staticSummary; "@@ORDEROK@@" = [string]$orderOk; "@@GATEWAY@@" = [string]$gateway; "@@SECRETCFG@@" = [string]$secretCfg
    "@@CASES@@" = $caseList; "@@ALL400@@" = [string]$all400; "@@SNAPSAME@@" = [string]$snapSame
    "@@CMD6@@" = $cmd6; "@@CODE6@@" = [string]$code6; "@@SUM6@@" = $sum6; "@@ROW6@@" = $row6Verdict
}
foreach ($k in $m6.Keys) { $row6 = $row6.Replace($k, $m6[$k]) }

$row7Tpl = @'
## Row 7 - Counter race conditions
- What was checked: every counter (`likes_count`, `comments_count`, `shares_count`, `follower_count` (the real field name; the plan says `followers_count`), `following_count`, `reports_count`, `average_rating`/`ratings_count`) is updated only by an atomic `F()` update or an aggregate recompute, never read-then-write.
- Method 1 (static, regex over all non-test `.py` files; the prompt's `grep -rn "_count +=" apps/` was widened to any attribute assignment or `+=`/`-=` on a `*_count` or `average_rating` attribute):
  - Read-then-write candidates found: @@NRW@@ (expected 0)
@@RWLIST@@
  - Atomic `F("<x>_count")` update sites found: @@NF@@
@@FLIST@@
  - `Avg(` aggregate-recompute sites:
@@AGGLIST@@
- Method 2 (LIVE concurrency, real API views, 8 threads released together by a barrier, throwaway users, removed afterwards):
  - Likes on one published post: `@@LIKES@@`. Result: @@LIKESOK@@ (F() counter did not lose an update).
  - Ratings, 3 rounds of 8 customers rating one fresh business at the same moment:
    - round 1: `@@R1@@`
    - round 2: `@@R2@@`
    - round 3: `@@R3@@`
  - Rounds with a lost update (ratings_count or average different from the real rows): @@NMISS@@ of @@NROUNDS@@. Leftover throwaway users after cleanup: @@REMAINING@@.
- Method 3: `@@CMD7@@` -> exit @@CODE7@@, `@@SUM7@@`.
- Code reading behind F-99-3: `ratings/services.py` `rate_business()` runs `update_or_create` of the Rating, then `Rating.objects.filter(business=...).aggregate(Avg, Count)`, then `BusinessProfile.objects.filter(pk=...).update(average_rating=..., ratings_count=...)` inside `transaction.atomic()`, but takes NO row lock on the BusinessProfile first. Under PostgreSQL's default READ COMMITTED isolation two different customers rating the same business at the same moment can each aggregate before the other's Rating is committed, and the later UPDATE then overwrites the earlier one with a stale count and average. The unique (customer, business) constraint only protects one customer's own row, not the shared aggregate.
- Verdict: **@@ROW7@@**
- Finding F-99-3 (@@F993STATE@@, Medium): proposed fix, small and local: at the start of the `transaction.atomic()` block in `rate_business()`, lock the business row with `BusinessProfile.objects.select_for_update().get(pk=business.pk)` so concurrent recomputes are serialized; add a threaded test (`@pytest.mark.django_db(transaction=True)`, same shape as the live test above) asserting `ratings_count` equals the number of Rating rows after 8 simultaneous ratings. Not applied inside P-099: it changes production code, so it needs your approval first (same rule used for F-99-2).
'@
$row7 = $row7Tpl
$m7 = @{
    "@@NRW@@" = [string]$hitsRW.Count; "@@RWLIST@@" = (Indent-Hits $hitsRW); "@@NF@@" = [string]$hitsF.Count; "@@FLIST@@" = (Indent-Hits $hitsF)
    "@@AGGLIST@@" = (Indent-Hits $hitsAgg); "@@LIKES@@" = [string]$likesLine; "@@LIKESOK@@" = $(if ($likesOk) { "PASS" } else { "FAIL or INCONCLUSIVE" })
    "@@R1@@" = [string]$r1; "@@R2@@" = [string]$r2; "@@R3@@" = [string]$r3; "@@NMISS@@" = [string]$ratingMismatch; "@@NROUNDS@@" = [string]$ratingRoundsRead
    "@@REMAINING@@" = [string]$remaining; "@@CMD7@@" = $cmd7; "@@CODE7@@" = [string]$code7; "@@SUM7@@" = $sum7
    "@@ROW7@@" = $row7Verdict; "@@F993STATE@@" = $f993State
}
foreach ($k in $m7.Keys) { $row7 = $row7.Replace($k, $m7[$k]) }

# Verdict table pulled back out of the report so the sign-off is generated from what is really in it.
$verdicts = @{}
foreach ($n in 1..7) {
    $mv = [regex]::Match($doc, "(?m)^\| $n \| [^|]+\| ([^|]+)\|")
    if ($mv.Success) { $verdicts[$n] = $mv.Groups[1].Value.Trim() } else { $verdicts[$n] = "(see table)" }
}
$verdicts[6] = $row6Verdict; $verdicts[7] = $row7Verdict

$fullVerdict = "FAIL"
if ($codeFull -eq 0) { $fullVerdict = "PASS" }
$finalTpl = @'
## Final sign-off
- Full backend suite after this pass (`@@CMDFULL@@`): exit @@CODEFULL@@, `@@SUMFULL@@`@@FULLNOTE@@. Verdict: **@@FULLVERDICT@@**.
- No application code was changed by P-099, so the suite result also shows nothing regressed during the audit itself.
- Definition of Done (from the P-099 prompt):
  - [x] All seven Section 28 threats re-verified against LIVE code with the methods above (static search, running-stack tests, and the existing test suites), not from the master plan.
  - [x] Small, obvious gaps fixed directly: none were found that qualified. Every gap found changes production behavior, so each is documented as a follow-up instead (F-99-1, F-99-2, F-99-3).
  - [x] Substantial findings documented as specific, prioritized follow-ups (list below).
  - @@DODSUITE@@ Full pytest suite green.
- Result per threat: 1 IDOR = @@V1@@ ; 2 Broken auth = @@V2@@ ; 3 Spam/abuse = @@V3@@ ; 4 Moderation bypass = @@V4@@ ; 5 Malicious upload = @@V5@@ ; 6 Webhook = @@V6@@ ; 7 Counter races = @@V7@@.
- Prioritized follow-ups (nothing below was started):
  1. F-99-2 (High): block anonymous/non-owner reads of unpublished Post/Reel on `GET /<id>/`; fix S-1 (Like/Save on unpublished) in the same part; this also unblocks the owner view needed by Flutter D-1.
  2. F-99-1 (High): per-user message rate limit on the chat send path (REST and WebSocket).
  3. F-99-3 (Medium): `select_for_update` on the business row inside `rate_business()` + a threaded regression test.
  4. S-2 (Medium): product decision on owners rating their own business.
  5. S-3 (Low): P-012 envelope for `ConversationStartView` errors (needs a Flutter parsing check).
  6. O-99-1 (Low): optional `validate_upload` in the admin forms.
- Repo hygiene still pending (carried over, not done here): delete `p095_step*_audit.txt`, `p096_step*.ps1`, `p098_step*.ps1`, `p099_step*.ps1`, `p099_step*_evidence.txt` and all `.bak` copies once you no longer need them; add `celerybeat-schedule` to `.gitignore` (the file has a UTF-16 line appended at the end that git does not read as a rule: re-write it as plain UTF-8).
- Not covered by this pass: live Paymob and live FCM (Section 7 items 3 and 4); the Flutter side beyond the token-storage check; certificate pinning (P-100 is deferred by its own decision record).
'@
$final = $finalTpl
$mf = @{
    "@@CMDFULL@@" = $cmdFull; "@@CODEFULL@@" = [string]$codeFull; "@@SUMFULL@@" = $sumFull; "@@FULLNOTE@@" = $fullNote; "@@FULLVERDICT@@" = $fullVerdict
    "@@DODSUITE@@" = $(if ($codeFull -eq 0) { "[x]" } else { "[ ]" })
    "@@V1@@" = $verdicts[1]; "@@V2@@" = $verdicts[2]; "@@V3@@" = $verdicts[3]; "@@V4@@" = $verdicts[4]; "@@V5@@" = $verdicts[5]; "@@V6@@" = $verdicts[6]; "@@V7@@" = $verdicts[7]
}
foreach ($k in $mf.Keys) { $final = $final.Replace($k, $mf[$k]) }

$openCount = 3 + 3 + 1   # S-1..S-3, F-99-1..3, O-99-1
$doc = $doc.Replace('Status: IN PROGRESS - Steps 1 and 2 of 3 done (rows 1-5). Rows 6-7 are filled by Step 3.', "Status: VERIFICATION COMPLETE (all 7 rows checked against live code on $today; $openCount open items listed under 'Open findings'; no application code changed).")
$doc = $doc.Replace('| 6 | Webhook spoofing | PENDING (Step 3) | Row 6 |', "| 6 | Webhook spoofing | $row6Verdict | Row 6 |")
$doc = $doc.Replace('| 7 | Counter race conditions | PENDING (Step 3) | Row 7 |', "| 7 | Counter race conditions | $row7Verdict | Row 7 |")
$doc = [regex]::Replace($doc, '(?m)^<!-- ROW6_PLACEHOLDER:.*-->$', { param($mm) $row6 })
$doc = [regex]::Replace($doc, '(?m)^<!-- ROW7_PLACEHOLDER:.*-->$', { param($mm) $row7 })
$doc = [regex]::Replace($doc, '(?m)^<!-- FINAL_PLACEHOLDER:.*-->$', { param($mm) $final })
$newRow3 = "| F-99-3 | P-099 Row 7 | rate_business() recomputes average_rating/ratings_count without locking the business row, so concurrent ratings can overwrite each other with a stale aggregate. Fix proposal in Row 7 (select_for_update + threaded test). | $f993State | Medium |"
$doc = [regex]::Replace($doc, '(?m)^\| O-99-1 \|.*$', { param($mm) $mm.Value + "`n" + $newRow3 })
[System.IO.File]::WriteAllText((Join-Path $BackendDir $ReportFile), $doc, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "Updated $ReportFile ($((Get-Item $ReportFile).Length) bytes). Backup: $ReportFile.step3.bak"

# ---------------------------------------------------------------------
# PROJECT_PROGRESS.md: append the P-099 section at the END (nothing above is touched)
# ---------------------------------------------------------------------
Write-Section "Appending the P-099 section to $ProgressFile"
$progPath = Join-Path $BackendDir $ProgressFile
$head = [System.IO.File]::ReadAllText($progPath).Substring(0, 5000)
$nlChar = "`n"
if ($head.Contains("`r`n")) { $nlChar = "`r`n" }
$already = [System.IO.File]::ReadAllText($progPath).Contains("## PART P-099")
if ($already) {
    Write-Host "SKIPPED: $ProgressFile already contains a '## PART P-099' section." -ForegroundColor Yellow
} else {
    $progTpl = @'

## PART P-099 - Full Section-28 Threat-Model Verification Pass - STATUS: VERIFICATION COMPLETE, 3 NEW FINDINGS OPEN (F-99-1, F-99-2, F-99-3)

Phase 19 (Security Hardening). Backend repo only (`cavallo-app`, local `D:\Cavallo\scd-backend`); the Flutter repo was only read (token-storage check). No application code changed, no migrations. Executed as 3 steps (one third each), each a PowerShell script run from `D:\Cavallo\scd-backend`. Date: @@DATE@@. Backend commit when Step 3 ran: `@@COMMIT@@`.

### Layout note
The P-099 prompt writes paths as `apps/...`. This repository has no `apps/` folder; every Django app sits at the repo root. All searches in `SECTION_28_THREAT_MODEL_VERIFICATION.md` use the real layout.

### What was implemented
`SECTION_28_THREAT_MODEL_VERIFICATION.md` (backend repo root): one section per Section 28 threat with the exact method, the result and the findings, plus a summary table, an open-findings table and a final sign-off. Raw output is in `p099_step1_evidence.txt`, `p099_step2_evidence.txt`, `p099_step3_evidence.txt` (throwaway).

### Result per threat
| # | Threat | Verdict |
|---|--------|---------|
| 1 | IDOR | @@V1@@ |
| 2 | Broken auth (JWT theft) | @@V2@@ |
| 3 | Spam/abuse (report/message flood) | @@V3@@ |
| 4 | Moderation bypass | @@V4@@ |
| 5 | Malicious file upload | @@V5@@ |
| 6 | Webhook spoofing | @@V6@@ |
| 7 | Counter race conditions | @@V7@@ |

### Verification evidence (real runs on the developer machine)
- Row 1: `pytest -k permission_sweep` = 266 passed, 1 skipped; 16/16 sweep files present; S-1/S-2/S-3 characterisation tests present.
- Row 2: effective settings in the container: rotation on, blacklist after rotation on, access 15 min, refresh 7 days (within Section 14's 7-30 days; note the code default is 14 and the local `.env` overrides it), blacklist app installed, SECRET_KEY not the insecure default; `accounts/tests/test_auth.py` 15 passed; Flutter: `shared_preferences` imported only by `cache_storage.dart`, `flutter_secure_storage` only by `secure_token_storage.dart`.
- Row 3: live login throttle 401 x5 then 429 (envelope code THROTTLED); live report throttle 400 x10 then 429 (THROTTLED).
- Row 4: static search clean (the three hits outside the allow-list are Celery tasks reading ModerationQueue/Transaction); live anonymous GET of a `pending_review` Post returned 200 with `status` and `rejection_reason` in the body (F-99-2); `pytest content feed stories search` 334 passed.
- Row 5: introspection of every model file field: 5 writable paths all call `validate_upload` (Post.image, Reel.video, Story.media, Message.media, Product.image), Reel.thumbnail read-only (ffmpeg-generated); `pytest core/tests/test_media.py chat/test_media_messages.py` 16 passed; spoofed/disguised upload tests 4 passed.
- Row 6: static order verify -> parse -> process; five live forged webhooks all 400, DB snapshot unchanged: @@WEBHOOKRES@@; `pytest payments`: @@SUM6@@.
- Row 7: no read-then-write counter pattern (@@NRW@@ hits); live concurrent likes: @@LIKESOK@@; live concurrent ratings (3 rounds): @@NMISS@@ of @@NROUNDS@@ rounds lost an update; `pytest social ratings reports`: @@SUM7@@.
- Full backend suite: @@SUMFULL@@@@FULLNOTE@@.

### Findings (all OPEN; none fixed inside P-099 because each changes production behavior)
- **F-99-1 (High, chat):** no throttle or flood control exists anywhere in non-test chat code (REST or WebSocket), so an authenticated user can send unlimited messages. Section 28 names message flood. Follow-up: per-user rate limit on the chat send path + a test that triggers it.
- **F-99-2 (High, content):** `GET /api/v1/posts/<id>/` and `GET /api/v1/reels/<id>/` are `AllowAny` and resolve through `Post.objects` / `Reel.objects`, so a pending or rejected item is readable by anyone who knows its id, with `status` and `rejection_reason`. The Flutter public screens hide it client-side (P-095 D-1), which hides the problem in the app only. Proposed fix: GET returns the object only if published (Reels: processing ready), or the requester owns the business, or has `can_moderate_content`; otherwise 404. Add tests for anonymous, non-owner, owner and moderator. Fix S-1 in the same part.
- **F-99-3 (Medium, ratings):** `ratings.services.rate_business()` recomputes the aggregate without locking the business row (see Row 7). State: @@F993STATE@@. Fix: `select_for_update()` on the BusinessProfile at the start of the atomic block + a threaded regression test.
- **O-99-1 (Low):** Django admin file uploads bypass `validate_upload` (staff only).
- Carried over, still OPEN: S-1, S-2, S-3 (P-096); Phase 17 gate D-1, D-2, G-1 (P-095); F-1 (P-097); full `flutter test` on `main` not run after the 58-file merge; live Paymob and live FCM unverified (Section 7 items 3 and 4).

### Files created
`SECTION_28_THREAT_MODEL_VERIFICATION.md`. Throwaway (safe to delete): `p099_step1.ps1`, `p099_step2.ps1`, `p099_step3.ps1`, `p099_step1_evidence.txt`, `p099_step2_evidence.txt`, `p099_step3_evidence.txt`, `SECTION_28_THREAT_MODEL_VERIFICATION.md.step2.bak`, `SECTION_28_THREAT_MODEL_VERIFICATION.md.step3.bak`, `PROJECT_PROGRESS.md.step3.bak`.

### Files modified
`PROJECT_PROGRESS.md` (this section only). No other existing file.

### Important implementation details
1. Live tests create throwaway users/business/posts and remove them. Step 2's cleanup failed with `ProtectedError` (BusinessProfile PROTECTs its user, and the pending posts had ModerationQueue rows); Step 3 removed that leftover data by hard-deleting the posts, their queue rows, the business and the user (@@CLEANUPNOTE@@). Lesson for future scripts: delete content and ModerationQueue rows before the business, the business before the user.
2. The live concurrency test uses 8 threads released by a `threading.Barrier`, `APIClient(HTTP_HOST="localhost")` with `force_authenticate`, and the real views. A passing run cannot prove the absence of a race; F-99-3 rests on reading `rate_business()`.
3. Step 2's first status-leak check looked for the literal `pending` while the real value is `pending_review`; Step 3 corrected the Row 4 text from the evidence.

### Architecture decisions
None new. The pass enforces existing rules (published_objects for public reads, validate_upload on every upload, signature-first webhook, atomic counters).

### Commands
From `D:\Cavallo\scd-backend`: `docker compose exec -T web pytest -k permission_sweep -q`; `docker compose exec -T web pytest payments -q`; `docker compose exec -T web pytest -q` (full suite, about 16 minutes).

### GitHub references
`cavallo-app` `main` at `@@COMMIT@@` when Step 3 ran (it already contains `cb0a346`, the P-100 deferral record). P-099 commit: (fill in after commit). `cavallo-mobile`: no changes.

### Remaining work
1. Decide and schedule the follow-ups in this order: F-99-2 (+S-1), F-99-1, F-99-3, then S-2, S-3, O-99-1.
2. Repo hygiene: delete the throwaway files listed above and the older `p095_*`, `p096_*`, `p098_*` ones; re-write `.gitignore` as plain UTF-8 and add `celerybeat-schedule`.
3. Record the owner's decision on the carried-over open items (D-1, D-2, G-1, F-1, S-1..S-3) in the Phase 17 and Phase 18 summary lines.

### Exact next starting point
Phase 19 is finished once the owner accepts the findings above (P-100 certificate pinning is deferred by its decision record). The next planned part is P-101 (Phase 20: query/index audit against the Section 9 composite indexes). Recommended to run a small fix part for F-99-2 + S-1 BEFORE P-101, because it is a real moderation-bypass.

### Edits to existing sections
In the Part status index/table add: `P-099 | Full Section-28 Threat-Model Verification Pass | Phase 19 | VERIFIED (7/7 threats checked on live code; 3 new open findings F-99-1 High, F-99-2 High, F-99-3 Medium; no code changed)`.
Set the Phase 19 summary line to: "IN PROGRESS (P-099 verified with open findings F-99-1/F-99-2/F-99-3; P-100 deferred by decision record)".
'@
    $webhookRes = (($caseResults | ForEach-Object { "$($_.Status)" }) -join ", ")
    $prog = $progTpl
    $mp = @{
        "@@DATE@@" = $today; "@@COMMIT@@" = $commit
        "@@V1@@" = $verdicts[1]; "@@V2@@" = $verdicts[2]; "@@V3@@" = $verdicts[3]; "@@V4@@" = $verdicts[4]; "@@V5@@" = $verdicts[5]; "@@V6@@" = $verdicts[6]; "@@V7@@" = $verdicts[7]
        "@@WEBHOOKRES@@" = $webhookRes; "@@SUM6@@" = $sum6; "@@NRW@@" = [string]$hitsRW.Count
        "@@LIKESOK@@" = $(if ($likesOk) { "no lost update" } else { "LOST UPDATE or inconclusive" })
        "@@NMISS@@" = [string]$ratingMismatch; "@@NROUNDS@@" = [string]$ratingRoundsRead; "@@SUM7@@" = $sum7
        "@@SUMFULL@@" = $sumFull; "@@FULLNOTE@@" = $fullNote; "@@F993STATE@@" = $f993State; "@@CLEANUPNOTE@@" = $cleanupNote
    }
    foreach ($k in $mp.Keys) { $prog = $prog.Replace($k, $mp[$k]) }
    $prog = $prog.Replace("`r`n", "`n").Replace("`n", $nlChar)
    [System.IO.File]::AppendAllText($progPath, $prog, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "Appended the P-099 section to $ProgressFile ($((Get-Item $ProgressFile).Length) bytes now). Backup: $ProgressFile.step3.bak"
}

# ---------------------------------------------------------------------
# Console summary to paste back
# ---------------------------------------------------------------------
Write-Section "STEP 3 RESULT - paste everything below this line back to Claude"
Write-Host "backend_commit        : $commit"
Write-Host "cleanup of step 2 data: $cleanupNote"
Write-Host "ROW6 verdict          : $row6Verdict"
Write-Host "ROW6 static           : order ok=$orderOk request.data used=$usesRequestData compare_digest=$constTime"
Write-Host "ROW6 forged requests  : $(($caseResults | ForEach-Object { $_.Status }) -join ', ') | all 400=$all400 | DB unchanged=$snapSame | secret configured in dev=$secretCfg"
Write-Host "ROW6 tests            : exit=$code6 | $sum6"
Write-Host "ROW7 verdict          : $row7Verdict"
Write-Host "ROW7 static           : read-then-write hits=$($hitsRW.Count) F() sites=$($hitsF.Count) Avg sites=$($hitsAgg.Count)"
Write-Host "ROW7 likes            : $likesLine"
Write-Host "ROW7 ratings round1   : $r1"
Write-Host "ROW7 ratings round2   : $r2"
Write-Host "ROW7 ratings round3   : $r3"
Write-Host "ROW7 leftover users   : $remaining"
Write-Host "ROW7 tests            : exit=$code7 | $sum7"
Write-Host "FULL SUITE            : exit=$codeFull | $sumFull$fullNote"
Write-Host "Files created         : $EvidenceFile , $ReportFile.step3.bak , $ProgressFile.step3.bak"
Write-Host "Files modified        : $ReportFile , $ProgressFile (appended at the end)"
Write-Host "(if anything says FAIL/CHECK/INCONCLUSIVE, also send the matching section of $EvidenceFile)"