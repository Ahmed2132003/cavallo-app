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
