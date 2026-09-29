---
name: underscore-scratch-convention
description: mlb_pipeline/_*.py is gitignored scratch by default; keeper-grade files force-add
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-07-22T01:36:05.676Z
---

`.gitignore` line 88: `mlb_pipeline/_*.py` — all underscore-prefixed
Python files in mlb_pipeline/ are hidden by default. Intent: local
scratch (one-off audits, debug scripts) stays out of git without
having to remember `.gitignore` each time.

**Why:** Prevents accidentally committing throwaway experiments to
the shared history. The convention is documented in-file at lines
84-87 of .gitignore: "When a new file is keeper-grade, `git add -f` it."

**How to apply:**
- **Local scratch** (audits, one-shot debug, throwaway experiments):
  name it `_something.py` — gitignore handles it automatically.
- **Shared infra that happens to be private-ish** (shared helpers,
  underscore-prefixed by module convention like `_playwright_helper.py`):
  `git add -f <path>` on the first commit. Subsequent edits track normally.
- The rule doesn't UNTRACK already-committed files, so once force-added
  you don't need `-f` again.

Related: [[project_launch_priorities_july]]
