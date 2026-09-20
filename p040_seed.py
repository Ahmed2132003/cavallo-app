"""
Part P-040 manual verification: seeds five pending queue items with
different priorities and ages, so the queue's sorting and its green /
amber / red age colours can all be seen at once.

Run from the backend folder:
  docker compose exec web python manage.py shell -c "exec(open('p040_seed.py').read())"

Creating a DummyContent row makes the real P-036 signal enqueue it; the
priority and created_at are then adjusted on the queue row (dev DB only).
"""

from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from moderation.models import ModerationQueue
from moderation.tests.testapp.models import DummyContent

ct = ContentType.objects.get_for_model(DummyContent)
now = timezone.now()

PLAN = [
    ("fast_path", timedelta(minutes=40)),  # fast_path, past 30 min  -> RED
    ("fast_path", timedelta(minutes=20)),  # fast_path, 15-30 min    -> AMBER
    ("normal", timedelta(hours=5)),  # normal, past 4 h        -> RED
    ("normal", timedelta(hours=3)),  # normal, 2-4 h           -> AMBER
    ("normal", timedelta(minutes=1)),  # normal, fresh           -> GREEN
]

for index, (priority, age) in enumerate(PLAN, start=1):
    content = DummyContent.objects.create(title=f"P040 item {index}")
    queue_item = ModerationQueue.objects.get(
        content_type=ct, object_id=content.pk
    )
    ModerationQueue.objects.filter(pk=queue_item.pk).update(
        priority=priority, created_at=now - age
    )
    print(
        f"queue id={queue_item.pk:<4} content id={content.pk:<4} "
        f"{priority:9} age~{age}"
    )