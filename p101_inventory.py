# p101_inventory.py - read-only: which Section 9 indexes EXIST (usage/EXPLAIN is Step 2).
import os
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
django.setup()
from django.db import connection

TABLES = ["content_post", "content_reel", "stories_story", "products_product", "businesses_businessprofile",
          "moderation_moderationqueue", "categories_category", "social_follow"]
SQL = """
SELECT t.relname, i.relname, am.amname, ix.indisvalid,
       array_agg(a.attname ORDER BY k.ord)
FROM pg_index ix
JOIN pg_class t ON t.oid = ix.indrelid
JOIN pg_class i ON i.oid = ix.indexrelid
JOIN pg_am am ON am.oid = i.relam
JOIN LATERAL unnest(ix.indkey::int2[]) WITH ORDINALITY AS k(attnum, ord) ON true
JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
WHERE t.relname = ANY(%s)
GROUP BY t.relname, i.relname, am.amname, ix.indisvalid
ORDER BY 1, 2
"""
EXPECT = [
    ("A1", "Post (business_id,status,created_at) [Sec.9 literal]", "content_post", ["business_id", "status", "created_at"], "btree"),
    ("A2", "Reel (business_id,status,created_at) [Sec.9 literal]", "content_reel", ["business_id", "status", "created_at"], "btree"),
    ("A3", "Story (business_id,status,created_at) [Sec.9 literal]", "stories_story", ["business_id", "status", "created_at"], "btree"),
    ("A4", "Story (business_id,status,expires_at) [as built, P-046]", "stories_story", ["business_id", "status", "expires_at"], "btree"),
    ("B1", "BusinessProfile (category_id,city) [Sec.9 literal]", "businesses_businessprofile", ["category_id", "city"], "btree"),
    ("B2", "Product (category_id,business_id) [as built, P-031]", "products_product", ["category_id", "business_id"], "btree"),
    ("C1", "GIN Product.search_vector", "products_product", ["search_vector"], "gin"),
    ("C2", "GIN BusinessProfile.search_vector", "businesses_businessprofile", ["search_vector"], "gin"),
    ("D1", "ModerationQueue (status)", "moderation_moderationqueue", ["status"], "btree"),
    ("D2", "ModerationQueue (content_type_id,object_id)", "moderation_moderationqueue", ["content_type_id", "object_id"], "btree"),
]
with connection.cursor() as cur:
    cur.execute("SHOW server_version")
    print("P101|PG_VERSION=%s" % cur.fetchone()[0])
    cur.execute(SQL, [TABLES])
    rows = cur.fetchall()
    for t, i, am, valid, cols in rows:
        print(f"P101|INDEX|{t}|{i}|{am}|valid={valid}|{','.join(cols)}")
    for code, label, table, cols, am in EXPECT:
        hit = [r for r in rows if r[0] == table and r[2] == am and list(r[4])[:len(cols)] == cols]
        if hit:
            exact = list(hit[0][4]) == cols
            print(f"P101|CHECK|{code}|{label}|{'FOUND-EXACT' if exact else 'FOUND-PREFIX'}|{hit[0][1]}")
        else:
            print(f"P101|CHECK|{code}|{label}|MISSING|-")
    for t in TABLES:
        cur.execute("SELECT n_live_tup, last_analyze, last_autoanalyze FROM pg_stat_user_tables WHERE relname=%s", [t])
        r = cur.fetchone()
        cur.execute(f"SELECT count(*), pg_size_pretty(pg_total_relation_size('{t}')) FROM {t}")
        c = cur.fetchone()
        print(f"P101|TABLE|{t}|rows={c[0]}|size={c[1]}|analyzed={'yes' if r and (r[1] or r[2]) else 'NO'}")
