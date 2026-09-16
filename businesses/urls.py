"""
URL routes for the businesses app's Business-profile endpoints
(Part P-026).

Included under /api/v1/businesses/ by config/urls.py. See
customer_urls.py for the separate /api/v1/customers/ routes — both
url modules live in this same app since CustomerProfile is defined
here too (P-024), but they're wired under two different root prefixes
per this part's own spec.
"""

from django.urls import path

from businesses.views import BusinessProfileMeView, BusinessProfilePublicView

app_name = "businesses"

urlpatterns = [
    # "me/" is listed before "<int:pk>/" for readability; order doesn't
    # actually matter here since <int:pk> only matches digit strings
    # and could never match the literal "me" anyway.
    path("me/", BusinessProfileMeView.as_view(), name="business-me"),
    path("<int:pk>/", BusinessProfilePublicView.as_view(), name="business-public"),
]
