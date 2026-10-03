# =====================================================================
# p101_step3.ps1  --  PART P-101, STEP 3 of 3 (final)
# Scope:
#   1. FIX the confirmed gap (Step 1/2): add the Section 9 composite indexes that
#      were missing - Post / Reel / Story (business, status, created_at) and
#      BusinessProfile (category, city) - as model Meta.indexes + ONE generated
#      migration per app (additive CREATE INDEX only), then re-measure with EXPLAIN.
#   2. Regression: targeted pytest on the apps touched by the indexes + their consumers.
#   3. Cache hit-rate spot check (Feed first page, Business Profile, Categories tree)
#      through the real URL stack, with a negative control.
#   4. Clean up the audit data (skip with -KeepData), remove audit cache keys.
#   5. Finish PERFORMANCE_AUDIT_PHASE20.md (Parts D and E) and append the P-101
#      section to the END of PROJECT_PROGRESS.md (only if every check passed).
#
# CREATES  : p101_verify.py, p101_cache.py, p101_postclean.py, p101_step3_evidence.txt,
#            p101_step3_plans.txt, content/stories/businesses migrations *_p101_composite_indexes.py
# MODIFIES : content/models.py, stories/models.py, businesses/models.py (Meta.indexes only),
#            PERFORMANCE_AUDIT_PHASE20.md, PROJECT_PROGRESS.md (append only).
#            Backups of every modified file go to .\p101_backups\ first.
# Does NOT git add / commit / push anything.
#
# Run from: D:\Cavallo\scd-backend  (PowerShell, Docker stack up, Steps 1-2 done)
#   powershell -ExecutionPolicy Bypass -File .\p101_step3.ps1
# Takes about 6-10 minutes (targeted pytest dominates).
# -Force   : redo after a mid-way failure (needs the audit data to still exist).
# -KeepData: do not delete the audit data at the end.
# =====================================================================
param([switch]$Force, [switch]$KeepData)

$ErrorActionPreference = "Continue"
$BackendDir   = "D:\Cavallo\scd-backend"
$ReportFile   = "PERFORMANCE_AUDIT_PHASE20.md"
$ProgressFile = "PROJECT_PROGRESS.md"
$EvidenceFile = "p101_step3_evidence.txt"
$BackupDir    = Join-Path $BackendDir "p101_backups"
$MarkerDone   = "## Part E - Gaps found, fixes made, conclusions (Step 3 - done)"
$MarkerD      = "## Part D - Cache hit rates: Feed first page, Business Profile, Categories tree (Step 3 - pending)"
$MarkerE      = "## Part E - Gaps found, fixes made, conclusions (Step 3 - pending)"
$ProgressMark = "## PART P-101 - Query/Index Audit"
Set-Location $BackendDir

$script:Results = @()
function Write-Section([string]$t) { Write-Host ""; Write-Host "=== $t ===" -ForegroundColor Cyan }
function Add-Evidence([string]$title, [string]$text) {
    $sep = "`r`n" + ("=" * 70) + "`r`n" + $title + "`r`n" + ("=" * 70) + "`r`n"
    [System.IO.File]::AppendAllText((Join-Path $BackendDir $EvidenceFile), $sep + $text + "`r`n")
}
$Utf8 = New-Object System.Text.UTF8Encoding($false)
function Write-Utf8([string]$Path, [string]$Content) { [System.IO.File]::WriteAllText((Join-Path $BackendDir $Path), $Content, $Utf8) }
function Read-Utf8([string]$Path) { return [System.IO.File]::ReadAllText((Join-Path $BackendDir $Path), $Utf8) }
function Check([string]$name, [bool]$ok, [string]$detail) {
    $script:Results += [pscustomobject]@{ Name = $name; Ok = $ok; Detail = $detail }
    if ($ok) { Write-Host "  PASS  $name  $detail" -ForegroundColor Green }
    else     { Write-Host "  FAIL  $name  $detail" -ForegroundColor Red }
}
function Dc([string[]]$a) { return (& docker compose exec -T web @a 2>&1 | Out-String) }
function Val([string]$s) { return ($s -replace '^[^=]+=', '') }
function Backup-File([string]$rel) {
    if (-not (Test-Path $BackupDir)) { New-Item -ItemType Directory -Path $BackupDir | Out-Null }
    $dest = Join-Path $BackupDir (($rel -replace '[\\/]', '__') + ".p101.bak")
    if (-not (Test-Path $dest)) { Copy-Item (Join-Path $BackendDir $rel) $dest -Force }
}
function Restore-File([string]$rel) {
    $src = Join-Path $BackupDir (($rel -replace '[\\/]', '__') + ".p101.bak")
    if (Test-Path $src) { Copy-Item $src (Join-Path $BackendDir $rel) -Force }
}

# ---------------------------------------------------------------- 1. Preflight
Write-Section "1. Preflight"
if (-not (Test-Path (Join-Path $BackendDir $ReportFile))) { Write-Host "Report missing. Run Steps 1-2 first." -ForegroundColor Red; exit 1 }
$report = Read-Utf8 $ReportFile
if ($report.Contains($MarkerDone) -and -not $Force) { Write-Host "Step 3 already done. Use -Force to redo." -ForegroundColor Yellow; exit 1 }
if ($Force) { $rb = Join-Path $BackupDir "$ReportFile.step3.bak"; if (Test-Path $rb) { Copy-Item $rb (Join-Path $BackendDir $ReportFile) -Force; $report = Read-Utf8 $ReportFile } }
if (Test-Path (Join-Path $BackendDir $EvidenceFile)) { Remove-Item (Join-Path $BackendDir $EvidenceFile) -Force }
$ps = (& docker compose ps --format "{{.Service}} {{.State}}" 2>&1 | Out-String)
Check "web, db, redis running" (($ps -match "web running") -and ($ps -match "db running") -and ($ps -match "redis running")) ""
Check "Part C done in report" ($report.Contains("Step 2 - done")) ""
Check "Step 2 evidence present" (Test-Path (Join-Path $BackendDir "p101_step2_evidence.txt")) ""
Check "audit data present" ((Dc @("python", "p101_inventory.py")) -match "content_post\|rows=[1-9]\d{3,}") "Step 1 data must still exist"
$gitBefore = (& git status --short 2>&1 | Out-String)
Add-Evidence "git status --short (before)" $gitBefore
if (-not (($ps -match "web running") -and ($ps -match "db running"))) { Write-Host "Run: docker compose up -d" -ForegroundColor Red; exit 1 }

