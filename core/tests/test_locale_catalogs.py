"""
Tests for the committed gettext catalogs and locale wiring (Part P-112).
"""

from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client
from django.utils import translation

from core.i18n_tools import build_mo, compile_po_file, parse_po

LOCALE_ROOT = Path(settings.BASE_DIR) / "locale"


@pytest.mark.parametrize("language", ["ar", "en"])
def test_committed_mo_matches_its_po(language):
    messages = LOCALE_ROOT / language / "LC_MESSAGES"
    expected = compile_po_file(messages / "django.po")

    assert (
        messages / "django.mo"
    ).read_bytes() == expected, (
        "django.mo is stale: run `python -m core.i18n_tools` and commit it."
    )


def test_both_catalogs_list_the_same_msgids():
    def msgids(language):
        text = (LOCALE_ROOT / language / "LC_MESSAGES" / "django.po").read_text(
            encoding="utf-8"
        )
        return {line for line in text.splitlines() if line.startswith("msgid ")}

    assert msgids("ar") == msgids("en")


def test_no_arabic_entry_is_empty_or_fuzzy():
    text = (LOCALE_ROOT / "ar" / "LC_MESSAGES" / "django.po").read_text(
        encoding="utf-8"
    )
    assert "fuzzy" not in text
    msgid_lines = [line for line in text.splitlines() if line.startswith("msgid ")]
    # Every msgid (the header included) must have a non-empty translation.
    assert len(parse_po(text)) == len(msgid_lines)


def test_parse_and_build_roundtrip_handles_multiline_and_escapes():
    entries = parse_po(
        'msgid ""\nmsgstr "H"\n\n'
        'msgid "a\\nb"\nmsgstr ""\n"x"\n"y"\n\n'
        'msgid "skip"\nmsgstr ""\n\n'
    )
    assert entries == {"": "H", "a\nb": "xy"}
    assert build_mo(entries)[:4] == b"\xde\x12\x04\x95"


def test_settings_declare_exactly_arabic_and_english():
    assert [code for code, _name in settings.LANGUAGES] == ["en", "ar"]
    assert settings.LANGUAGE_CODE == "en"
    assert Path(settings.LOCALE_PATHS[0]) == LOCALE_ROOT
    assert "django.middleware.locale.LocaleMiddleware" in settings.MIDDLEWARE


def test_locale_middleware_runs_after_sessions_and_before_common():
    middleware = settings.MIDDLEWARE
    locale = middleware.index("django.middleware.locale.LocaleMiddleware")
    assert middleware.index("django.contrib.sessions.middleware.SessionMiddleware") < (
        locale
    )
    assert locale < middleware.index("django.middleware.common.CommonMiddleware")


@pytest.mark.django_db
def test_content_language_header_follows_accept_language():
    client = Client()
    arabic = client.get(
        "/api/v1/categories/tree/", HTTP_ACCEPT_LANGUAGE="ar-EG,en;q=0.5"
    )
    english = client.get("/api/v1/categories/tree/", HTTP_ACCEPT_LANGUAGE="en")
    unsupported = client.get("/api/v1/categories/tree/", HTTP_ACCEPT_LANGUAGE="fr")

    assert arabic["Content-Language"] == "ar"
    assert english["Content-Language"] == "en"
    assert unsupported["Content-Language"] == "en"


def test_translation_override_uses_project_catalog():
    with translation.override("ar"):
        assert translation.gettext("New follower") == "متابع جديد"
    with translation.override("en"):
        assert translation.gettext("New follower") == "New follower"
