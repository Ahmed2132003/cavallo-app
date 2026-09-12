"""
S3-compatible object storage backend (Part P-013).

Architecture Section 19 forbids ever storing media on local VPS disk.
This wraps django-storages' S3Boto3Storage so ``STORAGES["default"]``
(config/settings/base.py) works identically against any S3-compatible
provider — Backblaze B2, Wasabi, DigitalOcean Spaces, or (in dev/test)
the local MinIO container added in this same part — purely by changing
the seven ``OBJECT_STORAGE_*`` env vars documented in ``.env.example``
and ``CONFIG.md``. No code in this file, or anywhere else, needs to
change to swap providers.

Real bucket/credentials are still pending a budget decision
(architecture Section 7, item 2 — Backblaze B2 vs Wasabi vs DO Spaces).
This does NOT block dev/test: the dev defaults point at the MinIO
service in docker-compose.yml, so this backend is fully functional
today. It DOES still block staging/production (Phase 21/22) until the
real values are provided.
"""

from django.conf import settings
from storages.backends.s3boto3 import S3Boto3Storage


class MediaStorage(S3Boto3Storage):
    """
    Default file storage for every FileField/ImageField in the project.

    Every connection detail is a class attribute sourced from Django
    settings (themselves read from the OBJECT_STORAGE_* env vars in
    config/settings/base.py) — never hardcoded here, so dev, test,
    staging, and prod can each point at a completely different
    provider/bucket without this file changing at all. Class attributes
    on a django-storages backend take precedence over its own
    AWS_*-setting-based defaults (verified against django-storages
    1.14's storages.base.BaseStorage.__init__), which is exactly the
    override this class relies on.

    Future parts (Products, Posts, Reels, Stories, chat media, ...)
    should point their FileField/ImageField's ``storage=`` at an
    instance of this class (or simply rely on it being
    STORAGES["default"], which is the Django-wide default every
    FileField uses unless told otherwise) — never construct a
    django-storages backend directly, and never add a second storage
    class unless a genuinely different bucket/provider is needed for
    that content type.
    """

    bucket_name = settings.OBJECT_STORAGE_BUCKET
    region_name = settings.OBJECT_STORAGE_REGION
    endpoint_url = settings.OBJECT_STORAGE_ENDPOINT_URL
    access_key = settings.OBJECT_STORAGE_KEY
    secret_key = settings.OBJECT_STORAGE_SECRET
    use_ssl = settings.OBJECT_STORAGE_USE_SSL

    # Signed URLs by default: works out of the box against every
    # provider (Backblaze B2, Wasabi, DO Spaces, MinIO) without first
    # requiring a public-read bucket policy to be configured on the
    # real provider once Section 7 item 2 is resolved. Revisit only if
    # a deliberate "public bucket" decision is made later.
    querystring_auth = True
    querystring_expire = 3600  # Signed URLs are valid for 1 hour.

    # Never silently overwrite an existing key on a name collision;
    # django-storages appends a random suffix instead. Every future
    # upload-handling part can rely on this rather than re-deciding it.
    file_overwrite = False

    # "path"-style addressing (bucket in the URL path, not a
    # bucket.subdomain) is required for MinIO and most non-AWS
    # S3-compatible providers — virtual-hosted-style addressing (boto3's
    # own default) does not work against them. AWS S3 itself accepts
    # path-style too, so this is safe across every provider this class
    # will ever point at.
    addressing_style = "path"
