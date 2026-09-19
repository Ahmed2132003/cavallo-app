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

PART P-033-HOTFIX: this file also splits the single boto3 connection
used by S3Boto3Storage into TWO separate connections:
  - ``self.connection`` (inherited, unchanged) — the INTERNAL endpoint
    (``OBJECT_STORAGE_ENDPOINT_URL``), used for every real upload/
    read/delete call the backend itself makes to the storage service.
  - ``self.public_connection`` (added below) — the PUBLIC-facing
    endpoint (``OBJECT_STORAGE_PUBLIC_ENDPOINT_URL``), used ONLY when
    signing a presigned URL to hand back to an API client (see the
    overridden ``url()`` method below).
Generating a presigned URL is a pure local cryptographic signing
operation — it never actually contacts the endpoint over the network —
so pointing the SIGNING client at a different (public) endpoint has no
effect whatsoever on the backend's own ability to talk to MinIO/S3
internally.
"""

import threading

from django.conf import settings
from django.utils.encoding import filepath_to_uri
from storages.backends.s3boto3 import S3Boto3Storage
from storages.utils import clean_name


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

    # PART P-033-HOTFIX: the PUBLIC-facing endpoint, used only by
    # ``public_connection``/``url()`` below — never by any inherited
    # upload/read/delete method, which all keep using ``endpoint_url``
    # above via the parent class's own ``self.connection``.
    public_endpoint_url = settings.OBJECT_STORAGE_PUBLIC_ENDPOINT_URL

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

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Mirrors the parent class's own ``self._connections`` /
        # ``self._unsigned_connections`` pattern (storages/backends/s3.py)
        # — one boto3 resource per thread, lazily created.
        self._public_connections = threading.local()

    @property
    def public_connection(self):
        """
        A second boto3 S3 resource, identical to the parent class's own
        ``self.connection`` (same credentials, region, SSL, verify —
        via the inherited ``self._create_session()``), except its
        ``endpoint_url`` is ``self.public_endpoint_url`` instead of
        ``self.endpoint_url``.

        Only ever used for signing inside ``url()`` below — never for
        an actual upload/read/delete call, all of which continue to go
        through the parent's own ``self.connection`` untouched.
        """
        connection = getattr(self._public_connections, "connection", None)
        if connection is None:
            session = self._create_session()
            connection = session.resource(
                "s3",
                region_name=self.region_name,
                use_ssl=self.use_ssl,
                endpoint_url=self.public_endpoint_url,
                config=self.client_config,
                verify=self.verify,
            )
            self._public_connections.connection = connection
        return connection

    def url(self, name, parameters=None, expire=None, http_method=None):
        """
        Overrides storages.backends.s3.S3Storage.url() (django-storages
        1.14.4) to sign presigned URLs using ``self.public_connection``
        (the PUBLIC endpoint) instead of ``self.connection`` (the
        INTERNAL endpoint) — see PART P-033-HOTFIX at the top of this
        file. Every other line below is an exact copy of the parent
        implementation; only which connection generates the signed URL
        changes.
        """
        name = self._normalize_name(clean_name(name))
        params = parameters.copy() if parameters else {}
        if expire is None:
            expire = self.querystring_expire

        if self.custom_domain:
            from urllib.parse import urlencode
            from datetime import datetime, timedelta

            url = "{}//{}/{}{}".format(
                self.url_protocol,
                self.custom_domain,
                filepath_to_uri(name),
                "?{}".format(urlencode(params)) if params else "",
            )

            if self.querystring_auth and self.cloudfront_signer:
                expiration = datetime.utcnow() + timedelta(seconds=expire)
                return self.cloudfront_signer.generate_presigned_url(
                    url, date_less_than=expiration
                )

            return url

        params["Bucket"] = self.bucket_name
        params["Key"] = name

        connection = (
            self.public_connection
            if self.querystring_auth
            else self.unsigned_connection
        )
        return connection.meta.client.generate_presigned_url(
            "get_object", Params=params, ExpiresIn=expire, HttpMethod=http_method
        )