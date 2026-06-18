"""Pre-commit gate: detect hardcoded percentages in user-facing code paths.

Runs over staged .py files (or all if STAGED_ONLY=0). Flags percentages
in string literals that:
  - appear in modules known to feed user surfaces (sweat card / Jerry reads / etc.)
  - are NOT accompanied by a nearby sample-size citation (n=X, "X games",
    "X-Y" W-L pair, "lifetime", "attempts", "starts")
  - are not inside comment lines

Exits non-zero on violations so a pre-commit hook can block the commit.

USAGE
  python scripts/check_hardcoded_percents.py            # check all known files
  python scripts/check_hardcoded_percents.py --staged   # only staged files
  python scripts/check_hardcoded_percents.py path/to/file.py [...]

INSTALL AS PRE-COMMIT HOOK
  Copy or symlink to .git/hooks/pre-commit (mark executable):
    #!/usr/bin/env bash
    python scripts/check_hardcoded_percents.py --staged

Spec: feedback_sample_size_with_pct.md (every % needs n alongside) +
      docs/hardcoded_percent_audit.md
"""
import os
import re
import subprocess
import sys

# Files whose strings render to user-facing surfaces. New emitters
# should be added here when introduced.
USER_FACING_FILES = {
    "mlb_pipeline/play_of_day.py",
    "mlb_pipeline/generate_mlb_game_reads.py",
    "mlb_pipeline/generate_sweat_card.py",
    "mlb_pipeline/generate_dawg_of_day.py",
    "mlb_pipeline/generate_tonight_card.py",
    "mlb_pipeline/generate_nba_game_reads.py",
    "mlb_pipeline/generate_nfl_game_reads.py",
    "mlb_pipeline/generate_ufc_game_reads.py",
    "mlb_pipeline/generate_props.py",
    "mlb_pipeline/cohort_signals.py",
}

# Match a percentage like "76%", "76.2%", "~80%". Ignored if preceded
# by a digit (avoids matching inside larger numbers).
_PCT_PATTERN = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,2})?)\s*%")

# Markers that signal a nearby sample-size citation. If any appears in
# the same string literal as a %, the violation is exempted.
_N_MARKERS = re.compile(
    r"(?:n\s*=\s*\d+|\b\d+\s*(?:games?|starts?|attempts?|samples?|outcomes?)\b|"
    r"\b\d+-\d+\b|over\s+\d+|across\s+\d+|lifetime|backtest)",
    re.IGNORECASE,
)

# Allow-list for known safe contexts (e.g. test files, calibration
# computation that emits %s that are about-to-be-validated).
_ALLOW_FILE_SUBSTRINGS = ("_test_", "test_", "/tests/", "buy_down_calibration.py")

# Specific (file, line) pairs to ignore — known false positives where
# the percentage appears inside a docstring (which the regex scanner
# misses because triple-quoted strings are skipped but their inner
# single-quoted bits leak through). Add new entries with a comment
# explaining why each is safe.
_ALLOW_FILE_LINE = {
    # generate_sweat_card.py:55-67 docstring describing a 2026-05-22 bug
    # symptom: surface text "0% audited (n=)" appeared due to None lookup.
    ("mlb_pipeline/generate_sweat_card.py", 61),
    # generate_sweat_card.py:486 — function docstring describes the 70%+
    # threshold as a reference, not a user-facing claim.
    ("mlb_pipeline/generate_sweat_card.py", 486),
    # generate_tonight_card.py:99 — same docstring pattern as sweat card.
    ("mlb_pipeline/generate_tonight_card.py", 99),
}


def _staged_python_files():
    try:
        out = subprocess.check_output(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
            text=True,
        )
    except subprocess.CalledProcessError:
        return []
    return [
        p.strip() for p in out.splitlines()
        if p.strip().endswith(".py")
    ]


def _scan_string_literals(src):
    """Yield (literal_text, start_lineno) for single-line string literals.
    Skips triple-quoted strings entirely — those are 99% docstrings, not
    user-facing output. Catching docstrings creates false positives that
    train developers to ignore the hook.
    """
    for m in re.finditer(
        r"(?<!\\)(\".*?(?<!\\)\"|'.*?(?<!\\)')",
        src,
    ):
        yield m.group(0), src[:m.start()].count("\n") + 1


def _line_is_comment(src, line_number):
    """True if the given 1-indexed line starts with optional whitespace
    then '#'. Used to skip docstrings & inline comments."""
    lines = src.splitlines()
    if line_number - 1 >= len(lines):
        return False
    return lines[line_number - 1].lstrip().startswith("#")


def check_file(path):
    """Return list of (lineno, snippet, reason) for each violation."""
    if any(s in path for s in _ALLOW_FILE_SUBSTRINGS):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
    except OSError:
        return []

    violations = []
    for literal, lineno in _scan_string_literals(src):
        if not _PCT_PATTERN.search(literal):
            continue
        if _line_is_comment(src, lineno):
            continue
        if _N_MARKERS.search(literal):
            continue
        # Skip known false positives (docstring context the regex can't detect)
        norm_path = path.replace("\\", "/")
        if (norm_path, lineno) in _ALLOW_FILE_LINE:
            continue
        # Strip wrapping quotes for display
        display = literal.strip().strip("'\"").strip('"""').strip("'''")
        if len(display) > 120:
            display = display[:117] + "..."
        violations.append((lineno, display,
                           "hardcoded % without nearby n/sample-size citation"))
    return violations


def main():
    args = sys.argv[1:]
    staged = "--staged" in args
    explicit_files = [a for a in args if not a.startswith("--")]

    if explicit_files:
        candidate_files = explicit_files
    elif staged:
        candidate_files = _staged_python_files()
    else:
        # Default: check all known user-facing files
        candidate_files = sorted(USER_FACING_FILES)

    candidate_files = [
        p for p in candidate_files
        if any(p.endswith(suffix) or suffix in p
               for suffix in USER_FACING_FILES)
    ]
    if not candidate_files:
        print("No user-facing files to check.")
        return 0

    all_violations = []
    for path in candidate_files:
        for lineno, snippet, reason in check_file(path):
            all_violations.append((path, lineno, snippet, reason))

    if not all_violations:
        print(f"check_hardcoded_percents: {len(candidate_files)} files clean.")
        return 0

    def _safe(s):
        # Strip characters the local console can't encode (cp1252 on Win)
        try:
            sys.stdout.write(s); sys.stdout.write("")
            return s
        except UnicodeEncodeError:
            return s.encode("ascii", errors="replace").decode("ascii")

    print(_safe(f"check_hardcoded_percents: {len(all_violations)} violation(s):"))
    for path, lineno, snippet, reason in all_violations:
        try:
            print(f"  {path}:{lineno}  {reason}")
            print(f"    > {snippet}")
        except UnicodeEncodeError:
            print(f"  {path}:{lineno}  {reason}")
            print("    > " + snippet.encode("ascii", errors="replace").decode("ascii"))
    print()
    print("Per feedback_sample_size_with_pct: every % on a user surface must")
    print("show n alongside, both live (not hardcoded), gated at n >= 30.")
    print("Either wire to cohort_lookup / mlb_tier_calibration, strip the %,")
    print("or add an n citation inside the same string literal.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
