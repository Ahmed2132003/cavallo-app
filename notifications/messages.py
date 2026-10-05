"""
Notification wording catalog (Part P-112).

``render_notification`` turns (notification_type, params) into a
(title, body) pair in ONE language, using Django's gettext catalogs
(locale/<lang>/LC_MESSAGES/django.po). It is called by
notifications.tasks.dispatch_notification inside
``translation.override(<language>)``.

Rules:

* Every English string below is a literal ``gettext`` msgid so the
  catalogs can be audited with grep. Sentences are complete (no
  fragment concatenation), because Arabic word order and gender
  agreement cannot be assembled from English pieces.
* User-generated text (comment text, chat message text, a moderator's
  rejection reason) arrives in ``params`` and is inserted AS WRITTEN -
  it is never looked up in a catalog and never translated.
* A builder returns None when ``params`` lack what it needs; the caller
  then keeps the title/body the event source supplied (English), so a
  malformed event can never lose a notification.
"""

from django.utils import translation
from django.utils.translation import gettext as _

from core.i18n import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES


def _new_follower(params):
    return _("New follower"), _("Someone started following your business.")


def _comment_on_content(params):
    text = params.get("text")
    if text is None:
        return None
    return _("New comment"), str(text)


def _chat_message(params):
    title = _("New message")
    text = params.get("text")
    if text:
        return title, str(text)
    media = params.get("media")
    if media == "image":
        return title, _("Sent a photo")
    if media == "video":
        return title, _("Sent a video")
    shared = params.get("shared")
    if shared == "post":
        return title, _("Shared a post")
    if shared == "reel":
        return title, _("Shared a reel")
    if shared == "product":
        return title, _("Shared a product")
    return None


def _new_like(params):
    return _("New like"), _("Someone liked your content.")


def _new_share(params):
    return _("New share"), _("Someone shared your content.")


def _new_rating(params):
    return _("New rating"), _("Someone rated your business.")


def _moderation_approved(params):
    item = params.get("item")
    if item == "post":
        return _("Your post was approved"), _("Your post is now published.")
    if item == "reel":
        return _("Your reel was approved"), _("Your reel is now published.")
    if item == "story":
        return _("Your story was approved"), _("Your story is now published.")
    return _("Your content was approved"), _("Your content is now published.")


def _moderation_rejected(params):
    item = params.get("item")
    if item == "post":
        title = _("Your post was rejected")
    elif item == "reel":
        title = _("Your reel was rejected")
    elif item == "story":
        title = _("Your story was rejected")
    else:
        title = _("Your content was rejected")
    # The reason is typed by a moderator: inserted as written.
    return title, _("Reason: %(reason)s") % {"reason": params.get("reason", "")}


def _system_announcement(params):
    title = params.get("title")
    body = params.get("body")
    if title is None or body is None:
        return None
    return str(title), str(body)


_BUILDERS = {
    "new_follower": _new_follower,
    "comment_on_content": _comment_on_content,
    "chat_message": _chat_message,
    "new_like": _new_like,
    "new_share": _new_share,
    "new_rating": _new_rating,
    "moderation_approved": _moderation_approved,
    "moderation_rejected": _moderation_rejected,
    "system_announcement": _system_announcement,
}


def render_notification(notification_type, params, language):
    """
    Return ``(title, body)`` in ``language``, or None when the type is
    unknown or ``params`` are insufficient (caller falls back).

    ``language`` outside ar/en is treated as English.
    """
    builder = _BUILDERS.get(notification_type)
    if builder is None:
        return None
    if language not in SUPPORTED_LANGUAGES:
        language = DEFAULT_LANGUAGE
    with translation.override(language):
        return builder(params or {})


def render_for_all_languages(notification_type, params):
    """{language: (title, body)} for every supported language (may be {})."""
    rendered = {}
    for language in SUPPORTED_LANGUAGES:
        pair = render_notification(notification_type, params, language)
        if pair is not None:
            rendered[language] = pair
    return rendered
