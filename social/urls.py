"""
URL routes for Part P-052's Follow/Unfollow endpoint.

Mounted under /api/v1/businesses/ by config/urls.py, alongside (not
instead of) businesses.urls' own include — same shape as
content/reel_urls.py being a second include under a shared-app
prefix pattern, just inverted here (two different apps sharing one
prefix, since the Follow model lives in `social`, not `businesses`).
"""

from django.urls import path

from social.views import FollowToggleView

app_name = "social"

urlpatterns = [
    path("<int:pk>/follow/", FollowToggleView.as_view(), name="business-follow"),
]
