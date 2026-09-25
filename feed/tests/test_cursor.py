"""
Part P-059 — Home Feed cursor tests (pure functions, no database).

The cursor is what lets the feed resume correctly across the
followed -> backfill transition, so its encoding is tested on its own
first: exact round-trips, and rejection of every malformed shape.
"""

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest

from feed.cursor import (
    MAX_CURSOR_LENGTH,
    PHASE_BACKFILL,
    PHASE_FOLLOWING,
    FeedCursor,
    InvalidCursorError,
    decode_cursor,
    encode_cursor,
)

UTC = timezone.utc
TS = datetime(2026, 9, 24, 9, 41, 9, 887123, tzinfo=UTC)


def _token(payload):
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _following_payload(**overrides):
    payload = {
        "v": 1,
        "p": "f",
        "ts": "2026-09-24T09:41:09.887123+00:00",
        "t": "post",
        "id": 12,
    }
    payload.update(overrides)
    return payload


def _backfill_payload(**overrides):
    payload = _following_payload(p="b", ft=1)
    payload.update(overrides)
    return payload


class TestRoundTrip:
    def test_following_cursor_round_trips(self):
        cursor = FeedCursor(PHASE_FOLLOWING, TS, "post", 12)
        assert decode_cursor(encode_cursor(cursor)) == cursor

    def test_backfill_cursor_round_trips_featured_and_not(self):
        for featured in (True, False):
            cursor = FeedCursor(PHASE_BACKFILL, TS, "reel", 7, is_featured=featured)
            decoded = decode_cursor(encode_cursor(cursor))
            assert decoded == cursor
            assert decoded.is_featured is featured

    def test_microseconds_are_preserved(self):
        cursor = FeedCursor(PHASE_FOLLOWING, TS, "post", 1)
        assert decode_cursor(encode_cursor(cursor)).created_at.microsecond == 887123

    def test_zero_microseconds_round_trip(self):
        exact = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        cursor = FeedCursor(PHASE_FOLLOWING, exact, "reel", 3)
        assert decode_cursor(encode_cursor(cursor)) == cursor

    def test_non_utc_timestamp_is_normalised_to_utc(self):
        cairo = timezone(timedelta(hours=3))
        local = TS.astimezone(cairo)
        cursor = FeedCursor(PHASE_FOLLOWING, local, "post", 5)
        decoded = decode_cursor(encode_cursor(cursor))
        assert decoded.created_at == TS
        assert decoded.created_at.utcoffset() == timedelta(0)

    def test_same_position_always_encodes_to_the_same_string(self):
        a = FeedCursor(PHASE_FOLLOWING, TS, "post", 12)
        b = FeedCursor(
            PHASE_FOLLOWING, TS.astimezone(timezone(timedelta(hours=3))), "post", 12
        )
        assert encode_cursor(a) == encode_cursor(b)

    def test_token_is_url_safe_and_unpadded(self):
        token = encode_cursor(FeedCursor(PHASE_BACKFILL, TS, "reel", 99, True))
        assert token.isascii()
        assert not set(token) & set("+/=")
        assert len(token) < MAX_CURSOR_LENGTH

    def test_known_token_decodes_to_the_documented_json_shape(self):
        token = encode_cursor(FeedCursor(PHASE_FOLLOWING, TS, "post", 12))
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        assert json.loads(raw) == _following_payload()


class TestFeedCursorConstruction:
    def test_naive_datetime_is_rejected(self):
        with pytest.raises(ValueError):
            FeedCursor(PHASE_FOLLOWING, datetime(2026, 1, 1, 12, 0, 0), "post", 1)

    def test_unknown_phase_is_rejected(self):
        with pytest.raises(ValueError):
            FeedCursor("x", TS, "post", 1)

    def test_unknown_content_type_is_rejected(self):
        with pytest.raises(ValueError):
            FeedCursor(PHASE_FOLLOWING, TS, "story", 1)

    @pytest.mark.parametrize("bad_id", [0, -1, True, "5", 5.0, None])
    def test_bad_object_id_is_rejected(self, bad_id):
        with pytest.raises(ValueError):
            FeedCursor(PHASE_FOLLOWING, TS, "post", bad_id)

    def test_backfill_cursor_requires_is_featured(self):
        with pytest.raises(ValueError):
            FeedCursor(PHASE_BACKFILL, TS, "post", 1)

    def test_following_cursor_must_not_carry_is_featured(self):
        with pytest.raises(ValueError):
            FeedCursor(PHASE_FOLLOWING, TS, "post", 1, is_featured=False)


