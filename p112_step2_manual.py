# P-112 STEP 2 manual check. Run from D:\Cavallo\scd-backend:
#   docker compose exec web python manage.py shell -c "exec(open('p112_step2_manual.py', encoding='utf-8').read())"
# Uses the first user in the DB, creates 2 notifications + 1 device token, prints what it
# finds, then removes everything it created and restores the user's language.
from unittest.mock import patch

from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from devices.models import DeviceToken
from notifications.models import Notification
from notifications.tasks import dispatch_notification

User = get_user_model()
user = User.objects.order_by("id").first()
original = user.preferred_language
created_ids = []


def fire(language):
    User.objects.filter(pk=user.pk).update(preferred_language=language)
    with patch("notifications.tasks.send_push_notification") as push:
        dispatch_notification(
            recipient_id=user.id,
            notification_type="moderation_rejected",
            title="Your post was rejected",
            body="Reason: x",
            deep_link_type="business_profile",
            target_id=1,
            params={"item": "post", "reason": "Blurry image"},
        )
    row = Notification.objects.filter(recipient=user).order_by("-id").first()
    created_ids.append(row.id)
    print(f"[{language}] title={row.title!r} body={row.body!r} params={row.params}")
    print(f"      push languages sent: {sorted(push.call_args.kwargs['localized'])}")


try:
    fire("ar")
    fire("en")

    client = APIClient(HTTP_HOST="localhost")
    client.force_authenticate(user=user)
    r = client.post(
        "/api/v1/devices/register/",
        {"token": "p112-manual-token", "platform": "android", "locale": "ar-EG"},
        format="json",
    )
    print("register ->", r.status_code, r.json())
    print("stored locale:", DeviceToken.objects.get(token="p112-manual-token").locale)
finally:
    Notification.objects.filter(id__in=created_ids).delete()
    DeviceToken.objects.filter(token="p112-manual-token").delete()
    User.objects.filter(pk=user.pk).update(preferred_language=original)
    print("cleaned up; language restored to", original)