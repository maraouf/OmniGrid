"""Tests for the commit-msg hook that refuses AI attribution trailers.

Why this hook needs a fixture rather than trust: the rule it enforces
CONTRADICTS the tool. This repo's commits carry no AI attribution — no
`Co-Authored-By: Claude ...`, no `Claude-Session: ...` — while the assistant's
own git instructions ask for both, and that instruction is live; it was
reissued mid-session while this file was being written. Two sources giving opposite
orders is not something anyone can be reminded out of, so the hook is the
binding constraint and its behaviour is worth pinning.

The fixture earned its place immediately. Run inside a real commit it caught a
live hole: under a UTF-8 locale — which is what git's own hook environment
uses — the emoji-prefixed "Generated with [Claude Code]" footer was NOT
refused, while the two plain-ASCII trailers matched fine. The single trailer
that ships with an emoji in front of it was the one that could slip through.
The hook now forces byte-oriented matching, and the locale test below keeps
that closed.

The rest split two ways. Four are the hook doing its job. The other three are
the ways a careless tightening would make it useless:

* refusing a genuine human co-author, which would get the hook routed around,
  and a hook people route around protects nothing;
* refusing any commit whose body mentions the `CLAUDE.md` file, which this
  repo's messages do constantly;
* refusing on text inside a `#` comment line, which git strips before storing —
  `git commit -v` pastes the entire staged diff there, so a commit touching
  this very file would trip on its own patterns.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / ".githooks" / "commit-msg"

BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(
    BASH is None or not HOOK.is_file(),
    reason="needs bash and the checked-in hook",
)

FOOTER = ("fix: something\n\n"
          "\U0001f916 Generated with [Claude Code](https://claude.com/claude-code)\n")


def _run(message: str, tmp_path: Path, env: Optional[dict] = None) -> int:
    """Run the real hook over a message file; return its exit status."""
    f = tmp_path / "COMMIT_EDITMSG"
    f.write_text(message, encoding="utf-8", newline="\n")
    return subprocess.run(
        [str(BASH), str(HOOK), str(f)],
        capture_output=True, text=True, timeout=30, env=env,
    ).returncode


# --- the trailers it exists to stop --------------------------------------

def test_refuses_a_claude_co_author_trailer(tmp_path):
    assert _run("fix: something\n\n"
                "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>\n",
                tmp_path) != 0


def test_refuses_a_session_link_trailer(tmp_path):
    assert _run("fix: something\n\n"
                "Claude-Session: https://claude.ai/code/session_abc123\n",
                tmp_path) != 0


def test_refuses_the_generated_with_footer(tmp_path):
    assert _run(FOOTER, tmp_path) != 0


def test_refuses_a_trailer_that_is_indented(tmp_path):
    """Leading whitespace must not smuggle one past the anchor."""
    assert _run("fix: something\n\n"
                "  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>\n",
                tmp_path) != 0


def test_refuses_the_footer_under_a_utf8_locale(tmp_path):
    """The hole this fixture actually found, in the environment it hid in.

    The footer is the only trailer with a multibyte character in front of it,
    so it was the only one whose match depended on how the caller's locale
    decoded bytes — and it is the one the harness appends. A hook that stops
    two of three trailers is not a guard, it is a false sense of one.
    """
    for loc in ("C", "C.UTF-8", "en_US.UTF-8", "POSIX"):
        env = dict(os.environ, LC_ALL=loc)
        rc = _run(FOOTER, tmp_path, env=env)
        assert rc != 0, f"footer slipped through under LC_ALL={loc}"


# --- the ways a careless tightening would break it ------------------------

def test_allows_a_human_co_author(tmp_path):
    """`Co-Authored-By:` is a legitimate git trailer. Only the Claude ones are
    refused — blocking every co-author would make this hook something people
    route around."""
    assert _run("fix: something\n\n"
                "Co-Authored-By: Jane Doe <jane@example.com>\n",
                tmp_path) == 0


def test_allows_a_message_that_merely_mentions_the_claude_md_file(tmp_path):
    """This repo's commit bodies discuss CLAUDE.md constantly; an unanchored
    match on the word would refuse most of them."""
    assert _run("docs: document the merge order in CLAUDE.md\n\n"
                "CLAUDE.md now carries the provider ordering rule.\n",
                tmp_path) == 0


def test_ignores_a_trailer_inside_a_comment_line(tmp_path):
    """git strips `#` lines before storing the message, and `git commit -v`
    pastes the whole staged diff there — so a commit that touches this hook
    would otherwise trip on its own patterns."""
    assert _run("fix: something\n\n"
                "# Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>\n"
                "# Claude-Session: https://claude.ai/code/session_abc123\n",
                tmp_path) == 0


def test_an_ordinary_message_passes(tmp_path):
    """The baseline — if this ever fails, the hook is refusing everything."""
    assert _run("fix(hosts): correct the disk rollup\n\n"
                "The pool total was being overwritten by a single dataset.\n",
                tmp_path) == 0
