"""
Focused tests for Task P2.1 — password reset OTP core.

These tests talk to the real Redis configured for the test settings. Each
test uses a unique email address, and the fixture deletes only the keys
derived from that address afterwards. The database is never flushed.
"""

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from unittest import mock
from uuid import uuid4

import pytest
from django_redis import get_redis_connection

from accounts import services
from accounts.services import (
    create_password_reset_otp,
    verify_password_reset_otp,
)


@pytest.fixture
def redis_conn():
    return get_redis_connection("default")


@pytest.fixture
def email(redis_conn):
    address = f"otp-{uuid4().hex}@example.com"
    yield address
    redis_conn.delete(*services._otp_keys(address))


def _wrong(otp):
    return f"{(int(otp) + 1) % 10**6:06d}"


def _attempts(redis_conn, email):
    _, attempts_key = services._otp_keys(email)
    return int(redis_conn.get(attempts_key) or 0)


def _stored_value(redis_conn, email):
    otp_key, _ = services._otp_keys(email)
    return redis_conn.get(otp_key).decode("ascii")


def test_otp_is_six_digits(email):
    otp = create_password_reset_otp(email)
    assert isinstance(otp, str)
    assert len(otp) == 6
    assert otp.isascii() and otp.isdigit()


def test_otp_is_zero_padded(email):
    with mock.patch("accounts.services.secrets.randbelow", return_value=7):
        otp = create_password_reset_otp(email)
    assert otp == "000007"
    assert verify_password_reset_otp(email, "000007") is True


def test_digest_is_stored_not_raw_otp(redis_conn, email):
    otp = create_password_reset_otp(email)
    stored = _stored_value(redis_conn, email)
    issuance_id, digest = stored.split(":", 1)
    assert stored != otp
    assert len(issuance_id) == 32
    assert len(digest) == 64
    assert digest == services._otp_digest(email, issuance_id, otp)


def test_each_issuance_gets_a_unique_issuance_id(redis_conn, email):
    create_password_reset_otp(email)
    first_id = _stored_value(redis_conn, email).split(":", 1)[0]
    create_password_reset_otp(email)
    second_id = _stored_value(redis_conn, email).split(":", 1)[0]
    assert first_id != second_id


def test_otp_expires_in_300_seconds(redis_conn, email):
    assert services.OTP_TTL_SECONDS == 300
    ttl_ms = services.OTP_TTL_SECONDS * 1000
    create_password_reset_otp(email)
    otp_key, attempts_key = services._otp_keys(email)
    assert ttl_ms - 5_000 < redis_conn.pttl(otp_key) <= ttl_ms
    assert ttl_ms - 5_000 < redis_conn.pttl(attempts_key) <= ttl_ms


def test_correct_otp_succeeds(email):
    otp = create_password_reset_otp(email)
    assert verify_password_reset_otp(email, otp) is True


def test_incorrect_otp_fails(email):
    otp = create_password_reset_otp(email)
    assert verify_password_reset_otp(email, _wrong(otp)) is False


def test_unknown_email_fails_and_creates_no_keys(redis_conn, email):
    assert verify_password_reset_otp(email, "123456") is False
    assert redis_conn.exists(*services._otp_keys(email)) == 0


def test_expired_otp_fails(redis_conn, email):
    otp = create_password_reset_otp(email)
    otp_key, _ = services._otp_keys(email)
    redis_conn.pexpire(otp_key, 1)
    time.sleep(0.05)
    assert verify_password_reset_otp(email, otp) is False


def test_five_wrong_attempts_allowed_and_sixth_rejected(redis_conn, email):
    otp = create_password_reset_otp(email)
    wrong = _wrong(otp)
    for _ in range(5):
        assert verify_password_reset_otp(email, wrong) is False
    assert _attempts(redis_conn, email) == 5

    # Sixth attempt is rejected even with the correct OTP, and is not counted.
    assert verify_password_reset_otp(email, otp) is False
    assert _attempts(redis_conn, email) == 5


def test_fifth_attempt_can_still_succeed(email):
    otp = create_password_reset_otp(email)
    wrong = _wrong(otp)
    for _ in range(4):
        assert verify_password_reset_otp(email, wrong) is False
    assert verify_password_reset_otp(email, otp) is True


