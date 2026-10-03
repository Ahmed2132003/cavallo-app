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
