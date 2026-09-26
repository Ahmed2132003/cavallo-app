"""
Part P-059 -- Home Feed URLs. Wired into config/urls.py under
api/v1/feed/ -- its own top-level prefix, same shape as likes/saves/
comments/shares, since the feed isn't business-scoped and so doesn't
belong under api/v1/businesses/.
"""

from django.urls import path

from feed.views import DiscoverFeedView, HomeFeedView

urlpatterns = [
    path("home/", HomeFeedView.as_view(), name="home-feed"),
    # Part P-062. Backfill-tier-only "browse broadly" feed — see
    # DiscoverFeedView's own docstring.
    path("discover/", DiscoverFeedView.as_view(), name="discover-feed"),
]
