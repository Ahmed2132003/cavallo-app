from django.urls import path

from content.views import ReelDetailView, ReelListCreateView

urlpatterns = [
    path("", ReelListCreateView.as_view(), name="reel-list-create"),
    path("<int:pk>/", ReelDetailView.as_view(), name="reel-detail"),
]