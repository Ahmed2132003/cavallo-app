"""
URL routes for Part P-053's generic Like/Unlike endpoint.

Mounted under /api/v1/likes/ by config/urls.py as its own top-level
prefix — separate from social/urls.py's /api/v1/businesses/ prefix
(Follow), since Like isn't business-scoped: it targets a generic
content object (Post or Reel) identified in the request body, not a
business id in the URL path.
"""

from django.urls import path

from social.views import LikeToggleView

app_name = "likes"

urlpatterns = [
    path("", LikeToggleView.as_view(), name="toggle"),
]