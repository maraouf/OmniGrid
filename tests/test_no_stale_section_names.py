"""Stale-terminology lint — fails the build when an old section name
leaks into a user-visible surface.

Run directly (``python tests/test_no_stale_section_names.py``) or via
``pytest tests/test_no_stale_section_names.py``. Exit code is non-zero
when any unallowlisted hit is found; the offending file + line + matched
text is printed so the matching commit can fix it before merge.

Surfaces audited (user-visible only):
    * ``static/i18n/*.json``   — every translation bundle
    * ``static/index.html``    — Alpine SPA template
    * ``static/_partials/**/*.html`` — every server-side-included partial
    * ``static/login.html`` + ``static/login.js`` (login page is also user-facing)

The lint is INTENTIONALLY narrow — it does NOT scan ``static/js/*.js``
because those files carry many code-comment references to the OLD
section names that document the rename's history. The rule registry
(``tests/stale_terminology.json``) carries an optional ``allowlist``
field per rule for the rare case where a user-visible surface must
intentionally keep the old wording (e.g. a backwards-compat fallback
string).

Adding a new rule: bump ``tests/stale_terminology.json`` with a new
``rules`` entry containing the regex pattern + the replacement hint.
The pattern is compiled with ``re.IGNORECASE`` so case variants don't
require duplicate entries.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Repo root is the parent of this tests/ directory.
ROOT = Path(__file__).resolve().parent.parent
RULES_PATH = ROOT / "tests" / "stale_terminology.json"

# User-visible-surface globs. These are the files an end user reads on
# the rendered UI; code-only files (Python, JS modules carrying
# comments + ARIA strings that may reference old names for context)
# are exempt.
SURFACE_GLOBS = (
    "static/i18n/*.json",
    "static/index.html",
    "static/_partials/**/*.html",
    "static/login.html",
)


def _load_rules() -> list[dict]:
    """Parse the JSON rule registry. The ``_doc`` block is informational
    only and skipped; only the ``rules`` array contributes patterns."""
    if not RULES_PATH.exists():
        raise SystemExit(f"Rules file missing: {RULES_PATH}")
    data = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    rules = data.get("rules") or []
    if not isinstance(rules, list) or not rules:
        raise SystemExit(f"Rules file has no rules: {RULES_PATH}")
    compiled: list[dict] = []
    for r in rules:
        pat = r.get("pattern")
        if not pat:
            continue
        try:
            re_obj = re.compile(pat, re.IGNORECASE)
        except re.error as exc:
            raise SystemExit(f"Invalid regex in rules file: {pat!r} ({exc})")
        compiled.append({
            "regex": re_obj,
            "raw": pat,
            "hint": r.get("replacement_hint", ""),
            "reason": r.get("reason", ""),
            "allowlist": set(r.get("allowlist") or []),
        })
    return compiled


def _iter_surface_files() -> list[Path]:
    """Resolve every user-visible-surface file by globbing the
    ``SURFACE_GLOBS`` list against the repo root. Sorts deterministically
    so the lint's output order matches operator expectation across runs."""
    out: list[Path] = []
    seen: set[Path] = set()
    for glob in SURFACE_GLOBS:
        for p in sorted(ROOT.glob(glob)):
            if p.is_file() and p not in seen:
                seen.add(p)
                out.append(p)
    return out


def _scan_file(path: Path, rules: list[dict]) -> list[tuple[int, str, dict]]:
    """Walk one file line-by-line; return ``[(lineno, line_text, rule)]``
    for every match. Reads as text with UTF-8; bytes that don't decode
    are replaced with the replacement char rather than raising — the
    lint's job is to find string drift, not parse exotic encodings."""
    hits: list[tuple[int, str, dict]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return hits
    rel = str(path.relative_to(ROOT)).replace("\\", "/")
    for lineno, line in enumerate(text.splitlines(), start=1):
        for rule in rules:
            if rel in rule["allowlist"]:
                continue
            if rule["regex"].search(line):
                hits.append((lineno, line.strip(), rule))
    return hits


def main() -> int:
    rules = _load_rules()
    files = _iter_surface_files()
    if not files:
        print("WARN: no user-visible-surface files matched the glob list.")
        return 0
    total_hits = 0
    for path in files:
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        for (lineno, line, rule) in _scan_file(path, rules):
            total_hits += 1
            print(
                f"::error file={rel},line={lineno}::Stale terminology "
                f"({rule['raw']!r}): {line!r} — replace with {rule['hint']!r}."
            )
    if total_hits:
        print(
            f"\nFAILED: {total_hits} stale-terminology hit(s) across "
            f"{len(files)} surfaces. Update the file(s) or add an "
            f"allowlist entry in tests/stale_terminology.json."
        )
        return 1
    print(f"OK: 0 stale-terminology hits across {len(files)} surfaces.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
