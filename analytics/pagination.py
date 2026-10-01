from core.pagination import StandardCursorPagination


class DailyStatsCursorPagination(StandardCursorPagination):
    """
    Part P-084. Cursor pagination for the daily-stats list, newest day first.

    StandardCursorPagination orders by ``-created_at``; for daily stats the
    meaningful (and, per business, unique) ordering key is ``date``. Because
    (business, date) is unique and every request is scoped to ONE business,
    ``-date`` is a total order, so the cursor is stable with no tiebreaker.
    Backfilled/re-computed rows keep their place (their created_at/updated_at
    are irrelevant to the order).

    page_size 30 ~= one month of days per page; clients may pass
    ``?page_size=`` up to StandardCursorPagination's max_page_size (100).
    """

    page_size = 30
    ordering = "-date"