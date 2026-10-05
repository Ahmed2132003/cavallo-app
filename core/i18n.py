"""
Language resolution helpers (Part P-112).

The mobile client sends ``Accept-Language`` on every request (Dio
interceptor, Flutter side). Server code that must return localized
content (category names today, anything else later) asks this module
which of the two supported languages to use instead of parsing the
header itself.

Rules, deliberately small and predictable:

* Only Arabic (``ar``) and English (``en``) are supported.
* Region subtags are ignored (``ar-EG`` -> ``ar``).
* Quality values are honoured, highest first; ``q=0`` means "not
  acceptable" and is skipped.
* Anything unsupported, missing or malformed falls back to English.
"""

LANGUAGE_AR = "ar"
LANGUAGE_EN = "en"
SUPPORTED_LANGUAGES = (LANGUAGE_AR, LANGUAGE_EN)
DEFAULT_LANGUAGE = LANGUAGE_EN


def parse_accept_language(header):
    """Return the primary language tags of ``header``, best first."""
    if not header:
        return []

    weighted = []
    for position, part in enumerate(str(header).split(",")):
        pieces = part.strip().split(";")
        tag = pieces[0].strip().lower()
        if not tag or tag == "*":
            continue

        quality = 1.0
        for param in pieces[1:]:
            name, _, value = param.strip().partition("=")
            if name.strip().lower() == "q":
                try:
                    quality = float(value.strip())
                except ValueError:
                    quality = 0.0

        if quality <= 0:
            continue
        weighted.append((-quality, position, tag.split("-")[0]))

    weighted.sort()
    return [tag for _quality, _position, tag in weighted]


def get_request_language(request, default=DEFAULT_LANGUAGE):
    """Pick ``ar`` or ``en`` for a DRF/Django request."""
    header = request.META.get("HTTP_ACCEPT_LANGUAGE", "")
    for tag in parse_accept_language(header):
        if tag in SUPPORTED_LANGUAGES:
            return tag
    return default
