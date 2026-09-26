"""
Part P-059 — Home Feed API.

GET /api/v1/feed/home/?cursor=<opaque>&page_size=<int>

Authenticated only — there is no meaningful "home feed" without a user
to resolve follows/exclusions for. A malformed cursor or an
out-of-range page_size is a 400, not a 500: get_home_feed() raises
ValueError for both (InvalidCursorError included, since it subclasses
ValueError), and this view is the one place that catches it and turns
it into a DRF ValidationError.

Part P-060 — Redis caching of the first page (no ?cursor= supplied).
See FEED_HOME_CACHE_TTL_SECONDS / _feed_home_cache_key below for the
caching contract and its one documented deviation from the raw exec
spec (page_size scoping).
"""

from rest_framework.exceptions import ValidationError
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.cache import cache_get_or_set
from feed.serializers import FeedItemSerializer
from feed.services import get_discover_feed, get_home_feed

DEFAULT_PAGE_SIZE = 20

# Part P-060: 90s = منتصف الـ range اللي حدده architecture Section 16 (60-120s).
# مفيش invalidate-on-write هنا (بعكس P-030) لأن الـ staleness هنا مقبولة
# معماريًا بنص TTL، مش حاجة تحتاج تصحيح فوري زي بروفايل التاجر.
FEED_HOME_CACHE_TTL_SECONDS = 90


def _feed_home_cache_key(user_id):
    """Cache key convention لصفحة 1 من الـ Home Feed الخاصة بيوزر معين.

    بيتبع نفس المثال المكتوب في core/cache.py's docstring ("feed:42:page1")
    مش الصيغة "feed:home:{user_id}:page1" المكتوبة في الـ spec الخام —
    الاتنين ماتنفذوش في كود قبل كده فمفيش تعارض حقيقي، بس اخترنا نتبع
    الـ convention اللي اتحطت فعليًا في core/cache.py.
    """
    return f"feed:{user_id}:page1"


class HomeFeedView(GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = FeedItemSerializer

    def get(self, request):
        cursor = request.query_params.get("cursor") or None
        page_size_raw = request.query_params.get("page_size")
        if page_size_raw is not None:
            try:
                page_size = int(page_size_raw)
            except ValueError:
                raise ValidationError({"page_size": "Must be an integer."})
        else:
            page_size = DEFAULT_PAGE_SIZE

        try:
            result = self._get_feed_page(request.user, cursor, page_size)
        except ValueError as exc:
            # Covers both InvalidCursorError (bad `cursor`) and an
            # out-of-range `page_size` — get_home_feed() raises
            # ValueError for both, deliberately without distinguishing
            # them in the message (see feed/cursor.py's own docstring
            # on not leaking why a forged cursor failed).
            raise ValidationError({"detail": str(exc)})

        serializer = self.get_serializer(result["items"], many=True)
        return Response(
            {"items": serializer.data, "next_cursor": result["next_cursor"]}
        )

    def _get_feed_page(self, user, cursor, page_size):
        """
        Part P-060: صفحة 1 (من غير cursor) بالـ page_size الافتراضي بس
        هي اللي بتتخزن في الكاش لمدة FEED_HOME_CACHE_TTL_SECONDS. أي
        طلب فيه cursor بيتخطى الكاش ويحسب من جديد دايمًا.

        Deviation موثّقة عن الـ exec spec الخام: الـ spec الأصلي بيعمل
        hardcode لـ page_size=20 جوه الـ lambda حتى لو العميل طلب
        page_size مختلف في أول صفحة — ده معناه رد غلط للعميل. بدل كده،
        أي طلب لصفحة 1 بـ page_size مش الافتراضي بيتخطى الكاش تمامًا
        ويتحسب Live، لأن مفتاح الكاش مفيهوش page_size qualifier،
        وتخزين نتيجة بحجم مختلف تحته هيبوظ أول طلب تاني بالـ default.
        """
        if cursor is not None or page_size != DEFAULT_PAGE_SIZE:
            return get_home_feed(user, cursor=cursor, page_size=page_size)

        cache_key = _feed_home_cache_key(user.id)

        def _compute():
            return get_home_feed(user, cursor=None, page_size=DEFAULT_PAGE_SIZE)

        return cache_get_or_set(
            cache_key, _compute, ttl_seconds=FEED_HOME_CACHE_TTL_SECONDS
        )


class DiscoverFeedView(GenericAPIView):
    """
    Part P-062 — GET /api/v1/feed/discover/?cursor=<opaque>&page_size=<int>

    Authenticated only — same reasoning as HomeFeedView: excluding the
    user's followed businesses needs a user to resolve Follow rows for.
    Backfill-tier-only (no following tier at all) — see
    `get_discover_feed()`'s own docstring for why.

    Deliberately NOT cached, unlike HomeFeedView/P-060: this part's own
    Definition of Done does not call for a cache, and unlike Home
    Feed's "page 1, no cursor" case, there's no single canonical first
    page to key a cache on that would stay valid across users with
    different follow sets sharing the same cache entry.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = FeedItemSerializer

    def get(self, request):
        cursor = request.query_params.get("cursor") or None
        page_size_raw = request.query_params.get("page_size")
        if page_size_raw is not None:
            try:
                page_size = int(page_size_raw)
            except ValueError:
                raise ValidationError({"page_size": "Must be an integer."})
        else:
            page_size = DEFAULT_PAGE_SIZE

        try:
            result = get_discover_feed(request.user, cursor, page_size)
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)})

        serializer = self.get_serializer(result["items"], many=True)
        return Response(
            {"items": serializer.data, "next_cursor": result["next_cursor"]}
        )
