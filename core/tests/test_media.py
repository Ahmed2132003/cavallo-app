"""
Unit tests for core.media.validate_upload() (Part P-013).

No external service is needed for these — python-magic/libmagic is a
real dependency baked into the Docker image (see the Dockerfile's
libmagic1 apt package), so these tests exercise genuine content
sniffing against real file bytes, not a mocked magic module.
"""

import struct

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from core.media import validate_upload

# A minimal, genuinely valid PNG: signature + a well-formed IHDR chunk.
# libmagic identifies this as image/png from the signature bytes alone,
# so the trailing padding content is irrelevant to detection.
_VALID_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    + struct.pack(">I", 13)
    + b"IHDR"
    + struct.pack(">IIBBBBB", 10, 10, 8, 6, 0, 0, 0)
    + b"0000"
    + b"0" * 100
)

# A Windows PE executable's real signature ("MZ" + DOS stub), which is
# what libmagic actually keys off, regardless of the filename/extension
# or the browser-supplied Content-Type below — both of which claim this
# is a JPEG. This is the exact "disguised executable" case Architecture
# Section 28 requires validate_upload() to catch.
_DISGUISED_EXE_BYTES = (
    b"MZ" + b"\x90\x00" * 30 + b"This program cannot be run in DOS mode" + b"0" * 200
)


class TestValidateUpload:
    def test_valid_file_passes(self):
        """A real PNG within the size limit raises nothing."""
        upload = SimpleUploadedFile(
            "photo.png", _VALID_PNG_BYTES, content_type="image/png"
        )

        # Should not raise.
        validate_upload(
            upload,
            allowed_mime_types=["image/png", "image/jpeg"],
            max_size_bytes=1024 * 1024,
        )

    def test_valid_file_read_position_reset(self):
        """
        The caller's own subsequent read/save of the file must not be
        affected by the sniffing read performed internally.
        """
        upload = SimpleUploadedFile(
            "photo.png", _VALID_PNG_BYTES, content_type="image/png"
        )

        validate_upload(
            upload,
            allowed_mime_types=["image/png"],
            max_size_bytes=1024 * 1024,
        )

        assert upload.tell() == 0
        assert upload.read() == _VALID_PNG_BYTES

    def test_spoofed_extension_is_rejected_by_content_sniffing(self):
        """
        An executable renamed to .jpg (correct extension, correct
        claimed Content-Type, wrong actual content) must be rejected —
        this is the concrete case Architecture Section 28 names.
        """
        upload = SimpleUploadedFile(
            "totally_a_photo.jpg",
            _DISGUISED_EXE_BYTES,
            content_type="image/jpeg",
        )

        with pytest.raises(ValidationError) as exc_info:
            validate_upload(
                upload,
                allowed_mime_types=["image/jpeg", "image/png"],
                max_size_bytes=1024 * 1024,
            )

        assert exc_info.value.code == "unsupported_file_type"
        # The interpolated message (what DRF's get_error_detail() will
        # actually show the client) names the real detected type.
        assert "application/x-dosexec" in str(exc_info.value)

    def test_oversized_file_is_rejected(self):
        """A file exceeding max_size_bytes is rejected, regardless of
        whether its content type would otherwise be allowed."""
        upload = SimpleUploadedFile(
            "big.png",
            _VALID_PNG_BYTES + b"0" * 5000,
            content_type="image/png",
        )

        with pytest.raises(ValidationError) as exc_info:
            validate_upload(
                upload,
                allowed_mime_types=["image/png"],
                max_size_bytes=100,
            )

        assert exc_info.value.code == "file_too_large"

    def test_disallowed_but_genuine_mime_type_is_rejected(self):
        """
        A real, correctly-identified file type that simply isn't in
        the caller's allow-list (e.g. a GIF where only PNG/JPEG are
        wanted) is rejected the same way a spoofed file would be —
        allowed_mime_types is an allow-list, not just an anti-spoofing
        check.
        """
        gif_bytes = b"GIF89a" + b"0" * 100
        upload = SimpleUploadedFile("photo.gif", gif_bytes, content_type="image/gif")

        with pytest.raises(ValidationError) as exc_info:
            validate_upload(
                upload,
                allowed_mime_types=["image/png", "image/jpeg"],
                max_size_bytes=1024 * 1024,
            )

        assert exc_info.value.code == "unsupported_file_type"
