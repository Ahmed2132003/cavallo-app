from django.urls import path

from stories.views import StoryListCreateView, StoryPublicListView

urlpatterns = [
    path("", StoryListCreateView.as_view(), name="story-list-create"),
    # Part P-048. Public, expiry-aware "visible now" feed — see
    # StoryPublicListView's docstring for the query-driven-visibility
    # contract this route implements.
    path("public/", StoryPublicListView.as_view(), name="story-public-list"),
]
