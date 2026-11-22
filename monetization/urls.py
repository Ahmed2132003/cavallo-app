"""
Part P-092 (STEP 1): monetization URLs, mounted at api/v1/monetization/
in config/urls.py. The public Plan list and (BE-03) the merchant-only
custom-quote live here; activation of
Featured status is never exposed over HTTP (see
monetization/services.py: activate_subscription()).
"""

from django.urls import path

from monetization.views import CustomQuoteView, PlanListView

app_name = "monetization"

urlpatterns = [
    path("plans/", PlanListView.as_view(), name="plan-list"),
    path(
        "subscriptions/custom-quote/",
        CustomQuoteView.as_view(),
        name="custom-quote",
    ),
]