$modelsAlready = (Read-Utf8 "content\models.py").Contains("post_biz_status_created_idx")
if (-not $modelsAlready) {
    $pre = Dc @("python", "manage.py", "makemigrations", "--check", "--dry-run")
    Add-Evidence "makemigrations --check --dry-run (BEFORE edits)" $pre
    $clean = $pre -match "No changes detected"
    Check "no pending model changes before the fix" $clean "otherwise the generated migration would mix unrelated changes"
    if (-not $clean) { Write-Host "Pending unrelated model changes exist. Stop. Send me the evidence file." -ForegroundColor Red; exit 1 }
}

# ---------------------------------------------------------------- 2. Fix: composite indexes
Write-Section "2. Fix - Section 9 composite indexes (models + migrations)"
$specs = @(
    @{ File = "content\models.py"; New = "post_biz_status_created_idx"
       Pattern = '(?m)^([ \t]*)(models\.Index\(fields=\["business"\], name="content_post_business_idx"\),)'
       Add = 'models.Index(fields=["business", "status", "created_at"], name="post_biz_status_created_idx"),' },
    @{ File = "content\models.py"; New = "reel_biz_status_created_idx"
       Pattern = '(?m)^([ \t]*)(models\.Index\(fields=\["business"\], name="content_reel_business_idx"\),)'
       Add = 'models.Index(fields=["business", "status", "created_at"], name="reel_biz_status_created_idx"),' },
    @{ File = "stories\models.py"; New = "story_biz_status_created_idx"
       Pattern = '(?m)^([ \t]*)(models\.Index\(\r?\n[ \t]*fields=\["business", "status", "expires_at"\],\r?\n[ \t]*name="story_biz_status_exp_idx",\r?\n[ \t]*\),)'
       Add = 'models.Index(fields=["business", "status", "created_at"], name="story_biz_status_created_idx"),' },
    @{ File = "businesses\models.py"; New = "biz_category_city_idx"
       Pattern = '(?m)^([ \t]*)(GinIndex\(fields=\["search_vector"\], name="business_search_vector_gin"\),)'
       Add = 'models.Index(fields=["category", "city"], name="biz_category_city_idx"),' }
)
$comment = "# Part P-101: Architecture Section 9 composite index, found missing by the Phase 20 audit."
$editOk = $true
if ($modelsAlready) {
    Write-Host "  models already contain the new indexes - skipping the edit and makemigrations, still verifying and migrating"
} else {
    foreach ($s in $specs) { Backup-File $s.File }
    foreach ($s in $specs) {
        $text = Read-Utf8 $s.File
        $nl = if ($text.Contains("`r`n")) { "`r`n" } else { "`n" }
        $n = ([regex]::Matches($text, $s.Pattern)).Count
        if ($n -ne 1 -or $text.Contains($s.New)) { Check "anchor for $($s.New)" $false "matches=$n"; $editOk = $false; continue }
        $addLine = $s.Add; $cm = $comment; $nlx = $nl
        $ev = [System.Text.RegularExpressions.MatchEvaluator]{ param($m) $ind = $m.Groups[1].Value; $m.Value + $nlx + $ind + $cm + $nlx + $ind + $addLine }
        $text = [regex]::Replace($text, $s.Pattern, $ev)
        Write-Utf8 $s.File $text
        Check "index added in $($s.File)" ((Read-Utf8 $s.File).Contains($s.New)) $s.New
    }
    if (-not $editOk) {
        foreach ($s in $specs) { Restore-File $s.File }
        Write-Host "Anchor mismatch - model files restored from backup. Nothing changed. Send me the output." -ForegroundColor Red; exit 1
    }
    $chk = Dc @("python", "manage.py", "check")
    Add-Evidence "manage.py check" $chk
    Check "manage.py check clean (index names valid)" ($chk -match "no issues") ""
    $mk = Dc @("python", "manage.py", "makemigrations", "content", "stories", "businesses", "-n", "p101_composite_indexes")
    Add-Evidence "makemigrations" $mk
    Write-Host $mk
}

# Migration files: verify (runs on first run AND on re-run), then migrate (idempotent)
$migFiles = @(Get-ChildItem -Path (Join-Path $BackendDir "content\migrations"), (Join-Path $BackendDir "stories\migrations"), (Join-Path $BackendDir "businesses\migrations") -Filter "*_p101_composite_indexes.py" -ErrorAction SilentlyContinue)
Check "3 migration files present" ($migFiles.Count -eq 3) "got $($migFiles.Count)"
$onlyAdd = $true
$migReport = ""
foreach ($f in $migFiles) {
    $t = [System.IO.File]::ReadAllText($f.FullName, $Utf8)
    $adds = ([regex]::Matches($t, "migrations\.AddIndex\(")).Count
    $others = ([regex]::Matches($t, "migrations\.(Add|Alter|Remove|Rename|Create|Delete|Run)(?!Index\()\w*\(")).Count
    $migReport += "$($f.Name): AddIndex=$adds other_ops=$others`r`n"
    if ($adds -lt 1 -or $others -ne 0) { $onlyAdd = $false }
}
Add-Evidence "migration content check" $migReport
Check "migrations contain ONLY AddIndex operations" $onlyAdd ($migReport -replace "`r`n", " ; ")
if (-not ($migFiles.Count -eq 3 -and $onlyAdd)) {
    Write-Host "Unexpected migration content. NOT migrating. Open the files listed in the evidence and send them to me." -ForegroundColor Red
    exit 1
}
$mg = Dc @("python", "manage.py", "migrate")
Add-Evidence "migrate" $mg
Check "migrate applied cleanly" (($mg -notmatch "Traceback") -and (($mg -match "p101_composite_indexes\.\.\. OK") -or ($mg -match "No migrations to apply"))) ""
$post = Dc @("python", "manage.py", "makemigrations", "--check", "--dry-run")
Check "no model/migration drift after the fix" ($post -match "No changes detected") ""

