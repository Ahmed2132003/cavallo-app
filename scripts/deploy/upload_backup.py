"""Upload a database backup file to the project's object storage.

Part P-103. Run INSIDE the ``web`` container by
``scripts/deploy/backup_db.sh`` (so it reuses the container's
OBJECT_STORAGE_* environment, the same variables core/storage_backends.py
reads from P-013). Uses boto3 directly instead of Django's storage API
because the object key must be exactly ``backups/<environment>/<file>``.

Usage:
    python scripts/deploy/upload_backup.py <local-file> <object-key>

Exit code 0 only when the object exists in the bucket with the same size
as the local file. Any other result is a failure, and the deploy script
stops before migrations run.
"""

import os
import sys

import boto3
from botocore.config import Config

KEY_PREFIX = "backups/"
REQUIRED_ENV = (
    "OBJECT_STORAGE_BUCKET",
    "OBJECT_STORAGE_KEY",
    "OBJECT_STORAGE_SECRET",
)


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    return 1


def build_client():
    endpoint_url = os.environ.get("OBJECT_STORAGE_ENDPOINT_URL") or None
    s3_options = {"addressing_style": "path"} if endpoint_url else {}
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        region_name=os.environ.get("OBJECT_STORAGE_REGION") or None,
        aws_access_key_id=os.environ["OBJECT_STORAGE_KEY"],
        aws_secret_access_key=os.environ["OBJECT_STORAGE_SECRET"],
        config=Config(signature_version="s3v4", s3=s3_options),
    )


def main(argv):
    if len(argv) != 3:
        return fail("usage: upload_backup.py <local-file> <object-key>")
    local_path, object_key = argv[1], argv[2]

    if not object_key.startswith(KEY_PREFIX):
        return fail(f"object key must start with '{KEY_PREFIX}'")
    if not os.path.isfile(local_path):
        return fail(f"backup file not found: {local_path}")
    local_size = os.path.getsize(local_path)
    if local_size == 0:
        return fail(f"backup file is empty: {local_path}")

    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        return fail("missing environment variables: " + ", ".join(missing))

    bucket = os.environ["OBJECT_STORAGE_BUCKET"]
    client = build_client()
    client.upload_file(local_path, bucket, object_key)

    remote_size = client.head_object(Bucket=bucket, Key=object_key)["ContentLength"]
    if remote_size != local_size:
        return fail(f"size mismatch: local {local_size}, remote {remote_size}")

    print(f"Uploaded {local_size} bytes to s3://{bucket}/{object_key}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
