from django.test import RequestFactory

from core.i18n import get_request_language, parse_accept_language


def _request(header=None):
    extra = {}
    if header is not None:
        extra["HTTP_ACCEPT_LANGUAGE"] = header
    return RequestFactory().get("/", **extra)


def test_missing_header_falls_back_to_english():
    assert get_request_language(_request()) == "en"


def test_arabic_is_selected():
    assert get_request_language(_request("ar")) == "ar"


def test_region_subtag_is_ignored():
    assert get_request_language(_request("ar-EG")) == "ar"
    assert get_request_language(_request("en-US")) == "en"


def test_quality_values_decide_the_winner():
    assert get_request_language(_request("en;q=0.5, ar;q=0.9")) == "ar"
    assert get_request_language(_request("ar;q=0.2, en;q=0.8")) == "en"


def test_zero_quality_is_skipped():
    assert get_request_language(_request("ar;q=0, en")) == "en"


def test_unsupported_language_falls_back_to_english():
    assert get_request_language(_request("fr-FR, de;q=0.8")) == "en"


def test_wildcard_and_garbage_are_ignored():
    assert get_request_language(_request("*")) == "en"
    assert get_request_language(_request(";;;,,,")) == "en"
    assert get_request_language(_request("ar;q=abc")) == "en"


def test_first_supported_language_wins_when_unsupported_comes_first():
    assert get_request_language(_request("fr, ar;q=0.9, en;q=0.8")) == "ar"


def test_parse_returns_primary_tags_best_first():
    assert parse_accept_language("ar-EG,ar;q=0.9,en;q=0.8") == ["ar", "ar", "en"]
    assert parse_accept_language("") == []
    assert parse_accept_language(None) == []
