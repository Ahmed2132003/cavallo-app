"""
Part P-040 manual verification: removes everything the verification
created, so the dev database is left as it was found.

Run from the backend folder, while the manual settings are still active:
  docker compose exec web python manage.py shell -c "exec(open('p040_cleanup.py').read())"

Then restore the normal web service and delete the temporary files (see
the step-by-step instructions).
"""

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import connection

from moderation.models import ModerationLog, ModerationQueue
from moderation.tests.testapp.models import DummyContent

User = get_user_model()
ct = ContentType.objects.get_for_model(DummyContent)

queue = ModerationQueue.objects.filter(content_type=ct)
print("logs deleted:", ModerationLog.objects.filter(queue_item__in=queue).delete())
print("queue rows deleted:", queue.delete())
print("dummy content deleted:", DummyContent.objects.all().delete())

table = DummyContent._meta.db_table
with connection.cursor() as cursor:
    cursor.execute(f'DROP TABLE IF EXISTS "{table}"')
print("dropped table:", table)

print("content type deleted:", ct.delete())

emails = [
    "p040.mod@example.com",
    "p040.flagonly@example.com",
    "p040.plain@example.com",
]
print("test users deleted:", User.objects.filter(email__in=emails).delete())