"""
Payments URLs, mounted at api/v1/payments/ in config/urls.py.

* webhook/paymob/ (Part P-090): called by the gateway, unauthenticated by
  necessity, secured by signature verification (payments/views.py).
* initiate/ (Part P-092): called by the Web Dashboard on behalf of an
  authenticated Business owner (payments/initiate_views.py).
"""

from django.urls import path

from payments.initiate_views import PaymentInitiateView
from payments.views import PaymobWebhookView

urlpatterns = [
    path("webhook/paymob/", PaymobWebhookView.as_view(), name="paymob-webhook"),
    path("initiate/", PaymentInitiateView.as_view(), name="payment-initiate"),
]
