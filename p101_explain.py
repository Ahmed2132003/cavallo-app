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
