"""
URL routes for Part P-109's rate/ratings endpoints.

Mounted under /api/v1/businesses/ by config/urls.py, alongside (not
instead of) businesses.urls' and social.urls' own includes under that
same prefix - same shape as social.urls being a second app sharing
the businesses/ prefix (P-052's precedent), just one more app added
to that list.
"""

from django.urls import path

from ratings.views import RateBusinessView, RatingsListView

app_name = "ratings"

urlpatterns = [
    path("<int:pk>/rate/", RateBusinessView.as_view(), name="business-rate"),
    path("<int:pk>/ratings/", RatingsListView.as_view(), name="business-ratings"),
]