def test_correct_otp_is_single_use(redis_conn, email):
    otp = create_password_reset_otp(email)
    assert verify_password_reset_otp(email, otp) is True
    assert verify_password_reset_otp(email, otp) is False
    assert redis_conn.exists(*services._otp_keys(email)) == 0


def test_reissue_invalidates_previous_otp(email):
    with mock.patch(
        "accounts.services.secrets.randbelow", side_effect=[111111, 222222]
    ):
        first = create_password_reset_otp(email)
        second = create_password_reset_otp(email)
    assert (first, second) == ("111111", "222222")
    assert verify_password_reset_otp(email, first) is False
    assert verify_password_reset_otp(email, second) is True


def test_reissue_resets_attempt_count(redis_conn, email):
    otp = create_password_reset_otp(email)
    wrong = _wrong(otp)
    for _ in range(3):
        verify_password_reset_otp(email, wrong)
    assert _attempts(redis_conn, email) == 3

    new_otp = create_password_reset_otp(email)
    assert _attempts(redis_conn, email) == 0
    assert verify_password_reset_otp(email, new_otp) is True


def test_stale_issuance_cannot_consume_reissued_otp(redis_conn, email):
    with mock.patch(
        "accounts.services.secrets.randbelow", side_effect=[111111, 222222]
    ):
        create_password_reset_otp(email)
        otp_key, attempts_key = services._otp_keys(email)
        stale_value = _stored_value(redis_conn, email)
        second = create_password_reset_otp(email)

    assert (
        services._consume_otp(redis_conn, otp_key, attempts_key, stale_value) is False
    )
    assert verify_password_reset_otp(email, second) is True


def test_stale_issuance_cannot_consume_reissue_with_identical_otp(redis_conn, email):
    """
    Regression: the six-digit OTP is forced to collide across two issuances.
    The old issuance value must not be able to consume the new one.
    """
    with mock.patch("accounts.services.secrets.randbelow", return_value=123456):
        first = create_password_reset_otp(email)
        otp_key, attempts_key = services._otp_keys(email)
        old_value = _stored_value(redis_conn, email)
        second = create_password_reset_otp(email)

    new_value = _stored_value(redis_conn, email)
    assert first == second == "123456"
    assert new_value != old_value

    # A verification that fetched the old issuance cannot consume the new one.
    assert services._consume_otp(redis_conn, otp_key, attempts_key, old_value) is False
    assert redis_conn.exists(otp_key) == 1
    assert _stored_value(redis_conn, email) == new_value

    # The newly issued OTP is still valid exactly once.
    assert verify_password_reset_otp(email, second) is True
    assert verify_password_reset_otp(email, second) is False


def test_email_with_colon_in_local_part(redis_conn):
    address = f'"a:b-{uuid4().hex}"@example.com'
    try:
        otp = create_password_reset_otp(address)
        assert verify_password_reset_otp(f"  {address.upper()}  ", otp) is True
        assert verify_password_reset_otp(address, otp) is False
    finally:
        redis_conn.delete(*services._otp_keys(address))


def test_email_normalization_is_consistent(email):
    otp = create_password_reset_otp(f"  {email.upper()}  ")
    assert verify_password_reset_otp(email, otp) is True


def test_redis_keys_do_not_expose_email(redis_conn, email):
    create_password_reset_otp(email)
    email_id = hashlib.sha256(email.encode("utf-8")).hexdigest()
    keys = services._otp_keys(email)
    local_part = email.split("@")[0]
    for key in keys:
        assert redis_conn.exists(key) == 1
        assert email not in key
        assert local_part not in key
        assert "@" not in key
        assert key.endswith(email_id)


@pytest.mark.parametrize(
    "bad_otp",
    ["", "12345", "1234567", "12345a", "123456\n", " 123456", "١٢٣٤٥٦", None, 123456],
)
def test_malformed_otp_rejected_without_consuming_attempt(redis_conn, email, bad_otp):
    otp = create_password_reset_otp(email)
    assert verify_password_reset_otp(email, bad_otp) is False
    assert _attempts(redis_conn, email) == 0
    assert verify_password_reset_otp(email, otp) is True


def test_concurrent_verifications_allow_only_one_success(email):
    otp = create_password_reset_otp(email)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(lambda _: verify_password_reset_otp(email, otp), range(8))
        )
    assert results.count(True) == 1
