---
description: Run the full OmniGrid release — docs sync, changelog, gates, one commit and push
---

Run the repository's full release workflow. First read `.claude/commands/release.md` and follow its ten steps, progress format, release-safety rules, documentation checks, verification gates, and one-commit/one-push policy exactly.

OpenCode-specific execution rule: this checkout has native Windows `python` and `npm`; run those commands directly and never route npm through WSL. Keep the repository's CRLF policy for authored files, do not normalize `node_modules/`, and do not bypass hooks with `--no-verify`.

Use the current session for the release. Do not delegate it to a subagent. Stop and report the blocking step if a gate fails or the working tree cannot be safely released.
