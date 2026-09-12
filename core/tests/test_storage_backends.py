"""
Integration test for core.storage_backends.MediaStorage (Part P-013).

Ahmed: run this against the real `minio` Docker Compose service too —
`docker compose exec web pytest core/tests/test_storage_backends.py -v`
with the stack up — before treating this part as fully validated. See
this part's PROJECT_PROGRESS.md entry for why: no Docker daemon was
available in the authoring environment (same constraint P-000/P-010/
P-011/P-012 all hit), so this test was written and validated here
against a real local S3-compatible HTTP server (moto's server mode)
instead of the actual MinIO container — genuinely equivalent from
Django's point of view (real HTTP calls, a real custom endpoint_url,
no mocking of core.storage_backends itself), but the real MinIO
container itself was never booted.

`moto` is a test-only dependency, not baked into requirements.txt or
the Docker image — same convention as pytest/pytest-django/flake8/
black (see P-012's progress notes). Install it ad hoc before running
this file:

    docker compose exec web pip install "moto[server]"

This test is automatically skipped (not failed) if moto isn't
installed, so a bare `pytest`/`docker compose exec web pytest` run
without that ad hoc install still passes cleanly — it just won't cover
this file.
"""

import pytest

moto_server = pytest.importorskip("moto.server")
boto3 = pytest.importorskip("boto3")

from django.core.files.base import ContentFile  # noqa: E402

from core.storage_backends import MediaStorage  # noqa: E402


@pytest.fixture(scope="module")
def s3_test_server():
    """
    A real local S3-compatible HTTP server for the duration of this
    module's tests — genuine HTTP round trips (PUT/HEAD/GET/DELETE),
    not a fully in-process mock. Bound to 127.0.0.1 on an
    OS-assigned free port so this never conflicts with anything else
    running locally (including the real docker-compose `minio`
    service, if it happens to be up at the same time).
    """
    server = moto_server.ThreadedMotoServer(ip_address="127.0.0.1", port=0)
    server.start()
    host, port = server.get_host_and_port()
    yield f"http://{host}:{port}"
    server.stop()


@pytest.fixture()
def media_storage(s3_test_server):
    """
    A MediaStorage instance pointed at the test server via explicit
    constructor kwargs (which S3Boto3Storage's BaseStorage.__init__
    always applies, overriding the class's settings-derived defaults)
    — this deliberately does NOT depend on OBJECT_STORAGE_* being set
    in the test environment's .env, so this test runs the same way
    everywhere (Ahmed's machine, CI, wherever), unlike the real
    dev-database/dev-cache settings which do require services to be up.
    """
    bucket_name = "scd-test-media"
    region_name = "us-east-1"

    client = boto3.client(
        "s3",
        region_name=region_name,
        endpoint_url=s3_test_server,
        aws_access_key_id="test-access-key",
        aws_secret_access_key="test-secret-key",
    )
    client.create_bucket(Bucket=bucket_name)

    return MediaStorage(
        bucket_name=bucket_name,
        region_name=region_name,
        endpoint_url=s3_test_server,
        access_key="test-access-key",
        secret_key="test-secret-key",
        use_ssl=False,
    )


class TestMediaStorage:
    def test_save_and_retrieve_round_trip(self, media_storage):
        """
        Matches this part's acceptance criterion: a test upload
        succeeds and is retrievable via a generated URL.
        """
        content = b"fake-image-bytes-for-testing"
        saved_name = media_storage.save("products/test-image.png", ContentFile(content))

        assert media_storage.exists(saved_name)
        assert media_storage.size(saved_name) == len(content)

        url = media_storage.url(saved_name)
        assert url.startswith("http://")
        assert saved_name in url

        with media_storage.open(saved_name, "rb") as f:
            assert f.read() == content

    def test_file_overwrite_disabled_appends_suffix(self, media_storage):
        """
        file_overwrite = False must be honored: saving a second file
        under the same name gets a different key, and the first
        file's content is left untouched.
        """
        first = media_storage.save("stories/clip.mp4", ContentFile(b"first"))
        second = media_storage.save("stories/clip.mp4", ContentFile(b"second"))

        assert first != second
        with media_storage.open(first, "rb") as f:
            assert f.read() == b"first"
        with media_storage.open(second, "rb") as f:
            assert f.read() == b"second"

    def test_delete_removes_object(self, media_storage):
        saved_name = media_storage.save("reels/thumb.jpg", ContentFile(b"thumb"))
        assert media_storage.exists(saved_name)

        media_storage.delete(saved_name)

        assert not media_storage.exists(saved_name)
