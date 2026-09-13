"""
Throwaway DRF view used only to exercise `core.permissions.HasCapability`
(Part P-019).

This view is never wired into `config/urls.py` — it only exists to give
the test client something real to hit, via `accounts/tests/urls.py`
(itself only used by the test suite, via pytest-django's
`@pytest.mark.urls(...)`). Per the part's own scope, no real
Admin/Moderator endpoint was needed to validate this framework — Phase 6
(moderation) is the first real consumer.
"""

from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import HasCapability


class ModerationCapabilityTestView(APIView):
    """
    Requires `accounts.can_moderate_content` via HasCapability.

    A user in the Moderator, Admin, or SuperAdmin group (or any
    superuser) can reach this; a plain Customer/Business user without
    that permission gets a 403 in the standard P-012 error envelope.
    """

    permission_classes = [HasCapability("can_moderate_content")]

    def get(self, request):
        return Response({"ok": True})
