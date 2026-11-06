"""
Focused tests for Task P2.2 — password reset token core.

Like test_password_reset.py (P2.1), these talk to the real Redis configured
for the test settings. Each test uses a unique email address, and the
fixture deletes only the keys derived from that address afterwards. The
database is never flushed.
"""

import hashlib
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from unittest import mock
from uuid import uuid4

import pytest
from django_redis import get_redis_connection

from accounts import services
from accounts.services import (
    RESET_TOKEN_TTL_SECONDS,
    consume_password_reset_token,
    create_password_reset_otp,
    verify_otp_and_issue_reset_token,
    verify_password_reset_otp,
)


@pytest.fixture
def redis_conn():
    return get_redis_connection("default")


@pytest.fixture
def email(redis_conn):
    address = f"rt-{uuid4().hex}@example.com"
    yield address
    redis_conn.delete(*services._otp_keys(address), services._reset_token_key(address))


def _wrong(otp):
    return f"{(int(otp) + 1) % 10**6:06d}"


def _issue(email):
    otp = create_password_reset_otp(email)
    token = verify_otp_and_issue_reset_token(email, otp)
    assert token is not None
    return token


def _other_token(token):
    """A well-formed token that differs from `token`."""
    return ("A" if token[0] != "A" else "B") + token[1:]


# --- issuing ---------------------------------------------------------------


def test_token_issued_after_successful_otp(email):
    otp = create_password_reset_otp(email)
    token = verify_otp_and_issue_reset_token(email, otp)
    assert isinstance(token, str)
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", token)


def test_tokens_are_random(email):
    first = _issue(email)
    second = _issue(email)
    assert first != second


def test_wrong_otp_issues_no_token(redis_conn, email):
    otp = create_password_reset_otp(email)
    assert verify_otp_and_issue_reset_token(email, _wrong(otp)) is None
    assert redis_conn.exists(services._reset_token_key(email)) == 0


def test_unknown_email_issues_no_token(redis_conn, email):
    assert verify_otp_and_issue_reset_token(email, "123456") is None
    assert redis_conn.exists(services._reset_token_key(email)) == 0


def test_expired_otp_issues_no_token(redis_conn, email):
    otp = create_password_reset_otp(email)
    otp_key, _ = services._otp_keys(email)
    redis_conn.pexpire(otp_key, 1)
    time.sleep(0.05)
    assert verify_otp_and_issue_reset_token(email, otp) is None
    assert redis_conn.exists(services._reset_token_key(email)) == 0


@pytest.mark.parametrize("bad_otp", ["", "12345", "12345a", " 123456", None, 123456])
def test_malformed_otp_issues_no_token(redis_conn, email, bad_otp):
    create_password_reset_otp(email)
    assert verify_otp_and_issue_reset_token(email, bad_otp) is None
    assert redis_conn.exists(services._reset_token_key(email)) == 0


def test_exhausted_otp_attempts_issue_no_token(redis_conn, email):
    otp = create_password_reset_otp(email)
    for _ in range(5):
        assert verify_otp_and_issue_reset_token(email, _wrong(otp)) is None
    # Sixth attempt is rejected even with the correct OTP.
    assert verify_otp_and_issue_reset_token(email, otp) is None
    assert redis_conn.exists(services._reset_token_key(email)) == 0


def test_otp_cannot_be_reused_to_mint_a_second_token(email):
    otp = create_password_reset_otp(email)
    token = verify_otp_and_issue_reset_token(email, otp)
    assert token is not None
    assert verify_otp_and_issue_reset_token(email, otp) is None
    assert verify_password_reset_otp(email, otp) is False
    # The first token is untouched by the failed replay.
    assert consume_password_reset_token(email, token) is True


def test_otp_is_consumed_when_token_is_issued(redis_conn, email):
    _issue(email)
    assert redis_conn.exists(*services._otp_keys(email)) == 0


def test_issue_uses_email_normalization(email):
    otp = create_password_reset_otp(email)
    token = verify_otp_and_issue_reset_token(f"  {email.upper()}  ", otp)
    assert token is not None
    assert consume_password_reset_token(email, token) is True


# --- storage ---------------------------------------------------------------


def test_raw_token_is_not_stored_in_redis(redis_conn, email):
    token = _issue(email)
    key = services._reset_token_key(email)
    stored = redis_conn.get(key).decode("ascii")
    assert stored != token
    assert token not in stored
    assert len(stored) == 64
    assert stored == services._reset_token_digest(email, token)
    # Nothing under any key derived from this email holds the raw token.
    for derived in (*services._otp_keys(email), key):
        assert token not in (redis_conn.get(derived) or b"").decode("ascii")
        assert token not in derived


def test_redis_key_does_not_expose_email(redis_conn, email):
    _issue(email)
    key = services._reset_token_key(email)
    email_id = hashlib.sha256(email.encode("utf-8")).hexdigest()
    assert redis_conn.exists(key) == 1
    assert email not in key
    assert email.split("@")[0] not in key
    assert "@" not in key
    assert key.endswith(email_id)


def test_token_digest_depends_on_email_and_token():
    base = services._reset_token_digest("a@example.com", "t" * 43)
    assert base != services._reset_token_digest("b@example.com", "t" * 43)
    assert base != services._reset_token_digest("a@example.com", "u" * 43)


def test_token_ttl_is_300_seconds(redis_conn, email):
    assert RESET_TOKEN_TTL_SECONDS == 300
    _issue(email)
    pttl = redis_conn.pttl(services._reset_token_key(email))
    assert 295_000 < pttl <= 300_000


