"""
Generic media-upload validation utility (Part P-013).

``validate_upload()`` is the single choke point every future
upload-handling part (Products, Posts, Reels, Stories, chat media, ...)
must call before saving a file to any FileField/ImageField backed by
``core.storage_backends.MediaStorage``. This is the concrete
implementation of the architecture Section 28 "malicious file upload"
mitigation: it sniffs the file's *actual* content via libmagic rather
than trusting the filename extension or the browser-supplied
Content-Type, since both are trivially spoofable (rename ``evil.exe``
to ``evil.jpg`` and an extension-only check would pass it straight
through).

Integration note for future parts — read this before calling
validate_upload() from a serializer:

    Django REST Framework's own field/serializer validation machinery
    (``Field.run_validators`` and ``Serializer.to_internal_value``)
    already catches ``django.core.exceptions.ValidationError`` raised
    inside a ``validate_<field>()`` method or a ``validators=[...]``
    callable, and converts it into DRF's own
    ``rest_framework.exceptions.ValidationError`` — which is what
    Part P-012's ``core.exceptions.custom_exception_handler`` actually
    matches on to build the ``{"error": {"code": "VALIDATION_ERROR",
    ...}}`` envelope. This is exactly why this function deliberately
    raises Django's core ``ValidationError`` and not DRF's: it lets the
    same function be reused from a plain Django ``clean()``/form
    context too, not just DRF.

    Call it from serializer field validation (a ``validate_<field>()``
    method, or attach it via a field's ``validators=[...]``) so DRF's
    machinery performs that conversion automatically. Calling it
    directly from raw view code, outside any serializer/form
    validation path, will NOT get P-012's envelope for free — the
    caller would need to catch Django's ValidationError and re-raise it
    as ``rest_framework.exceptions.ValidationError`` itself in that
    case.
"""

import magic
from django.core.exceptions import ValidationError

# Reading more than this many bytes just to sniff a MIME type would be
# wasteful for large uploads; libmagic's own signatures are all well
# within the first few hundred bytes of a file, so 2 KB leaves a wide
# safety margin without ever loading a large file fully into memory.
_SNIFF_BYTES = 2048


def validate_upload(file, allowed_mime_types, max_size_bytes):
    """
    Validate an uploaded file's real content type and size.

    Args:
        file: a Django ``UploadedFile`` (``InMemoryUploadedFile`` or
            ``TemporaryUploadedFile`` — anything DRF/Django hands a
            serializer for a ``FileField``/``ImageField``), or any
            file-like object supporting ``.read()``/``.seek()``.
        allowed_mime_types: list[str] of acceptable MIME types, e.g.
            ``["image/jpeg", "image/png"]``.
        max_size_bytes: int, maximum allowed size in bytes.

    Raises:
        django.core.exceptions.ValidationError: if the file's size
            exceeds ``max_size_bytes`` (code ``"file_too_large"``), or
            its actual, content-sniffed MIME type is not in
            ``allowed_mime_types`` (code ``"unsupported_file_type"``).

    Returns:
        None on success — the caller proceeds to save the file as
        normal. This function only validates; it never modifies or
        returns the file itself.

    The file's read position is always reset to the start (``seek(0)``)
    before this function returns, whether it raises or not, so the
    caller's own subsequent ``save()``/``read()`` of the same file
    object is never affected by the sniffing read done here.
    """
    size = getattr(file, "size", None)
    if size is None:
        # Fallback for plain file-like objects without a Django
        # UploadedFile's .size attribute (e.g. a raw file opened with
        # open(..., "rb") in a test).
        current_position = file.tell()
        file.seek(0, 2)  # Seek to end.
        size = file.tell()
        file.seek(current_position)

    if size > max_size_bytes:
        raise ValidationError(
            "File is too large (%(size)d bytes). Maximum allowed size "
            "is %(max_size)d bytes.",
            code="file_too_large",
            params={"size": size, "max_size": max_size_bytes},
        )

    file.seek(0)
    file_head = file.read(_SNIFF_BYTES)
    file.seek(0)

    detected_mime = magic.from_buffer(file_head, mime=True)

    if detected_mime not in allowed_mime_types:
        raise ValidationError(
            "Unsupported file type: %(detected_mime)s. Allowed types "
            "are: %(allowed)s.",
            code="unsupported_file_type",
            params={
                "detected_mime": detected_mime,
                "allowed": ", ".join(allowed_mime_types),
            },
        )