class TestDecodeRejectsMalformedTokens:
    @pytest.mark.parametrize(
        "token",
        [
            "",
            None,
            123,
            "!!!not-base64!!!",
            "abc def",
            "a" * (MAX_CURSOR_LENGTH + 1),
            "A",  # impossible base64 length
            "bm90LWpzb24",  # base64 of "not-json"
            _token([1, 2, 3]),  # JSON, but not an object
            _token("just a string"),
            _token(None),
        ],
    )
    def test_garbage_tokens(self, token):
        with pytest.raises(InvalidCursorError):
            decode_cursor(token)

    def test_non_utf8_bytes_are_rejected(self):
        token = base64.urlsafe_b64encode(b"\xff\xfe\xfd").rstrip(b"=").decode()
        with pytest.raises(InvalidCursorError):
            decode_cursor(token)

    @pytest.mark.parametrize("version", [0, 2, "1", True, None, 1.0])
    def test_wrong_version(self, version):
        with pytest.raises(InvalidCursorError):
            decode_cursor(_token(_following_payload(v=version)))

    @pytest.mark.parametrize("phase", ["x", "", "F", None, 1, ["f"]])
    def test_unknown_phase(self, phase):
        with pytest.raises(InvalidCursorError):
            decode_cursor(_token(_following_payload(p=phase)))

    @pytest.mark.parametrize("missing", ["v", "p", "ts", "t", "id"])
    def test_missing_key_following(self, missing):
        payload = _following_payload()
        del payload[missing]
        with pytest.raises(InvalidCursorError):
            decode_cursor(_token(payload))

    def test_extra_key_is_rejected(self):
        with pytest.raises(InvalidCursorError):
            decode_cursor(_token(_following_payload(extra=1)))

    def test_following_cursor_with_ft_is_rejected(self):
        with pytest.raises(InvalidCursorError):
            decode_cursor(_token(_following_payload(ft=1)))

    def test_backfill_cursor_without_ft_is_rejected(self):
        payload = _backfill_payload()
        del payload["ft"]
        with pytest.raises(InvalidCursorError):
            decode_cursor(_token(payload))

    @pytest.mark.parametrize("ft", [2, -1, "1", True, False, None, 1.0, [1]])
    def test_bad_ft(self, ft):
        with pytest.raises(InvalidCursorError):
            decode_cursor(_token(_backfill_payload(ft=ft)))

    @pytest.mark.parametrize(
        "ts",
        [
            "not-a-date",
            "",
            "2026-09-24",  # date only -> naive
            "2026-09-24T09:41:09",  # no timezone -> naive
            12345,
            None,
            ["x"],
        ],
    )
    def test_bad_timestamp(self, ts):
        with pytest.raises(InvalidCursorError):
            decode_cursor(_token(_following_payload(ts=ts)))

    @pytest.mark.parametrize("content_type", ["story", "", "POST", None, 1, ["post"]])
    def test_bad_content_type(self, content_type):
        with pytest.raises(InvalidCursorError):
            decode_cursor(_token(_following_payload(t=content_type)))

    @pytest.mark.parametrize("object_id", [0, -5, "12", 12.0, True, None, [12]])
    def test_bad_object_id(self, object_id):
        with pytest.raises(InvalidCursorError):
            decode_cursor(_token(_following_payload(id=object_id)))

    def test_valid_payloads_from_the_helpers_do_decode(self):
        # Guards the helpers themselves: if these stopped decoding, every
        # "rejects X" test above would be passing for the wrong reason.
        assert decode_cursor(_token(_following_payload())).phase == PHASE_FOLLOWING
        assert decode_cursor(_token(_backfill_payload())).phase == PHASE_BACKFILL
