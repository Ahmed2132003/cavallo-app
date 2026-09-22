from django.urls import path

from stories.views import StoryListCreateView

urlpatterns = [
    path("", StoryListCreateView.as_view(), name="story-list-create"),
]