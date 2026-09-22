from django.urls import path

from stories.views import StoryCreateView

urlpatterns = [
    path("", StoryCreateView.as_view(), name="story-list-create"),
]
