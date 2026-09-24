from django.urls import path

from reports.views import ReportCreateView

app_name = "reports"

urlpatterns = [
    path("", ReportCreateView.as_view(), name="create"),
]
