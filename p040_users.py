"""
Part P-040 manual verification: creates (or reuses) three test accounts.

Run from the backend folder:
  docker compose exec web python manage.py shell -c "exec(open('p040_users.py').read())"

  p040.mod@example.com       is_moderator=True + Moderator Group  (full access)
  p040.flagonly@example.com  is_moderator=True, NO Group          (403 case)
  p040.plain@example.com     ordinary customer                    (no access)
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

from accounts.services import register_user

User = get_user_model()
PASSWORD = "P040-Test-Pass-123!"


def ensure(email):
    user = User.objects.filter(email__iexact=email).first()
    if user is None:
        user = register_user(
            email=email, password=PASSWORD, account_type="customer"
        )
    return user


mod = ensure("p040.mod@example.com")
mod.is_moderator = True
mod.save()
mod.groups.add(Group.objects.get(name="Moderator"))

flag_only = ensure("p040.flagonly@example.com")
flag_only.is_moderator = True
flag_only.save()
flag_only.groups.clear()

plain = ensure("p040.plain@example.com")

for user in (mod, flag_only, plain):
    print(
        f"{user.email:28} is_moderator={user.is_moderator!s:5} "
        f"is_staff={user.is_staff!s:5} "
        f"groups={[g.name for g in user.groups.all()]}"
    )
print(f"Password for all three: {PASSWORD}")