"""
Part P-064 STEP 2 — Search URLs. Wired into config/urls.py under
api/v1/search/ — its own top-level prefix, same shape as feed/urls.py
(P-059), since search isn't business-scoped and so doesn't belong
under api/v1/businesses/.
"""

from django.urls import path

from search.views import SearchView

urlpatterns = [
    path("", SearchView.as_view(), name="search"),
]