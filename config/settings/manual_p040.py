"""
TEMPORARY, UNCOMMITTED settings for the Part P-040 manual verification.

Never commit this file. It exists only because Phase 7 (Post/Reel) has not
been built yet, so the real dev database contains no Moderatable content
at all and the moderator UI has nothing to review. Inheriting dev.py and
adding the repo's own throwaway `DummyContent(Moderatable)` test model
(moderation/tests/testapp, Part P-036) gives the running dev server one
real, table-backed content type to enqueue, approve and reject.

Delete this file (and docker-compose.p040.yml) when the verification is
finished — see p040_cleanup.py.
"""

from .dev import *  # noqa: F401,F403

INSTALLED_APPS = INSTALLED_APPS + ["moderation.tests.testapp"]  # noqa: F405