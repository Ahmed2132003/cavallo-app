"""
URL routes for Part P-081's device-token registration endpoint.

Mounted under /api/v1/devices/ by config/urls.py.
"""

from django.urls import path

from devices.views import DeviceRegisterView

app_name = "devices"

urlpatterns = [
    path("register/", DeviceRegisterView.as_view(), name="register"),
]