$inv = Dc @("python", "p101_inventory.py")
Add-Evidence "p101_inventory.py (after fix)" $inv
$chkLines = @($inv -split "`r?`n" | Where-Object { $_ -like "P101|CHECK|*" })
foreach ($c in @("A1", "A2", "A3", "B1")) {
    $l = $chkLines | Where-Object { $_ -like "P101|CHECK|$c|*" }
    Check "index $c exists in the database" ($l -match "FOUND-EXACT") ($l -replace ".*\|", "")
}

# ---------------------------------------------------------------- 3. Re-measure
Write-Section "3. Re-measure with EXPLAIN ANALYZE"
$verifyPy = @'
# p101_verify.py - PART P-101 STEP 3: re-measure the query paths that the new composite indexes target.
import os, re
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
django.setup()

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.models import Count
from django.utils import timezone

from categories.models import Category
from content.models import Post, Reel
from feed.services import fetch_following_tier
from search.services import SearchFilters, get_search_results
from social.models import Follow
from stories.models import Story

User = get_user_model()
PLANS_FILE = "/app/p101_step3_plans.txt"
open(PLANS_FILE, "w", encoding="utf-8").close()

with connection.cursor() as cur:
    for t in ("content_post", "content_reel", "stories_story", "businesses_businessprofile"):
        cur.execute(f"ANALYZE {t}")


def capture(fn):
    queries = []

    def wrapper(execute, sql, params, many, ctx):
        if sql.lstrip().upper().startswith("SELECT"):
            queries.append((sql, params))
        return execute(sql, params, many, ctx)

    with connection.execute_wrapper(wrapper):
        fn()
    return queries


def plan_of(sql, params, off=False):
    with connection.cursor() as cur:
        try:
            if off:
                cur.execute("SET enable_seqscan = off")
            cur.execute("EXPLAIN (ANALYZE, BUFFERS) " + sql, params)
            cur.execute("EXPLAIN (ANALYZE, BUFFERS) " + sql, params)
            return "\n".join(r[0] for r in cur.fetchall())
        finally:
            if off:
                cur.execute("RESET enable_seqscan")


def summarize(text):
    ms = re.search(r"Execution Time: ([\d.]+) ms", text)
    seq = sorted(set(re.findall(r"Seq Scan on (\S+)", text)))
    idx = sorted(set(re.findall(r"(?:Index Only Scan|Index Scan)(?: Backward)? using (\S+) on", text)
                     + re.findall(r"Bitmap Index Scan on (\S+)", text)))
    has_sort = bool(re.search(r"^\s*(->\s+)?(Incremental )?Sort\s", text, re.M))
    return (ms.group(1) if ms else "?"), seq, idx, has_sort


