"""
Permissive-but-safe validation for user-uploaded VIDEO (Reels).

Why this exists: ``core.media.validate_upload`` is an allow-list of exact
MIME types. For video that rejects perfectly good phone recordings (3GP,
M4V, MKV, AVI, MP4 files whose brand libmagic reports as
``application/octet-stream`` ...) and caps the size at 100 MB, which a
one-minute 4K clip already exceeds.

Rules here:
  1. Size ceiling comes from ``settings.REEL_MAX_UPLOAD_BYTES`` (default 2 GB).
  2. Anything libmagic sniffs as ``video/*`` is accepted.
  3. If libmagic says something ambiguous (octet-stream, application/mp4, ...)
     the file is probed with ffprobe and accepted only if it really contains a
     video stream. An executable renamed to .mp4 is still rejected.
The real format normalisation happens later in ``content.tasks.transcode_reel``
(everything becomes H.264/AAC MP4), so accepting more input formats is safe.
"""

import json
import os
import shutil
import subprocess
import tempfile

import magic
from django.conf import settings
from django.core.exceptions import ValidationError

_SNIFF_BYTES = 4096
_AMBIGUOUS_MIME_TYPES = {
    "application/octet-stream",
    "application/mp4",
    "application/x-matroska",
    "application/ogg",
    "application/vnd.apple.mpegurl",
    "audio/mp4",
    "audio/x-m4a",
}
_PROBE_TIMEOUT_SECONDS = 30


def _max_bytes():
    return int(getattr(settings, "REEL_MAX_UPLOAD_BYTES", 2 * 1024 * 1024 * 1024))


def _has_video_stream(file):
    """True iff ffprobe finds at least one video stream in ``file``."""
    path = None
    cleanup = None
    try:
        temp_path = getattr(file, "temporary_file_path", None)
        if callable(temp_path):
            path = temp_path()
        else:
            handle = tempfile.NamedTemporaryFile(delete=False)
            cleanup = handle.name
            file.seek(0)
            shutil.copyfileobj(file, handle)
            handle.close()
            path = handle.name
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=codec_type", "-of", "json", path,
            ],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            return False
        streams = json.loads(result.stdout or "{}").get("streams", [])
        return any(s.get("codec_type") == "video" for s in streams)
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    finally:
        file.seek(0)
        if cleanup and os.path.exists(cleanup):
            os.unlink(cleanup)


def validate_video_upload(file):
    """Raise Django ``ValidationError`` unless ``file`` is a real video."""
    max_size = _max_bytes()
    size = getattr(file, "size", None)
    if size is None:
        pos = file.tell()
        file.seek(0, 2)
        size = file.tell()
        file.seek(pos)

    if size > max_size:
        raise ValidationError(
            "Video is too large (%(size)d MB). Maximum allowed size is "
            "%(max_size)d MB.",
            code="file_too_large",
            params={"size": size // (1024 * 1024), "max_size": max_size // (1024 * 1024)},
        )

    file.seek(0)
    head = file.read(_SNIFF_BYTES)
    file.seek(0)
    detected = magic.from_buffer(head, mime=True)

    if detected.startswith("video/"):
        return
    if detected in _AMBIGUOUS_MIME_TYPES and _has_video_stream(file):
        return

    raise ValidationError(
        "Unsupported file type: %(detected_mime)s. Please upload a video file.",
        code="unsupported_file_type",
        params={"detected_mime": detected},
    )
