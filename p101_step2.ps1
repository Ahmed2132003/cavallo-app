# =====================================================================
# p101_step2.ps1  --  PART P-101, STEP 2 of 3
# Scope: EXPLAIN (ANALYZE, BUFFERS) on the REAL Django-generated SQL of
#   Feed (following + backfill tiers, page 1 and cursor page 2),
#   public Post/Reel lists by business, Search (filters + full-text),
#   Moderation pending list, Story visibility + expiry sweep.
#   Each query is also re-planned with enable_seqscan=off (diagnostic only,
#   session-scoped, reset afterwards) to tell "planner prefers a seq scan on
#   a small table" apart from "no usable index for this query shape".
#   Step 3 = fixes for confirmed gaps, cache hit rates, cleanup, final report,
#   PROJECT_PROGRESS.md.
#
# CREATES  : p101_explain.py, p101_step2_evidence.txt, p101_step2_plans.txt
# MODIFIES : PERFORMANCE_AUDIT_PHASE20.md (Part C only + Status line;
#            .step2.bak written first)
# DATABASE : read-only (SELECT / EXPLAIN only). CHANGES NO APPLICATION CODE.
#
# Run from: D:\Cavallo\scd-backend  (PowerShell, Docker stack up, Step 1 done)
#   powershell -ExecutionPolicy Bypass -File .\p101_step2.ps1
# Re-run guard: refuses if Part C is already filled, unless -Force
# (-Force restores the report from PERFORMANCE_AUDIT_PHASE20.md.step2.bak first).
# =====================================================================
param([switch]$Force)

$ErrorActionPreference = "Continue"
$BackendDir   = "D:\Cavallo\scd-backend"
$ReportFile   = "PERFORMANCE_AUDIT_PHASE20.md"
$EvidenceFile = "p101_step2_evidence.txt"
$PlansFile    = "p101_step2_plans.txt"
$MarkerOld    = "## Part C - EXPLAIN ANALYZE per query path (Step 2 - pending)"
$MarkerNew    = "## Part C - EXPLAIN ANALYZE per query path (Step 2 - done)"
Set-Location $BackendDir

$script:Results = @()
function Write-Section([string]$t) { Write-Host ""; Write-Host "=== $t ===" -ForegroundColor Cyan }
function Add-Evidence([string]$title, [string]$text) {
    $sep = "`r`n" + ("=" * 70) + "`r`n" + $title + "`r`n" + ("=" * 70) + "`r`n"
    [System.IO.File]::AppendAllText((Join-Path $BackendDir $EvidenceFile), $sep + $text + "`r`n")
}
function Write-Utf8([string]$Path, [string]$Content) {
    [System.IO.File]::WriteAllText((Join-Path $BackendDir $Path), $Content, (New-Object System.Text.UTF8Encoding($false)))
}
function Read-Utf8([string]$Path) {
    return [System.IO.File]::ReadAllText((Join-Path $BackendDir $Path), (New-Object System.Text.UTF8Encoding($false)))
}
function Check([string]$name, [bool]$ok, [string]$detail) {
    $script:Results += [pscustomobject]@{ Name = $name; Ok = $ok; Detail = $detail }
    if ($ok) { Write-Host "  PASS  $name  $detail" -ForegroundColor Green }
    else     { Write-Host "  FAIL  $name  $detail" -ForegroundColor Red }
}
function Val([string]$s) { return ($s -replace '^[^=]+=', '') }