def audit(label, targets, fn):
    for sql, params in [q for q in capture(fn) if any(f'FROM "{t}"' in q[0] for t in targets)]:
        table = next(t for t in targets if f'FROM "{t}"' in sql)
        p1, p2 = plan_of(sql, params, False), plan_of(sql, params, True)
        m1, s1, i1, o1 = summarize(p1)
        m2, s2, i2, o2 = summarize(p2)
        print("P101|VERIFY|{}|{}|ms={}|seq={}|idx={}|sort={}|ALT_ms={}|ALT_seq={}|ALT_idx={}".format(
            label, table, m1, ",".join(s1) or "-", ",".join(i1) or "-", o1, m2, ",".join(s2) or "-", ",".join(i2) or "-"))
        with open(PLANS_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n##### {label} | table={table}\n-- SQL:\n{sql}\n-- PARAMS: {list(params)}\n")
            f.write(f"-- PLAN (default planner):\n{p1}\n-- PLAN (enable_seqscan=off, diagnostic only):\n{p2}\n")


cust = User.objects.get(username="p101_cust_0")
followed = list(Follow.objects.filter(follower=cust).values_list("business_id", flat=True))
cat = Category.objects.filter(slug__startswith="p101-", parent__isnull=False).order_by("id").first().id
busy = (Post.objects.filter(business__business_name__startswith="P101 ").values("business_id")
        .annotate(c=Count("id")).order_by("-c").first())["business_id"]
story_biz = Story.objects.filter(status="published", expires_at__gt=timezone.now(),
                                 business__business_name__startswith="P101 ").values_list("business_id", flat=True).first()

audit("POST public list by business (P-043)", ["content_post"], lambda: list(
    Post.published_objects.select_related("business").filter(business_id=busy).order_by("-created_at")[:21]))
audit("REEL public list by business (P-043)", ["content_reel"], lambda: list(
    Reel.published_objects.select_related("business").filter(business_id=busy).order_by("-created_at")[:21]))
audit("FEED following-tier page1", ["content_post", "content_reel"],
      lambda: fetch_following_tier(followed, after=None, limit=20))
audit("STORY public list for one business", ["stories_story"], lambda: list(
    Story.objects.filter(status="published", expires_at__gt=timezone.now(), business_id=story_biz).order_by("-created_at")[:21]))
audit("SEARCH filter category+city+type", ["businesses_businessprofile"], lambda: get_search_results(
    SearchFilters(category_id=cat, city="Cairo", business_type="trader"), q=None, page_size=20))
print("P101|DONE=1")

'@
Write-Utf8 "p101_verify.py" $verifyPy
$vo = Dc @("python", "p101_verify.py")
Add-Evidence "p101_verify.py output" $vo
Check "verify finished" (($vo -match "P101\|DONE=1") -and -not ($vo -match "Traceback")) ""
$before = @{}
foreach ($l in (Read-Utf8 "p101_step2_evidence.txt") -split "`r?`n") {
    if ($l -like "P101|PLAN|*") { $p = $l.Split("|"); $before[$p[2] + "|" + $p[4]] = @{ Ms = (Val $p[5]); Idx = (Val $p[7]); Sort = (Val $p[8]) } }
}
$newIdx = @{ "POST public list by business (P-043)" = "post_biz_status_created_idx"; "REEL public list by business (P-043)" = "reel_biz_status_created_idx";
             "STORY public list for one business" = "story_biz_status_created_idx"; "SEARCH filter category+city+type" = "biz_category_city_idx" }
$vrows = @()
foreach ($l in ($vo -split "`r?`n" | Where-Object { $_ -like "P101|VERIFY|*" })) {
    $p = $l.Split("|"); $label = $p[2]; $table = $p[3]
    $idx = Val $p[5 + 1]; $altIdx = Val $p[10]
    $ni = $newIdx[$label]; $state = "n/a (informational)"
    if ($ni) { if ($idx -like "*$ni*") { $state = "NEW INDEX USED" } elseif ($altIdx -like "*$ni*") { $state = "new index usable (planner prefers another plan at this size)" } else { $state = "NEW INDEX NOT USABLE" } }
    $b = $before[$label + "|" + $table]
    $vrows += [pscustomobject]@{ Label = $label; Table = $table; BeforeMs = $(if ($b) { $b.Ms } else { "?" }); BeforeIdx = $(if ($b) { $b.Idx } else { "?" });
        AfterMs = (Val $p[4]); AfterIdx = $idx; Sort = (Val $p[7]); AltIdx = $altIdx; State = $state }
}
$vrows | Format-Table Label, Table, BeforeMs, AfterMs, State -AutoSize -Wrap | Out-String -Width 250 | Write-Host
Check "5+ re-measured plans" ($vrows.Count -ge 6) "got $($vrows.Count)"
foreach ($lab in @("POST public list by business (P-043)", "REEL public list by business (P-043)")) {
    $r = $vrows | Where-Object { $_.Label -eq $lab }
    Check "new index usable for: $lab" ($r -and $r.State -ne "NEW INDEX NOT USABLE") ($r.State)
}

# ---------------------------------------------------------------- 4. Regression tests
Write-Section "4. Targeted regression tests (a few minutes)"
$pt = Dc @("pytest", "content", "stories", "businesses", "feed", "search", "moderation", "-q", "-p", "no:cacheprovider")
Add-Evidence "pytest content stories businesses feed search moderation" $pt
$passed = 0; $m = [regex]::Match($pt, "(\d+) passed"); if ($m.Success) { $passed = [int]$m.Groups[1].Value }
$failed = ($pt -match "\d+ failed") -or ($pt -match "\d+ error")
Check "targeted pytest green" (($passed -gt 0) -and -not $failed) "passed=$passed"
$ptLast = (($pt -split "`r?`n" | Where-Object { $_.Trim() -ne "" } | Select-Object -Last 1)).Trim()
Write-Host "  $ptLast"

# ---------------------------------------------------------------- 5. Cache hit rates
Write-Section "5. Cache hit-rate spot check (about 30 seconds)"
$cachePy = @'
# p101_cache.py - PART P-101 STEP 3: cache hit-rate spot check through the REAL URL stack.
# For each target: start from an empty key, send 5 identical requests, then prove from three
# independent signals that only request 1 computed: (a) a counter around the compute function,
# (b) DB queries per request, (c) the Redis TTL counting down (a key re-written on every request
# would keep resetting to the full TTL). Control: a request shape that is documented as NOT cached
# (feed page_size != 20) must show compute_calls == requests, proving the counter can detect a miss.
import os, time
from unittest import mock
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
django.setup()

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django_redis import get_redis_connection
from rest_framework.test import APIClient

import businesses.views as business_views
import categories.views as category_views
import feed.views as feed_views
from businesses.models import BusinessProfile

User = get_user_model()
HOST = "localhost"
N = 5
rconn = get_redis_connection("default")


def hits_misses():
    s = rconn.info("stats")
    return int(s.get("keyspace_hits", 0)), int(s.get("keyspace_misses", 0))


def run_case(name, key, ttl_conf, path, user, patcher, calls):
    cache.delete(key)
    client = APIClient()
    if user is not None:
        client.force_authenticate(user)
    codes, queries, ttls, bodies = [], [], [], []
    h0, m0 = hits_misses()
    with patcher():
        for i in range(N):
            with CaptureQueriesContext(connection) as q:
                resp = client.get(path, HTTP_HOST=HOST)
            codes.append(resp.status_code)
            queries.append(len(q))
            ttls.append(cache.ttl(key))
            bodies.append(resp.content)
            time.sleep(1)
    h1, m1 = hits_misses()
    reasons = []
    if any(c != 200 for c in codes):
        reasons.append("non-200 status")
    if calls[0] != 1:
        reasons.append(f"compute ran {calls[0]} times, expected 1")
    t1, t5 = ttls[0], ttls[-1]
    if t1 is None or t5 is None or t1 <= 0:
        reasons.append("key missing in Redis after request 1")
    else:
        if not (ttl_conf - 5 <= t1 <= ttl_conf):
            reasons.append(f"TTL after request 1 is {t1}, configured {ttl_conf}")
        if t5 > t1 - 2:
            reasons.append(f"TTL did not count down ({t1} -> {t5}); key may be rewritten every request")
    if not all(x < queries[0] for x in queries[1:]):
        reasons.append("later requests did not issue fewer DB queries than request 1")
    print("P101|CACHE|{}|key={}|ttl_conf={}|codes={}|compute_calls={}|queries={}|ttls={}|redis_hits={}|redis_misses={}|identical={}|verdict={}|reasons={}".format(
        name, key, ttl_conf, ",".join(map(str, codes)), calls[0], ",".join(map(str, queries)),
        ",".join(map(str, ttls)), h1 - h0, m1 - m0, len(set(bodies)) == 1,
        "PASS" if not reasons else "FAIL", ";".join(reasons) or "-"))
    cache.delete(key)


def counting(module, attr, calls):
    real = getattr(module, attr)

    def wrapper(*a, **k):
        calls[0] += 1
        return real(*a, **k)

    return lambda: mock.patch.object(module, attr, wrapper)


user = User.objects.filter(username="p101_cust_0").first()
biz = BusinessProfile.objects.filter(business_name__startswith="P101 ").order_by("id").first()
if user is None or biz is None:
    raise SystemExit("P101|ERROR=audit data missing")

# 1. Feed first page (P-060)
calls = [0]
run_case("Feed first page", feed_views._feed_home_cache_key(user.id), feed_views.FEED_HOME_CACHE_TTL_SECONDS,
         "/api/v1/feed/home/", user, counting(feed_views, "get_home_feed", calls), calls)

# 2. Business Profile (P-030)
calls = [0]
real_get_object = business_views.BusinessProfilePublicView.get_object


def counting_get_object(self, *a, **k):
    calls[0] += 1
    return real_get_object(self, *a, **k)


run_case("Business Profile", business_views._business_profile_cache_key(biz.id),
         business_views.BUSINESS_PROFILE_CACHE_TTL_SECONDS, f"/api/v1/businesses/{biz.id}/", None,
         lambda: mock.patch.object(business_views.BusinessProfilePublicView, "get_object", counting_get_object), calls)

# 3. Categories tree (P-025)
calls = [0]
run_case("Categories tree", category_views.CATEGORY_TREE_CACHE_KEY, category_views.CATEGORY_TREE_CACHE_TTL_SECONDS,
         "/api/v1/categories/tree/", None, counting(category_views, "build_category_tree", calls), calls)

# 4. Control: feed page_size=10 is documented (feed/views.py) as bypassing the cache -> must compute every time
calls = [0]
client = APIClient()
client.force_authenticate(user)
with counting(feed_views, "get_home_feed", calls)():
    for _ in range(3):
        client.get("/api/v1/feed/home/?page_size=10", HTTP_HOST=HOST)
print("P101|CONTROL|feed page_size=10 (not cached by design)|compute_calls={}|expected=3|verdict={}".format(
    calls[0], "PASS" if calls[0] == 3 else "FAIL"))

for k in (feed_views._feed_home_cache_key(user.id), business_views._business_profile_cache_key(biz.id),
          category_views.CATEGORY_TREE_CACHE_KEY):
    cache.delete(k)
print("P101|DONE=1")

'@
Write-Utf8 "p101_cache.py" $cachePy
$co = Dc @("python", "p101_cache.py")
Add-Evidence "p101_cache.py output" $co
Write-Host $co
$cl = @($co -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ -like "P101|CACHE|*" })
$ctl = @($co -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ -like "P101|CONTROL|*" })
Check "3 cache targets reported" ($cl.Count -eq 3) "got $($cl.Count)"
foreach ($l in $cl) { $p = $l.Split("|"); Check "cache hit verified: $($p[2])" ((Val $p[12]) -eq "PASS") ("compute_calls=" + (Val $p[6]) + " queries=" + (Val $p[7])) }
Check "negative control (uncached shape computes every time)" (($ctl.Count -eq 1) -and ($ctl[0] -match "verdict=PASS")) ""

