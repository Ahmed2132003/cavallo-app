"""Tests for core.video_validation.validate_video_upload (reel upload fix)."""

import os

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from core.video_validation import validate_video_upload

_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "small_test_reel.mp4")
with open(_FIXTURE, "rb") as _f:
    _MP4 = _f.read()


def _upload(name, data, ctype="video/mp4"):
    return SimpleUploadedFile(name, data, content_type=ctype)


def test_real_mp4_is_accepted():
    validate_video_upload(_upload("a.mp4", _MP4))


def test_wrong_extension_and_content_type_still_accepted_when_really_video():
    validate_video_upload(_upload("clip.bin", _MP4, "application/octet-stream"))


def test_file_position_is_reset():
    f = _upload("a.mp4", _MP4)
    validate_video_upload(f)
    assert f.tell() == 0


def test_disguised_executable_is_rejected():
    exe = b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 600
    with pytest.raises(ValidationError) as exc:
        validate_video_upload(_upload("raw.mp4", exe))
    assert exc.value.code == "unsupported_file_type"


def test_plain_text_is_rejected():
    with pytest.raises(ValidationError):
        validate_video_upload(_upload("raw.mp4", b"just text, not a video"))


@override_settings(REEL_MAX_UPLOAD_BYTES=100)
def test_size_ceiling_comes_from_settings():
    with pytest.raises(ValidationError) as exc:
        validate_video_upload(_upload("a.mp4", _MP4))
    assert exc.value.code == "file_too_large"


def test_large_but_under_default_ceiling_is_not_rejected_for_size():
    # 150 MB is above the old 100 MB cap but below the new default.
    big = _MP4 + b"\0" * (150 * 1024 * 1024)
    validate_video_upload(_upload("big.mp4", big))
