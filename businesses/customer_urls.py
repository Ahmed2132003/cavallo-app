"""
URL routes for the businesses app's Customer-profile endpoints
(Part P-026).

Included under /api/v1/customers/ by config/urls.py. Kept as a
separate url module (rather than mixed into urls.py) purely so the
two prefixes each get their own clean, independent include() —
CustomerProfile itself still lives in businesses/models.py (P-024),
this is not a separate Django app.
"""

from django.urls import path

from businesses.views import CustomerProfileMeView

app_name = "customers"

urlpatterns = [
    path("me/", CustomerProfileMeView.as_view(), name="customer-me"),
]
