"""
Part P-042: async video transcoding pipeline for Reel.

Raw video uploaded by a business is not, by itself, meaningful for a
moderator to review (wrong container/codec for playback in the
moderator UI, potentially huge, no thumbnail to show in a list). This
task is the ONLY place Reel.video/Reel.thumbnail/Reel.duration_seconds
and Reel.processing_status are ever written after initial upload, and
it is the ONLY place a Reel's ModerationQueue row is ever created —
see content/models.py's Reel docstring and moderation/models.py's
Moderatable.auto_enqueue_on_create for the full deferred-enqueue
mechanism this task is the other half of.

Uses the real ffmpeg/ffprobe binaries via subprocess (Dockerfile
installs the `ffmpeg` system package, which also provides `ffprobe`) —
deliberately not a Python ffmpeg-wrapper library, and deliberately
never mocked in this app's own tests, per this part's own acceptance
criteria: a mocked subprocess call would not catch a real
command-syntax bug in the actual ffmpeg invocation.
"""

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from celery import shared_task
from django.contrib.contenttypes.models import ContentType
from django.core.files import File

from content.models import Reel
from moderation.models import ModerationQueue

logger = logging.getLogger(__name__)

# --- Tunable transcoding parameters -------------------------------------
# Change these constants only — do not bury magic numbers in the ffmpeg
# command construction below (same convention as moderation/tasks.py's
# FAST_PATH_SLA_MINUTES/NORMAL_SLA_HOURS).
MAX_HEIGHT_PX = 1080          # Never upscale a smaller source video.
VIDEO_BITRATE = "2M"
AUDIO_BITRATE = "128k"
THUMBNAIL_SECOND = 1          # Extract the thumbnail at this many seconds in.
FFMPEG_TIMEOUT_SECONDS = 300  # Generous ceiling for a single Reel's transcode.
# --------------------------------------------------------------------------


@shared_task(name="content.transcode_reel")
def transcode_reel(reel_id):
    """
    Transcode a freshly-uploaded Reel's raw video, extract a thumbnail,
    and — only on success — enqueue it into moderation.

    Flow: uploaded -> processing -> ready (queue row created) OR
                                  -> failed (no queue row, error logged).

    Idempotency note: this task is not safe to run concurrently for the
    same reel_id (two workers racing to write processing_status/video/
    thumbnail). It IS safe to run again sequentially after a failure —
    re-running simply repeats the same deterministic pipeline and, on a
    second success, `ModerationQueue.objects.get_or_create(...)` below
    will not create a duplicate row.
    """
    try:
        reel = Reel.objects.get(pk=reel_id)
    except Reel.DoesNotExist:
        # The Reel was hard-deleted (or never existed) between dispatch
        # and execution. Nothing to process, nothing to log as an
        # error — just stop. Not silently swallowed: still visible as
        # a WARNING in the worker's own logs.
        logger.warning(
            "transcode_reel_missing_reel",
            extra={"event": "transcode_reel_missing_reel", "reel_id": reel_id},
        )
        return

    reel.processing_status = Reel.ProcessingStatus.PROCESSING
    reel.save(update_fields=["processing_status"])

    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            input_path = tmp_dir / "input"
            output_path = tmp_dir / "output.mp4"
            thumbnail_path = tmp_dir / "thumbnail.jpg"

            # 1. Download the raw upload from object storage (P-013's
            #    MediaStorage) to a local temp file — ffmpeg needs a
            #    real filesystem path, not a remote storage handle.
            with reel.video.open("rb") as source, open(input_path, "wb") as dest:
                shutil.copyfileobj(source, dest)

            # 2. Transcode: normalize to a max resolution/bitrate.
            #    scale=-2:min(ih,MAX_HEIGHT_PX) never upscales a source
            #    already smaller than the cap; -2 keeps width even
            #    (required by yuv420p/libx264).
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(input_path),
                    "-vf",
                    f"scale=-2:min(ih\\,{MAX_HEIGHT_PX})",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-pix_fmt",
                    "yuv420p",
                    "-b:v",
                    VIDEO_BITRATE,
                    "-c:a",
                    "aac",
                    "-b:a",
                    AUDIO_BITRATE,
                    "-movflags",
                    "+faststart",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                timeout=FFMPEG_TIMEOUT_SECONDS,
            )

            # 3. Extract a thumbnail frame from the NORMALIZED output
            #    (guarantees the thumbnail always matches what a
            #    moderator/customer will actually play back).
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    str(THUMBNAIL_SECOND),
                    "-i",
                    str(output_path),
                    "-frames:v",
                    "1",
                    str(thumbnail_path),
                ],
                check=True,
                capture_output=True,
                timeout=FFMPEG_TIMEOUT_SECONDS,
            )

            # 4. Real duration via ffprobe (never trust client-supplied
            #    metadata for this).
            probe = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=FFMPEG_TIMEOUT_SECONDS,
            )
            duration_seconds = round(float(probe.stdout.strip()))

            # 5. Upload both results back through the same storage
            #    backend (P-013) the raw upload came from. save=False
            #    on each: a single reel.save() below writes both new
            #    field values plus processing_status together.
            with open(output_path, "rb") as f:
                reel.video.save(f"{reel.pk}_transcoded.mp4", File(f), save=False)
            with open(thumbnail_path, "rb") as f:
                reel.thumbnail.save(f"{reel.pk}_thumb.jpg", File(f), save=False)

            reel.duration_seconds = duration_seconds
            reel.processing_status = Reel.ProcessingStatus.READY
            reel.save()

    except Exception:
        # Deliberately broad: a bad input file can fail inside ffmpeg
        # (CalledProcessError), ffprobe (same), or the storage round
        # trip (boto3 errors) — all of them mean the same thing for
        # this Reel: transcoding did not succeed, and NOTHING below
        # (queue enqueue) must run. logger.exception() captures the
        # real traceback for whichever of those it was.
        logger.exception(
            "transcode_reel_failed",
            extra={"event": "transcode_reel_failed", "reel_id": reel_id},
        )
        reel.processing_status = Reel.ProcessingStatus.FAILED
        reel.save(update_fields=["processing_status"])
        return

    # 6. ONLY on success, and only here: the manual enqueue this whole
    #    part exists to build. get_or_create (not create) makes a
    #    sequential re-run after an earlier partial failure safe —
    #    see this function's own docstring.
    ModerationQueue.objects.get_or_create(
        content_type=ContentType.objects.get_for_model(Reel),
        object_id=reel.pk,
    )