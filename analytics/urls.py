from django.urls import path

from analytics.views import BusinessDailyStatsListView

urlpatterns = [
    path(
        "business/<int:pk>/daily/",
        BusinessDailyStatsListView.as_view(),
        name="business-daily-stats",
    ),
]