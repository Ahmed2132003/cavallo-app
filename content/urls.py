from django.urls import path

from content.views import PostDetailView, PostListCreateView, PostPublicListView

urlpatterns = [
    path("public/", PostPublicListView.as_view(), name="post-public-list"),
    path("<int:pk>/", PostDetailView.as_view(), name="post-detail"),
    path("", PostListCreateView.as_view(), name="post-list-create"),
]