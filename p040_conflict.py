"""
Part P-040 manual verification (409 case): approves one pending queue
item on the server, as the moderator, behind the app's back.

Run from the backend folder (replace 12 with the queue id shown as
"Queue item #N" on the review screen you are holding open in the app):
  docker compose exec -e ITEM_ID=12 web python manage.py shell -c "exec(open('p040_conflict.py').read())"
"""

import os

from django.contrib.auth import get_user_model

from moderation import services
from moderation.models import ModerationQueue

queue_id = int(os.environ["ITEM_ID"])
reviewer = get_user_model().objects.get(email="p040.mod@example.com")
item = ModerationQueue.objects.get(pk=queue_id)
log = services.approve(item, reviewer)
print(f"Approved queue id={queue_id} on the server (log id={log.pk}).")