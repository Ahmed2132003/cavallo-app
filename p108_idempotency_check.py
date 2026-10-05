# P-108 Test 3 - Celery idempotency spot check (stories.expire_stale_stories).
# Run with:  Get-Content .\p108_idempotency_check.py -Raw | python manage.py shell
# Expected last line: IDEMPOTENT  (second run changes nothing).
from stories.models import Story
from stories.tasks import expire_stale_stories


def snap():
    return Story.objects.filter(archived_at__isnull=False).count()


before = snap()
r1 = expire_stale_stories()
mid = snap()
r2 = expire_stale_stories()
after = snap()
print("archived rows  before / after run 1 / after run 2:", before, mid, after)
print("run 1 result:", r1)
print("run 2 result:", r2)
print("IDEMPOTENT" if mid == after else "NOT IDEMPOTENT")