def test_token_ttl_is_independent_of_otp_ttl(redis_conn, email):
    otp_ms = services.OTP_TTL_SECONDS * 1000
    token_ms = RESET_TOKEN_TTL_SECONDS * 1000
    assert RESET_TOKEN_TTL_SECONDS >= services.OTP_TTL_SECONDS

    otp = create_password_reset_otp(email)
    otp_key, _ = services._otp_keys(email)
    assert otp_ms - 5_000 < redis_conn.pttl(otp_key) <= otp_ms

    verify_otp_and_issue_reset_token(email, otp)
    token_pttl = redis_conn.pttl(services._reset_token_key(email))
    assert token_ms - 5_000 < token_pttl <= token_ms


# --- consuming -------------------------------------------------------------


def test_valid_token_is_consumed(email):
    token = _issue(email)
    assert consume_password_reset_token(email, token) is True


def test_token_is_single_use(redis_conn, email):
    token = _issue(email)
    assert consume_password_reset_token(email, token) is True
    assert consume_password_reset_token(email, token) is False
    assert redis_conn.exists(services._reset_token_key(email)) == 0


def test_invalid_token_is_rejected_and_does_not_consume(email):
    token = _issue(email)
    assert consume_password_reset_token(email, _other_token(token)) is False
    assert consume_password_reset_token(email, token) is True


def test_unknown_email_token_creates_no_keys(redis_conn, email):
    assert consume_password_reset_token(email, "A" * 43) is False
    assert redis_conn.exists(services._reset_token_key(email)) == 0


@pytest.mark.parametrize(
    "bad_token",
    [
        "",
        "short",
        "A" * 42,
        "A" * 44,
        "A" * 42 + " ",
        "A" * 42 + "\n",
        "A" * 42 + "=",
        "A" * 42 + "é",
        "١" * 43,
        None,
        12345,
        b"A" * 43,
    ],
)
def test_malformed_token_rejected_without_touching_valid_token(
    redis_conn, email, bad_token
):
    token = _issue(email)
    with mock.patch("accounts.services.get_redis_connection") as conn_factory:
        assert consume_password_reset_token(email, bad_token) is False
        conn_factory.assert_not_called()
    assert redis_conn.exists(services._reset_token_key(email)) == 1
    assert consume_password_reset_token(email, token) is True


def test_expired_token_is_rejected(redis_conn, email):
    token = _issue(email)
    redis_conn.pexpire(services._reset_token_key(email), 1)
    time.sleep(0.05)
    assert consume_password_reset_token(email, token) is False


def test_token_is_bound_to_its_email(email):
    other = f"rt-{uuid4().hex}@example.com"
    token = _issue(email)
    try:
        assert consume_password_reset_token(other, token) is False
        # The rightful email can still use it.
        assert consume_password_reset_token(email, token) is True
    finally:
        get_redis_connection("default").delete(services._reset_token_key(other))


def test_other_emails_token_cannot_be_used(email):
    other = f"rt-{uuid4().hex}@example.com"
    try:
        token_for_email = _issue(email)
        token_for_other = _issue(other)
        assert consume_password_reset_token(email, token_for_other) is False
        assert consume_password_reset_token(other, token_for_email) is False
        assert consume_password_reset_token(email, token_for_email) is True
        assert consume_password_reset_token(other, token_for_other) is True
    finally:
        get_redis_connection("default").delete(
            *services._otp_keys(other), services._reset_token_key(other)
        )


def test_consume_uses_email_normalization(email):
    token = _issue(email)
    assert consume_password_reset_token(f"  {email.upper()}  ", token) is True


def test_reissue_invalidates_previous_token(email):
    first = _issue(email)
    second = _issue(email)
    assert consume_password_reset_token(email, first) is False
    assert consume_password_reset_token(email, second) is True


def test_stale_digest_cannot_consume_newer_token(redis_conn, email):
    first = _issue(email)
    stale_digest = services._reset_token_digest(email, first)
    second = _issue(email)
    key = services._reset_token_key(email)
    result = services._run_script(
        redis_conn, services._LUA_CONSUME_RESET_TOKEN, [key], [stale_digest]
    )
    assert int(result) == 0
    assert redis_conn.exists(key) == 1
    assert consume_password_reset_token(email, second) is True


# --- concurrency -----------------------------------------------------------


def test_concurrent_consumption_allows_only_one_success(email):
    token = _issue(email)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(lambda _: consume_password_reset_token(email, token), range(8))
        )
    assert results.count(True) == 1
    assert results.count(False) == 7


def test_concurrent_exchange_of_one_otp_issues_only_one_token(redis_conn, email):
    otp = create_password_reset_otp(email)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(lambda _: verify_otp_and_issue_reset_token(email, otp), range(8))
        )
    tokens = [token for token in results if token is not None]
    assert len(tokens) == 1
    assert consume_password_reset_token(email, tokens[0]) is True


# --- logging ---------------------------------------------------------------


def test_raw_token_is_never_logged(caplog, email):
    with caplog.at_level(logging.DEBUG):
        token = _issue(email)
        consume_password_reset_token(email, token)
        consume_password_reset_token(email, token)
    assert token not in caplog.text


# --- P2.1 interface is unchanged ------------------------------------------


def test_p21_otp_functions_still_work_independently(email):
    otp = create_password_reset_otp(email)
    assert verify_password_reset_otp(email, otp) is True
    assert verify_password_reset_otp(email, otp) is False