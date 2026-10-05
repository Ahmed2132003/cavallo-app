"""
Pure-Python .po -> .mo compiler (Part P-112).

Why this exists: the Docker image (python:3.12-slim) does not ship GNU
gettext, so ``manage.py compilemessages`` cannot run there. The compiled
catalogs are committed instead, and this module regenerates them with no
system dependency:

    python -m core.i18n_tools    # recompile locale/*/LC_MESSAGES/django.po

A test (core/tests/test_locale_catalogs.py) fails when a committed .mo
does not match its .po, so the two can never drift apart silently.

Supported .po subset (all the project uses): msgid / msgstr pairs,
multi-line quoted strings, "#" comments, msgctxt is NOT supported,
plural forms are NOT supported, fuzzy entries are skipped, and entries
with an empty msgstr are skipped (gettext falls back to the msgid).
"""

import ast
import struct
import sys
from pathlib import Path

_MAGIC = 0x950412DE


def _unquote(line):
    return ast.literal_eval(line.strip())


def parse_po(text):
    """Return {msgid: msgstr} for the translated entries of ``text``."""
    entries = {}
    msgid = msgstr = None
    section = None
    fuzzy = False
    flags_fuzzy = False

    def flush():
        nonlocal msgid, msgstr, fuzzy
        if msgid is not None and msgstr is not None:
            if (msgstr and not fuzzy) or msgid == "":
                entries[msgid] = msgstr
        msgid = msgstr = None
        fuzzy = False

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            flush()
            section = None
            continue
        if line.startswith("#"):
            if line.startswith("#,") and "fuzzy" in line:
                flags_fuzzy = True
            continue
        if line.startswith("msgid "):
            flush()
            fuzzy = flags_fuzzy
            flags_fuzzy = False
            msgid = _unquote(line[6:])
            section = "id"
        elif line.startswith("msgstr "):
            msgstr = _unquote(line[7:])
            section = "str"
        elif line.startswith('"'):
            if section == "id":
                msgid += _unquote(line)
            elif section == "str":
                msgstr += _unquote(line)
        else:
            raise ValueError(f"Unsupported .po line: {raw!r}")
    flush()
    return entries


def build_mo(entries):
    """Serialise {msgid: msgstr} into GNU .mo bytes (deterministic)."""
    keys = sorted(entries)
    ids = b""
    strs = b""
    offsets = []
    for key in keys:
        encoded_id = key.encode("utf-8")
        encoded_str = entries[key].encode("utf-8")
        offsets.append((len(ids), len(encoded_id), len(strs), len(encoded_str)))
        ids += encoded_id + b"\0"
        strs += encoded_str + b"\0"

    count = len(keys)
    keystart = 7 * 4 + 16 * count
    valuestart = keystart + len(ids)
    koffsets = []
    voffsets = []
    for o1, l1, o2, l2 in offsets:
        koffsets += [l1, o1 + keystart]
        voffsets += [l2, o2 + valuestart]
    header = struct.pack("Iiiiiii", _MAGIC, 0, count, 7 * 4, 7 * 4 + count * 8, 0, 0)
    body = struct.pack(f"{len(koffsets + voffsets)}i", *(koffsets + voffsets))
    return header + body + ids + strs


def compile_po_file(po_path):
    """Return the .mo bytes for the .po file at ``po_path``."""
    entries = parse_po(Path(po_path).read_text(encoding="utf-8"))
    return build_mo(entries)


def compile_all(locale_root):
    """Rewrite every <locale_root>/*/LC_MESSAGES/django.mo from its .po."""
    written = []
    for po_path in sorted(Path(locale_root).glob("*/LC_MESSAGES/django.po")):
        mo_path = po_path.with_suffix(".mo")
        mo_path.write_bytes(compile_po_file(po_path))
        written.append(mo_path)
    return written


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent / "locale"
    for path in compile_all(root):
        print(f"compiled {path}")
    sys.exit(0)
