"""
Part BI-02 - external (other platform) metrics.

ONLY the manual path is wired. ExternalMetricsProvider is an interface for a
future official-API integration; nothing implements it and nothing here
makes a network call or scrapes anything. Every number returned to a client
carries ``source`` (manual / synced) and ``last_updated`` (its captured_at).
"""

import abc
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List

from django.utils import timezone

from analytics.models import ExternalContentMetricSnapshot
from analytics.utils import iso


@dataclass(frozen=True)
class ExternalMetricReading:
    metric_type: str
    metric_value: int
    captured_at: datetime
    external_reference: str = ""


class ExternalMetricsProvider(abc.ABC):
    """One implementation per platform, official APIs only (future work)."""

    platform = ""

    @abc.abstractmethod
    def fetch_metrics(
        self, *, business, content_type, content_id, external_reference
    ) -> List[ExternalMetricReading]:
        """Return the current readings for one Post/Reel on this platform."""


_REGISTRY: Dict[str, ExternalMetricsProvider] = {}


def register_provider(provider):
    if provider.platform not in ExternalContentMetricSnapshot.Platform.values:
        raise ValueError(f"Unknown platform: {provider.platform!r}")
    _REGISTRY[provider.platform] = provider


def get_provider(platform):
    return _REGISTRY.get(platform)


def registered_platforms():
    return sorted(_REGISTRY)


def create_snapshot(
    *,
    business,
    content_type,
    content_id,
    platform,
    metric_type,
    metric_value,
    captured_at=None,
    source=ExternalContentMetricSnapshot.Source.MANUAL,
    external_reference="",
):
    """Single write path for manual entries and (future) synced readings."""
    return ExternalContentMetricSnapshot.objects.create(
        business=business,
        content_type=content_type,
        content_id=content_id,
        platform=platform,
        metric_type=metric_type,
        metric_value=metric_value,
        captured_at=captured_at or timezone.now(),
        source=source,
        external_reference=external_reference,
    )


def serialize_snapshot(snapshot):
    return {
        "platform": snapshot.platform,
        "metric_type": snapshot.metric_type,
        "value": snapshot.metric_value,
        "source": snapshot.source,
        "last_updated": iso(snapshot.captured_at),
        "external_reference": snapshot.external_reference,
    }


def latest_snapshots(business, content_type, content_id):
    """Latest snapshot per (platform, metric_type), as plain dicts."""
    queryset = (
        ExternalContentMetricSnapshot.objects.filter(
            business=business, content_type=content_type, content_id=content_id
        )
        .order_by("platform", "metric_type", "-captured_at", "-id")
        .distinct("platform", "metric_type")
    )
    return [serialize_snapshot(snapshot) for snapshot in queryset]
