from categories.models import Category


def build_category_tree():
    """Build the full active category tree as nested plain dicts:
    ``[{"id", "name", "slug", "children": [...]}, ...]``.

    This is the ``compute_fn`` passed to ``core.cache.cache_get_or_set``
    by ``categories.views.CategoryTreeView`` — it is only ever called on
    a cache miss (or right after an Admin edit invalidates the cache via
    ``categories/signals.py``).

    Deliberately a single flat query (`Category.objects.filter(...)`)
    plus an in-memory tree build, not one query per level/node — a
    naive recursive-ORM-call version would be an N+1 query per branch,
    which is exactly what the ~1h cache TTL is there to make cheap to
    avoid paying repeatedly.
    """
    categories = list(
        Category.objects.filter(is_active=True)
        .order_by("name")
        .values("id", "name", "slug", "parent_id")
    )

    nodes_by_id = {
        row["id"]: {
            "id": row["id"],
            "name": row["name"],
            "slug": row["slug"],
            "children": [],
        }
        for row in categories
    }

    roots = []
    for row in categories:
        node = nodes_by_id[row["id"]]
        parent_id = row["parent_id"]
        if parent_id is not None and parent_id in nodes_by_id:
            nodes_by_id[parent_id]["children"].append(node)
        else:
            # Either a genuine root category, or its parent is inactive
            # (filtered out above) — either way it surfaces at the top
            # level rather than silently vanishing from the tree.
            roots.append(node)

    return roots
