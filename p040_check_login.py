"""
Part P-040 manual verification: diagnoses a failed login (HTTP 401) for
the three test accounts. Read-only - changes nothing.

Run from the backend folder:
  docker compose exec web python manage.py shell -c "exec(open('p040_check_login.py').read())"
"""

from django.contrib.auth import get_user_model

from accounts.services import authenticate_user

User = get_user_model()
PASSWORD = "P040-Test-Pass-123!"

for email in (
    "p040.mod@example.com",
    "p040.flagonly@example.com",
    "p040.plain@example.com",
):
    user = User.objects.filter(email__iexact=email).first()
    if user is None:
        print(f"{email:28} DOES NOT EXIST  -> run p040_users.py")
        continue
    print(
        f"{email:28} exists, is_active={user.is_active}, "
        f"password_ok={user.check_password(PASSWORD)}, "
        f"authenticate_user={'OK' if authenticate_user(email=email, password=PASSWORD) else 'FAILED'}, "
        f"is_moderator={user.is_moderator}, "
        f"groups={[g.name for g in user.groups.all()]}"
    )

print("--- all users whose email contains 'p040' ---")
for user in User.objects.filter(email__icontains="p040"):
    print(user.pk, user.email)