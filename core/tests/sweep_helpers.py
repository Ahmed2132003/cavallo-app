"""
Shared assertions for the P-096 permission / IDOR sweep.

Every negative-path sweep test asserts the P-012 error envelope, not
just the status code, so a 401/403 that accidentally carries a 500-ish
or ad-hoc body fails the sweep:

    {"error": {"code": "...", "message": "...", "fields": {}}}
"""


def assert_error_envelope(response, status_code, code):
    assert response.status_code == status_code, (
        f"expected HTTP {status_code}, got {response.status_code}: "
        f"{response.content[:300]!r}"
    )
    body = response.json()
    assert list(body.keys()) == ["error"], body
    error = body["error"]
    assert error["code"] == code, error
    assert isinstance(error["message"], str) and error["message"], error
    assert error["fields"] == {}, error


def assert_unauthenticated(response):
    assert_error_envelope(response, 401, "AUTHENTICATION_FAILED")


def assert_forbidden(response):
    assert_error_envelope(response, 403, "PERMISSION_DENIED")


def assert_not_found(response):
    assert_error_envelope(response, 404, "NOT_FOUND")