# ---------------------------------------------------------------- 1. Preflight
Write-Section "1. Preflight"
if (-not (Test-Path (Join-Path $BackendDir $ReportFile))) {
    Write-Host "Report not found. Run p101_step1.ps1 first." -ForegroundColor Red; exit 1
}
$bak = Join-Path $BackendDir "$ReportFile.step2.bak"
if ($Force -and (Test-Path $bak)) { Copy-Item $bak (Join-Path $BackendDir $ReportFile) -Force }
$report = Read-Utf8 $ReportFile
if ($report.Contains($MarkerNew) -and -not $Force) {
    Write-Host "Part C already filled. Use -Force to redo." -ForegroundColor Yellow; exit 1
}
if (-not $report.Contains($MarkerOld)) {
    Write-Host "Part C placeholder not found in the report. Stop." -ForegroundColor Red; exit 1
}
if (Test-Path (Join-Path $BackendDir $EvidenceFile)) { Remove-Item (Join-Path $BackendDir $EvidenceFile) -Force }
$ps = (& docker compose ps --format "{{.Service}} {{.State}}" 2>&1 | Out-String)
Check "web and db running" (($ps -match "web running") -and ($ps -match "db running")) ""
$gitBefore = (& git status --short 2>&1 | Out-String)
Add-Evidence "git status --short (before)" $gitBefore
if (-not (($ps -match "web running") -and ($ps -match "db running"))) { Write-Host "Run: docker compose up -d" -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------- 2. Script + run
Write-Section "2. Creating and running p101_explain.py (about 1 minute)"
$explainPy = @'
# p101_explain.py - PART P-101 STEP 2: EXPLAIN (ANALYZE, BUFFERS) on the REAL Django-generated SQL.
# Read-only (SELECT only). For every query it also re-plans with enable_seqscan=off, to separate
# "planner correctly prefers a seq scan on a small table" from "the index cannot serve this query".
import os, re
from datetime import timedelta
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
django.setup()

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.models import Count
from django.utils import timezone

from businesses.models import BusinessProfile
from categories.models import Category
from content.models import Post, Reel
from feed.cursor import PHASE_BACKFILL, PHASE_FOLLOWING
from feed.services import fetch_backfill_tier, fetch_following_tier
from moderation.models import ModerationQueue
from search.services import SearchFilters, get_search_results
from social.models import Follow
from stories.models import Story

User = get_user_model()
PLANS_FILE = "/app/p101_step2_plans.txt"
open(PLANS_FILE, "w", encoding="utf-8").close()
COUNTER = {"n": 0}


def capture(fn):
    queries = []

    def wrapper(execute, sql, params, many, ctx):
        if sql.lstrip().upper().startswith("SELECT"):
            queries.append((sql, params))
        return execute(sql, params, many, ctx)

    with connection.execute_wrapper(wrapper):
        result = fn()
    return result, queries


def plan_of(sql, params, seqscan_off=False):
    with connection.cursor() as cur:
        try:
            if seqscan_off:
                cur.execute("SET enable_seqscan = off")
            cur.execute("EXPLAIN (ANALYZE, BUFFERS) " + sql, params)  # warm-up run
            cur.execute("EXPLAIN (ANALYZE, BUFFERS) " + sql, params)  # recorded run
            return "\n".join(r[0] for r in cur.fetchall())
        finally:
            if seqscan_off:
                cur.execute("RESET enable_seqscan")


def summarize(text):
    ms = re.search(r"Execution Time: ([\d.]+) ms", text)
    seq = sorted(set(re.findall(r"Seq Scan on (\S+)", text)))
    idx = sorted(set(re.findall(r"(?:Index Only Scan|Index Scan)(?: Backward)? using (\S+) on", text)
                     + re.findall(r"Bitmap Index Scan on (\S+)", text)))
    has_sort = bool(re.search(r"^\s*(->\s+)?(Incremental )?Sort\s", text, re.M))
    return (ms.group(1) if ms else "?"), seq, idx, has_sort


def audit(label, targets, fn):
    result, queries = capture(fn)
    if isinstance(result, int):
        n_items = result
    elif isinstance(result, dict) and "items" in result:
        n_items = len(result["items"])
    else:
        n_items = len(result) if hasattr(result, "__len__") else -1
    print(f"P101|SCEN|{label}|items={n_items}|captured_selects={len(queries)}")
    kept = [q for q in queries if any(f'FROM "{t}"' in q[0] for t in targets)]
    for sql, params in kept:
        COUNTER["n"] += 1
        k = COUNTER["n"]
        table = next(t for t in targets if f'FROM "{t}"' in sql)
        p1 = plan_of(sql, params, False)
        p2 = plan_of(sql, params, True)
        ms1, seq1, idx1, sort1 = summarize(p1)
        ms2, seq2, idx2, sort2 = summarize(p2)
        print("P101|PLAN|{}|{}|{}|ms={}|seq={}|idx={}|sort={}|ALT_ms={}|ALT_seq={}|ALT_idx={}".format(
            label, k, table, ms1, ",".join(seq1) or "-", ",".join(idx1) or "-", sort1,
            ms2, ",".join(seq2) or "-", ",".join(idx2) or "-"))
        with open(PLANS_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n##### [{k}] {label} | table={table}\n-- SQL:\n{sql}\n-- PARAMS: {list(params)}\n")
            f.write(f"-- PLAN (default planner):\n{p1}\n-- PLAN (enable_seqscan=off, diagnostic only):\n{p2}\n")


# ------------------------------------------------------------------ fixtures
cust = User.objects.filter(username="p101_cust_0").first()
if cust is None:
    raise SystemExit("P101|ERROR=audit data missing, run Step 1 first")
followed = list(Follow.objects.filter(follower=cust).values_list("business_id", flat=True))
excluded = set(followed)
cats = list(Category.objects.filter(slug__startswith="p101-", parent__isnull=False).order_by("id"))
cat = cats[0].id
busy = (Post.objects.filter(business__business_name__startswith="P101 ").values("business_id")
        .annotate(c=Count("id")).order_by("-c").first())["business_id"]
now = timezone.now()
story_biz = Story.objects.filter(status="published", expires_at__gt=now, business__business_name__startswith="P101 ").values_list("business_id", flat=True).first()
print(f"P101|FIXTURE|customer=p101_cust_0|follows={len(followed)}|category_id={cat}|busy_business={busy}|story_business={story_biz}")

TP, TR = ["content_post", "content_reel"], ["content_post", "content_reel"]

# ------------------------------------------------------------------ FEED (P-059)
r = {}
audit("FEED following-tier page1", TP, lambda: r.setdefault("f1", fetch_following_tier(followed, after=None, limit=20)))
audit("FEED following-tier page2 (cursor)", TP, lambda: fetch_following_tier(
    followed, after=r["f1"][-1].to_cursor(PHASE_FOLLOWING), limit=20))
audit("FEED backfill-tier page1", TP, lambda: r.setdefault("b1", fetch_backfill_tier(excluded, after=None, limit=20)))
audit("FEED backfill-tier page2 (cursor)", TP, lambda: fetch_backfill_tier(
    excluded, after=r["b1"][-1].to_cursor(PHASE_BACKFILL), limit=20))

# ------------------------------------------------------------------ PUBLIC business-scoped lists (Section 9 composite target)
audit("POST public list by business (P-043)", ["content_post"],
      lambda: list(Post.published_objects.select_related("business").filter(business_id=busy).order_by("-created_at")[:21]))
audit("REEL public list by business (P-043)", ["content_reel"],
      lambda: list(Reel.published_objects.select_related("business").filter(business_id=busy).order_by("-created_at")[:21]))

# ------------------------------------------------------------------ SEARCH (P-063/P-064)
TS = ["products_product", "businesses_businessprofile"]
audit("SEARCH filter category+city+type", TS, lambda: get_search_results(
    SearchFilters(category_id=cat, city="Cairo", business_type="trader"), q=None, page_size=20))
audit("SEARCH filter city only", TS, lambda: get_search_results(SearchFilters(city="Cairo"), q=None, page_size=20))
audit("SEARCH filter category only", TS, lambda: get_search_results(SearchFilters(category_id=cat), q=None, page_size=20))
audit("SEARCH filter price+category", TS, lambda: get_search_results(
    SearchFilters(category_id=cat, min_price=100, max_price=1000), q=None, page_size=20))
audit("SEARCH full-text q", TS, lambda: get_search_results(SearchFilters(), q="leather shoes", page_size=20))
audit("SEARCH full-text q + category + city", TS, lambda: get_search_results(
    SearchFilters(category_id=cat, city="Cairo"), q="cotton shirt", page_size=20))

# ------------------------------------------------------------------ MODERATION queue (P-038)
TM = ["moderation_moderationqueue"]
audit("MOD pending list", TM, lambda: list(
    ModerationQueue.objects.filter(status=ModerationQueue.Status.PENDING).select_related("content_type").order_by("-created_at")[:21]))
audit("MOD pending list priority=fast_path", TM, lambda: list(
    ModerationQueue.objects.filter(status=ModerationQueue.Status.PENDING, priority="fast_path")
    .select_related("content_type").order_by("-created_at")[:21]))

# ------------------------------------------------------------------ STORY visibility (P-046/P-048)
TST = ["stories_story"]
audit("STORY public list (published, not expired)", TST, lambda: list(
    Story.objects.filter(status=Story.Status.PUBLISHED, expires_at__gt=timezone.now()).order_by("-created_at")[:21]))
audit("STORY public list for one business", TST, lambda: list(
    Story.objects.filter(status=Story.Status.PUBLISHED, expires_at__gt=timezone.now(), business_id=story_biz)
    .order_by("-created_at")[:21]))
audit("STORY expiry sweep (P-048)", TST, lambda: Story.objects.filter(
    expires_at__lte=timezone.now(), archived_at__isnull=True).count())
print("P101|DONE=1")

'@
Write-Utf8 "p101_explain.py" $explainPy
Check "p101_explain.py created" (Test-Path (Join-Path $BackendDir "p101_explain.py")) ""
$out = (& docker compose exec -T web python p101_explain.py 2>&1 | Out-String)
Add-Evidence "p101_explain.py output" $out
$lines = @($out -split "`r?`n" | ForEach-Object { $_.Trim() })
$scen  = @($lines | Where-Object { $_ -like "P101|SCEN|*" })
$plans = @($lines | Where-Object { $_ -like "P101|PLAN|*" })
$fix   = @($lines | Where-Object { $_ -like "P101|FIXTURE|*" })
$scen | ForEach-Object { Write-Host $_ }
Check "script finished (DONE marker)" ($out -match "P101\|DONE=1") ""
Check "no traceback in output" (-not ($out -match "Traceback")) ""
Check "17 scenarios reported" ($scen.Count -eq 17) "got $($scen.Count)"
Check "plans captured (>= 17)" ($plans.Count -ge 17) "got $($plans.Count)"
Check "plans file written" (Test-Path (Join-Path $BackendDir $PlansFile)) ""
$empty = @($scen | Where-Object { $_ -match "items=0\|" })
Check "every scenario returned rows" ($empty.Count -eq 0) ($empty -join " ; ")

# ---------------------------------------------------------------- 3. Parse + verdicts
Write-Section "3. Results"
$rows = @()
foreach ($l in $plans) {
    $p = $l.Split("|")
    $seq = Val $p[6]; $idx = Val $p[7]; $altIdx = Val $p[11]
    if ($idx -ne "-" -and $seq -eq "-") { $v = "INDEX USED" }
    elseif ($idx -ne "-" -and $seq -ne "-") { $v = "MIXED (index + seq scan)" }
    elseif ($altIdx -ne "-") { $v = "SEQ SCAN - index usable when forced" }
    else { $v = "SEQ SCAN - NO USABLE INDEX" }
    $rows += [pscustomobject]@{
        N = $p[3]; Label = $p[2]; Table = $p[4]; Ms = (Val $p[5]); Seq = $seq; Idx = $idx
        Sort = (Val $p[8]); AltMs = (Val $p[9]); AltIdx = $altIdx; Verdict = $v
    }
}
$rows | Format-Table N, Label, Table, Ms, Verdict -AutoSize -Wrap | Out-String -Width 250 | Write-Host

# ---------------------------------------------------------------- 4. Report (Part C)
Write-Section "4. Updating $ReportFile (Part C)"
Copy-Item (Join-Path $BackendDir $ReportFile) $bak -Force
$sb = New-Object System.Text.StringBuilder
[void]$sb.AppendLine($MarkerNew)
[void]$sb.AppendLine("")
[void]$sb.AppendLine("Method: ``p101_explain.py`` runs the real service/view querysets (feed/services.py, search/services.py, content public lists, moderation queue list, stories public list) with a Django ``execute_wrapper`` to capture the exact SQL, then ``EXPLAIN (ANALYZE, BUFFERS)`` on each (one warm-up run, one recorded run). Each is re-planned with ``SET enable_seqscan = off`` (diagnostic only, reset afterwards): if the planner then picks an index, the index is USABLE for that query shape and the seq scan was a cost-based choice; if it still cannot, the shape does not fit any index.")
[void]$sb.AppendLine("")
foreach ($f in $fix) { [void]$sb.AppendLine("Fixture: ``$f``"); [void]$sb.AppendLine("") }
[void]$sb.AppendLine("Verdict column is automatic (index names found in the plan). Human interpretation and fixes are in Part E (Step 3).")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("| # | Query path | Table | Exec ms | Indexes in plan | Seq scans | Sort node | Forced-index ms | Forced-index plan uses | Verdict |")
[void]$sb.AppendLine("|---|---|---|---|---|---|---|---|---|---|")
foreach ($r in $rows) {
    [void]$sb.AppendLine("| $($r.N) | $($r.Label) | $($r.Table) | $($r.Ms) | $($r.Idx) | $($r.Seq) | $($r.Sort) | $($r.AltMs) | $($r.AltIdx) | $($r.Verdict) |")
}
[void]$sb.AppendLine("")
[void]$sb.AppendLine("Raw plans (default planner and forced-index diagnostic) for every row above, copied from ``$PlansFile``:")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("<details><summary>Raw EXPLAIN (ANALYZE, BUFFERS) output</summary>")
[void]$sb.AppendLine("")
[void]$sb.AppendLine('```text')
[void]$sb.AppendLine((Read-Utf8 $PlansFile).TrimEnd())
[void]$sb.AppendLine('```')
[void]$sb.AppendLine("")
[void]$sb.AppendLine("</details>")
$new = $report.Replace($MarkerOld, $sb.ToString().TrimEnd())
$new = $new.Replace("Status: IN PROGRESS - Step 1 of 3 done (data + index existence). Step 2 (EXPLAIN ANALYZE) and Step 3 (cache hit rates, conclusions) pending.",
                    "Status: IN PROGRESS - Steps 1-2 of 3 done (data, index existence, EXPLAIN ANALYZE). Step 3 (fixes, cache hit rates, cleanup, conclusions) pending.")
Write-Utf8 $ReportFile $new
$check = Read-Utf8 $ReportFile
Check "Part C written" ($check.Contains($MarkerNew) -and -not $check.Contains($MarkerOld)) ""
Check "Part A, B, D, E still present" (($check.Contains("## Part A")) -and ($check.Contains("## Part B")) -and ($check.Contains("## Part D")) -and ($check.Contains("## Part E"))) ""

# ---------------------------------------------------------------- 5. Git guard
Write-Section "5. Git guard"
$gitAfter = (& git status --short 2>&1 | Out-String)
Add-Evidence "git status --short (after)" $gitAfter
$m1 = @($gitBefore -split "`r?`n" | Where-Object { $_ -match "^\s*M " -and $_ -notmatch "celerybeat-schedule" })
$m2 = @($gitAfter  -split "`r?`n" | Where-Object { $_ -match "^\s*M " -and $_ -notmatch "celerybeat-schedule" })
Check "no tracked file modified by this step" ($m1.Count -eq $m2.Count) ""

# ---------------------------------------------------------------- Summary
Write-Section "SUMMARY"
$fail = @($script:Results | Where-Object { -not $_.Ok })
Write-Host ("Checks: {0} total, {1} failed" -f $script:Results.Count, $fail.Count)
Write-Host ""
Write-Host "PLAN LINES (send me these):" -ForegroundColor Cyan
$plans | ForEach-Object { Write-Host $_ }
Write-Host ""
Write-Host "Files created: p101_explain.py $EvidenceFile $PlansFile ; modified: $ReportFile (backup $ReportFile.step2.bak)"
Write-Host "Send me: the full console output (or $EvidenceFile)."