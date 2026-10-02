"""
Part P-090: payments URLs, mounted at api/v1/payments/ in config/urls.py.
Only the gateway webhook lives here; the purchase flow itself is called
from services (P-089) and the web dashboard (P-092).
"""

from django.urls import path

from payments.views import PaymobWebhookView

urlpatterns = [
    path("webhook/paymob/", PaymobWebhookView.as_view(), name="paymob-webhook"),
]
