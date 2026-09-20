"""
Part P-040 manual verification: prints what the backend actually
recorded, straight from the database.

Run from the backend folder:
  docker compose exec web python manage.py shell -c "exec(open('p040_verify.py').read())"
"""

from django.contrib.contenttypes.models import ContentType

from moderation.models import ModerationLog, ModerationQueue
from moderation.tests.testapp.models import DummyContent

ct = ContentType.objects.get_for_model(DummyContent)

print("--- ModerationQueue (DummyContent rows) ---")
for q in ModerationQueue.objects.filter(content_type=ct).order_by("id"):
    content = DummyContent.objects.filter(pk=q.object_id).first()
    print(
        f"queue id={q.pk:<4} status={q.status:9} priority={q.priority:9} "
        f"content.status={getattr(content, 'status', None)}"
    )

print("--- ModerationLog ---")
logs = ModerationLog.objects.filter(queue_item__content_type=ct).order_by("id")
for log in logs:
    print(
        f"log id={log.pk:<4} queue_item={log.queue_item_id:<4} "
        f"action={log.action:9} reviewer={log.reviewer.email} "
        f"reason={log.reason!r}"
    )
if not logs:
    print("(no log rows)")