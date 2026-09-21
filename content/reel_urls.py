from django.urls import path

from content.views import ReelDetailView, ReelListCreateView, ReelPublicListView

urlpatterns = [
    path("public/", ReelPublicListView.as_view(), name="reel-public-list"),
    path("<int:pk>/", ReelDetailView.as_view(), name="reel-detail"),
    path("", ReelListCreateView.as_view(), name="reel-list-create"),
]