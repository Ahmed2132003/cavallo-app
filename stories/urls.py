from django.urls import path

from stories.views import (
    StoryListCreateView,
    StoryPublicListView,
    StoryViewCountView,
    StoryViewRecordView,
)

urlpatterns = [
    path("", StoryListCreateView.as_view(), name="story-list-create"),
    # Part P-048. Public, expiry-aware "visible now" feed — see
    # StoryPublicListView's docstring for the query-driven-visibility
    # contract this route implements.
    path("public/", StoryPublicListView.as_view(), name="story-public-list"),
    # Part P-049. Most-specific-first ordering (matches
    # products/urls.py's own convention) — doesn't functionally matter
    # here since neither "<int:pk>/view/" nor "<int:pk>/view-count/"
    # could ever collide with "public/" or "" anyway.
    path("<int:pk>/view/", StoryViewRecordView.as_view(), name="story-view-record"),
    path(
        "<int:pk>/view-count/",
        StoryViewCountView.as_view(),
        name="story-view-count",
    ),
]