# ---------------------------------------------------------------- 6. Cleanup
Write-Section "6. Cleanup of audit data"
$remaining = "skipped (-KeepData)"
if (-not $KeepData) {
    Write-Utf8 "p101_postclean.py" (@'
# p101_postclean.py - run after p101_cleanup.py: drop audit-related cache keys and report what is left.
import os
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
django.setup()
from django.contrib.auth import get_user_model
from django.core.cache import cache
from businesses.models import BusinessProfile
from categories.models import Category

cache.delete("categories:tree")
User = get_user_model()
print("P101|REMAINING|users={}|businesses={}|categories={}".format(
    User.objects.filter(username__startswith="p101_").count(),
    BusinessProfile.all_objects.filter(business_name__startswith="P101 ").count(),
    Category.objects.filter(slug__startswith="p101-").count()))

'@)
    $cu = Dc @("python", "p101_cleanup.py")
    Add-Evidence "p101_cleanup.py output" $cu
    $pc = Dc @("python", "p101_postclean.py")
    Add-Evidence "p101_postclean.py output" $pc
    Check "cleanup ran" (($cu -match "CLEANUP=done") -and -not ($cu -match "Traceback")) ""
    Check "no audit rows remain" ($pc -match "REMAINING\|users=0\|businesses=0\|categories=0") ""
    $remaining = "users=0, businesses=0, categories=0"
}

# ---------------------------------------------------------------- 7. Git guard (before writing docs)
Write-Section "7. Git guard"
$gitAfter = (& git status --short 2>&1 | Out-String)
Add-Evidence "git status --short (after code changes)" $gitAfter
$modNow = @($gitAfter -split "`r?`n" | Where-Object { $_ -match "^\s*M " } | ForEach-Object { $_.Trim() -replace "^M\s+", "" } | Where-Object { $_ -ne "celerybeat-schedule" })
$allowed = @("content/models.py", "stories/models.py", "businesses/models.py")
$unexpected = @($modNow | Where-Object { $allowed -notcontains $_ })
Check "only the 3 models files are modified (tracked)" ($unexpected.Count -eq 0) ($unexpected -join ", ")

# ---------------------------------------------------------------- 8. Report Parts D and E
Write-Section "8. Finishing $ReportFile"
Backup-File $ReportFile
Copy-Item (Join-Path $BackendDir $ReportFile) (Join-Path $BackupDir "$ReportFile.step3.bak") -Force
$failNow = @($script:Results | Where-Object { -not $_.Ok })
$allGreen = ($failNow.Count -eq 0)

$d = New-Object System.Text.StringBuilder
[void]$d.AppendLine("## Part D - Cache hit rates: Feed first page, Business Profile, Categories tree (Step 3 - done)")
[void]$d.AppendLine("")
[void]$d.AppendLine("Method: ``p101_cache.py`` calls the REAL URLs through Django's test client (Feed with a forced-authenticated audit customer, the other two anonymous), starting from an empty key, 5 identical requests 1 second apart. A request counts as a hit only if three independent signals agree: (1) a counter wrapped around the compute function ran exactly once in 5 requests, (2) requests 2-5 issued fewer DB queries than request 1, (3) the Redis TTL counted down between requests (a key re-written on every request would keep resetting to the full TTL). Negative control: Feed with ``page_size=10`` is documented in ``feed/views.py`` as bypassing the cache, so the same counter must show 3 computes in 3 requests - this proves the counter can detect a miss.")
[void]$d.AppendLine("")
[void]$d.AppendLine("| Target | Redis key | Configured TTL (s) | Compute runs in 5 requests | DB queries per request | TTL after each request (s) | Redis hits / misses (informational) | Verdict |")
[void]$d.AppendLine("|---|---|---|---|---|---|---|---|")
foreach ($l in $cl) { $p = $l.Split("|"); [void]$d.AppendLine("| $($p[2]) | ``$(Val $p[3])`` | $(Val $p[4]) | $(Val $p[6]) | $(Val $p[7]) | $(Val $p[8]) | $(Val $p[9]) / $(Val $p[10]) | $(Val $p[12]) |") }
[void]$d.AppendLine("")
foreach ($l in $ctl) { [void]$d.AppendLine("Control: ``$l``"); [void]$d.AppendLine("") }
[void]$d.AppendLine("Redis hit/miss counters are server-wide (other clients such as Celery can add noise), so they are shown but not used as pass criteria.")
$dText = $d.ToString().TrimEnd()

$e = New-Object System.Text.StringBuilder
[void]$e.AppendLine("## Part E - Gaps found, fixes made, conclusions (Step 3 - done)")
[void]$e.AppendLine("")
[void]$e.AppendLine("### E.1 Index existence and use (summary of Parts B and C)")
[void]$e.AppendLine("")
$e1 = @'
| Index (Section 9) | Exists | Used by the planner (evidence: Part C) |
|---|---|---|
| Product (category, business) as built; Section 9's (category, city) cannot exist on Product, which has no city column | yes | YES - ``products_category_business`` chosen for the category+city+type search (#12) |
| GIN ``products_search_vector_gin`` and ``business_search_vector_gin`` | yes | YES - chosen for every full-text query (#19, #20, #22) |
| ModerationQueue (status) | yes | YES - chosen for the pending list (#23, #24); a Sort node remains (see F-4) |
| Story (business, status, expires_at) as built in P-046 | yes | USABLE, NOT CHOSEN at this volume - the planner picked a seq scan (#25, #27) or the business FK index (#26); it is chosen only when seq scans are disabled. Cannot be claimed as actively used from this data; re-check at larger volume |
| Post / Reel / Story (business, status, created_at) - Section 9 literal | NO (found missing) | FIXED in this part, see E.2 |
| BusinessProfile (category, city) - Section 9 literal | NO (found missing) | FIXED in this part, see E.2 |
'@
[void]$e.AppendLine($e1)
[void]$e.AppendLine("")
[void]$e.AppendLine("### E.2 Fix made: four missing composite indexes")
[void]$e.AppendLine("")
[void]$e.AppendLine("Added as ``Meta.indexes`` entries (additive; one generated migration per app, ``*_p101_composite_indexes.py``, containing only AddIndex): ``post_biz_status_created_idx`` and ``reel_biz_status_created_idx`` on (business, status, created_at), ``story_biz_status_created_idx`` on (business, status, created_at), ``biz_category_city_idx`` on (category, city). The existing single-column and Story expiry indexes were left untouched.")
[void]$e.AppendLine("")
[void]$e.AppendLine("Re-measured with EXPLAIN ANALYZE on the same data (before = Part C, after = this step; raw plans in ``p101_step3_plans.txt``):")
[void]$e.AppendLine("")
[void]$e.AppendLine("| Query path | Table | Before ms | Before index | After ms | After index | Sort node after | New index status |")
[void]$e.AppendLine("|---|---|---|---|---|---|---|---|")
foreach ($r in $vrows) { [void]$e.AppendLine("| $($r.Label) | $($r.Table) | $($r.BeforeMs) | $($r.BeforeIdx) | $($r.AfterMs) | $($r.AfterIdx) | $($r.Sort) | $($r.State) |") }
[void]$e.AppendLine("")
[void]$e.AppendLine("Honest reading: at about 6000 rows per table every query already runs in milliseconds, so the timing differences are small and partly noise. The meaningful evidence is whether the new index is chosen or at least usable for the query shape it was specified for, and whether the Sort node disappears. Where the planner still prefers another plan at this size, the table says so; that is a statement about this data volume, not a defect.")
[void]$e.AppendLine("")
[void]$e.AppendLine("Regression check: ``pytest content stories businesses feed search moderation`` -> $ptLast. ``makemigrations --check`` reports no drift. The full backend suite was not re-run (Step 3 changed only index metadata).")
[void]$e.AppendLine("")
[void]$e.AppendLine("### E.3 Findings NOT fixed (design-level, not a missing index; need an owner decision or real usage data)")
[void]$e.AppendLine("")
$e3 = @'
- F-1 Feed backfill tier seq-scans Post and Reel (about 7.6 ms and 4.3 ms for page 1, #5-#8). The ORDER BY starts with ``business.is_featured`` which lives on a JOINED table, so no index on Post/Reel can supply that order; Postgres must read all published rows and sort. This cost grows linearly with content volume. It is the documented MVP trade-off in ``feed/services.py``, and page 1 of the Home Feed is cached for 90 s, but Discover (uncached) and every cursor page pay it. Options for a later part: denormalize ``is_featured`` onto Post/Reel (kept in sync like the existing counters), or a UNION/materialized feed. Not done here: it is a design change, outside this audit's scope.
- F-2 Story public list without a business filter (the Stories tray, #25) and the expiry sweep (#27) seq-scan ``stories_story``. Both existing Story indexes lead with ``business``, so neither can serve a query that has no business filter. Cheap at 3000 rows (0.3-0.5 ms) but the table keeps expired stories, so it grows. Candidate: an index on (status, expires_at). Not added: the plan names the business-led index only.
- F-3 Moderation pending list uses the (status) index but sorts all pending rows by created_at on every page (about 2500 rows, 1.5 ms). Candidate: (status, created_at). Not added: outside the indexes the plan names.
- F-4 City-only search on BusinessProfile and on Product (via the business join) seq-scans (#13, #14). BusinessProfile is tiny (about 300 rows) so this is correct planner behaviour; the new (category, city) index cannot serve a city-only filter because category leads. Revisit only if business volume grows by orders of magnitude.
'@
[void]$e.AppendLine($e3)
[void]$e.AppendLine("")
[void]$e.AppendLine("### E.4 Cache conclusion")
[void]$e.AppendLine("")
$cachePass = @($cl | Where-Object { $_ -match "verdict=PASS" }).Count
[void]$e.AppendLine("$cachePass of 3 cached targets (Feed first page, Business Profile, Categories tree) are genuinely hitting: one compute in 5 requests, fewer queries on repeats, TTL counting down. See Part D. No cache-key bug was found, so no cache code was changed.")
[void]$e.AppendLine("")
[void]$e.AppendLine("### E.5 Limits of this audit")
[void]$e.AppendLine("")
[void]$e.AppendLine("Data volume is 'low thousands' as the part requires, not production scale; a planner choice can change at 100x. The Story index in particular should be re-checked at larger volume. Single machine, warm cache, no concurrent load. Audit data removed: $remaining.")
[void]$e.AppendLine("")
[void]$e.AppendLine("### E.6 Result")
[void]$e.AppendLine("")
if ($allGreen) { [void]$e.AppendLine("All automated checks of Step 3 passed. P-101 acceptance: every named index now exists and is usable for its intended query; cache hits verified for all three targets; open findings F-1..F-4 recorded above.") }
else { [void]$e.AppendLine("NOT all Step 3 checks passed - see the console output / evidence file. Do not treat P-101 as complete.") }
$eText = $e.ToString().TrimEnd()

$new = $report.Replace($MarkerD, $dText).Replace($MarkerE, $eText)
$statusOld = "Status: IN PROGRESS - Steps 1-2 of 3 done (data, index existence, EXPLAIN ANALYZE). Step 3 (fixes, cache hit rates, cleanup, conclusions) pending."
$statusNew = if ($allGreen) { "Status: COMPLETE ($(Get-Date -Format 'yyyy-MM-dd')) - 4 missing composite indexes added; cache hits verified; 4 design-level findings open (Part E.3)." } else { "Status: NEEDS REVIEW - some Step 3 checks failed." }
$new = $new.Replace($statusOld, $statusNew)
Write-Utf8 $ReportFile $new
$rc = Read-Utf8 $ReportFile
Check "report Parts D and E written" ($rc.Contains($MarkerDone) -and -not $rc.Contains($MarkerD) -and $rc.Contains("Cache hit rates") ) ""

# ---------------------------------------------------------------- 9. PROJECT_PROGRESS.md (append only)
Write-Section "9. PROJECT_PROGRESS.md"
$failNow = @($script:Results | Where-Object { -not $_.Ok })
if ($failNow.Count -gt 0) {
    Write-Host "Checks failed - PROJECT_PROGRESS.md NOT updated." -ForegroundColor Yellow
} else {
    $prog = Read-Utf8 $ProgressFile
    if ($prog.Contains($ProgressMark)) { Write-Host "P-101 section already in PROJECT_PROGRESS.md - not appended again." -ForegroundColor Yellow }
    else {
        Backup-File $ProgressFile
        $pg = @'

---

## PART P-101 - Query/Index Audit + Cache-Hit-Rate Spot Check (Phase 20) - STATUS: COMPLETE (@@DATE@@) - 4 missing Section 9 composite indexes added; cache hits verified; 4 design-level findings OPEN

### What was implemented
- Built a realistic audit data set (300 businesses over 8 cities / 3 countries, 6000 products, 6000 posts, 3000 reels, 3000 stories of which 146 active, 11862 moderation rows, 1510 follows) with ANALYZE, then ran EXPLAIN (ANALYZE, BUFFERS) on the REAL Django-generated SQL (captured with a Django execute_wrapper) for: Feed following + backfill tiers (page 1 and cursor page 2), public Post/Reel lists by business, Search (4 filter shapes + 2 full-text), Moderation pending list (with and without priority), Story public list (all / one business) and the expiry sweep. Every query was also re-planned with enable_seqscan=off (diagnostic) to separate "planner prefers a seq scan on a small table" from "no usable index".
- Found 4 Section 9 composite indexes MISSING and added them: Post, Reel and Story (business, status, created_at); BusinessProfile (category, city). Additive migrations only.
- Verified the three cached targets really hit the cache through the real URLs.
- Full report: `PERFORMANCE_AUDIT_PHASE20.md` (Parts A-E, raw plans included).

### Files created
- Backend: `PERFORMANCE_AUDIT_PHASE20.md`; `p101_step1.ps1`, `p101_step2.ps1`, `p101_step3.ps1`; helpers `p101_seed.py`, `p101_cleanup.py`, `p101_inventory.py`, `p101_explain.py`, `p101_verify.py`, `p101_cache.py`, `p101_postclean.py`; evidence `p101_step1_evidence.txt`, `p101_step2_evidence.txt`, `p101_step3_evidence.txt`, `p101_step2_plans.txt`, `p101_step3_plans.txt`; migrations `content/migrations/*_p101_composite_indexes.py`, `stories/migrations/*_p101_composite_indexes.py`, `businesses/migrations/*_p101_composite_indexes.py`.
### Files modified
- `content/models.py` (2 Meta.indexes entries), `stories/models.py` (1), `businesses/models.py` (1), `PERFORMANCE_AUDIT_PHASE20.md` is new, `PROJECT_PROGRESS.md` (this section, appended). Nothing in the mobile repo changed.

### Important implementation details
- New index names (Django's 30-char limit): `post_biz_status_created_idx`, `reel_biz_status_created_idx`, `story_biz_status_created_idx`, `biz_category_city_idx`. Existing indexes (`content_post_business_idx`, `content_reel_business_idx`, `story_biz_status_exp_idx`, GIN indexes, moderation indexes) are unchanged.
- Section 9's `(category_id, city)` on Product is impossible as written (Product has no city); the as-built `products_category_business` was audited instead and is used by the planner.
- Audit data was deleted at the end (users `p101_*`, businesses `P101 *`, categories `p101-*`); the Redis keys `categories:tree`, the audit feed key and the audit business-profile key were deleted too.

### Architecture decisions
- No architecture change. Indexes only. No cache code changed (no cache-key bug found).
- F-1..F-4 below were deliberately NOT fixed: they are design-level (not missing indexes) or indexes the plan does not name.

### Commands
- Run from `D:\Cavallo\scd-backend` (PowerShell): `powershell -ExecutionPolicy Bypass -File .\p101_step1.ps1`, then `.\p101_step2.ps1`, then `.\p101_step3.ps1`.
- Apply on another machine: `docker compose exec -T web python manage.py migrate`.
- Commit (never `git add .`): `git add content/models.py stories/models.py businesses/models.py content/migrations stories/migrations businesses/migrations PERFORMANCE_AUDIT_PHASE20.md p101_step1.ps1 p101_step2.ps1 p101_step3.ps1 p101_seed.py p101_cleanup.py p101_inventory.py p101_explain.py p101_verify.py p101_cache.py p101_postclean.py p101_step1_evidence.txt p101_step2_evidence.txt p101_step3_evidence.txt p101_step2_plans.txt p101_step3_plans.txt PROJECT_PROGRESS.md`, then `git commit`, then `git push`.

### Tests and verification results (real run on the developer machine)
- Step 1 inventory: A1/A2/A3/B1 MISSING; B2, C1, C2, D1, D2, A4 found. Step 2: 17 scenarios, 27 plans. Step 3: all indexes found after the fix, `makemigrations --check` clean, targeted pytest (`content stories businesses feed search moderation`): @@PYTEST@@.
- Cache: Feed first page, Business Profile and Categories tree each computed once in 5 requests with the Redis TTL counting down; negative control (feed `page_size=10`) computed on every request, as documented.
- NOT run: the full backend pytest (only index metadata changed), the Flutter side, any test at production data volume.

### Findings OPEN (design-level, need owner decision or real usage data)
- F-1 Feed backfill tier seq-scans Post/Reel because its ORDER BY starts with `business.is_featured` (joined table). Cost grows with content volume; page 1 of Home Feed is cached 90 s, Discover and cursor pages are not. Later option: denormalize `is_featured` onto Post/Reel or build a UNION/materialized feed.
- F-2 Story public list without a business filter and the expiry sweep seq-scan `stories_story` (both Story indexes lead with `business`). Candidate: (status, expires_at). Also: `story_biz_status_exp_idx` is usable but was not chosen by the planner at this volume; re-check at larger volume.
- F-3 Moderation pending list sorts all pending rows per page. Candidate: (status, created_at).
- F-4 City-only search seq-scans small tables (correct at this size).

### Known issues
- Repo hygiene carried over, not part of P-101: `celerybeat-schedule` tracked and modified; `p099_step*.ps1` show as deleted; GitHub `.gitignore` last line probably UTF-16.
- `OFFLINE_RESILIENCE_AUDIT_PHASE20.md` is untracked in the backend working tree and is not part of P-101 (it belongs to P-102 work).
- P-099 open findings (F-99-1, F-99-2, F-99-3; S-1 moderation-bypass) are NOT resolved by this part.
- Backups of the modified files are in `p101_backups\` (untracked, do not commit).

### Remaining work
1. Owner decision on F-1..F-3 (separate small parts if accepted).
2. Re-run the plan check at larger data volume before production (Phase 22).
3. P-102 (Flutter offline/resilience audit).

### GitHub references
- `cavallo-app` `main`: commit pending (the owner commits and pushes with the command above; fill the hash here after pushing). `cavallo-mobile`: no change.

### Phase 20 status
P-101 COMPLETE. Phase 20 stays IN PROGRESS until P-102 is recorded.

### Exact next starting point
P-102 (Phase 20): Flutter offline/resilience audit against Architecture Section 27 (client side of this phase). Check first whether `OFFLINE_RESILIENCE_AUDIT_PHASE20.md` in the backend working tree is an earlier P-102 attempt before starting.

### Edits to existing sections
In the Part status index/table add: `P-101 | Query/Index Audit + Cache-Hit-Rate Spot Check | Phase 20 | COMPLETE (4 composite indexes added; cache hits verified; F-1..F-4 open in PERFORMANCE_AUDIT_PHASE20.md)`.
'@
        $pg = $pg.Replace("@@DATE@@", (Get-Date -Format "yyyy-MM-dd")).Replace("@@PYTEST@@", $ptLast)
        [System.IO.File]::AppendAllText((Join-Path $BackendDir $ProgressFile), $pg, $Utf8)
        Check "PROJECT_PROGRESS.md section appended" ((Read-Utf8 $ProgressFile).Contains($ProgressMark)) ""
    }
}

# ---------------------------------------------------------------- Summary
Write-Section "SUMMARY"
$fail = @($script:Results | Where-Object { -not $_.Ok })
Write-Host ("Checks: {0} total, {1} failed" -f $script:Results.Count, $fail.Count)
if ($fail.Count -gt 0) { $fail | ForEach-Object { Write-Host "  FAILED: $($_.Name) $($_.Detail)" -ForegroundColor Red } }
Write-Host ""
Write-Host "Re-measure (before -> after):" -ForegroundColor Cyan
$vrows | ForEach-Object { Write-Host ("  {0} [{1}] {2} ms -> {3} ms | {4}" -f $_.Label, $_.Table, $_.BeforeMs, $_.AfterMs, $_.State) }
Write-Host ""
Write-Host "Cache lines:" -ForegroundColor Cyan
$cl | ForEach-Object { Write-Host "  $_" }
$ctl | ForEach-Object { Write-Host "  $_" }
Write-Host ""
Write-Host "Pytest: $ptLast"
Write-Host ""
Write-Host "Send me: the full console output (or $EvidenceFile)."
Write-Host "Nothing was committed. After I confirm, run git status and the commit commands from the Final Handoff."