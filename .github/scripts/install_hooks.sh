#!/usr/bin/env bash
# Install the repo's git hooks. Run once per clone:
#
#     bash .github/scripts/install_hooks.sh
#
# Git hooks live in .git/hooks, which is NOT version controlled, so a fresh
# clone has none. This installs them from the committed copies here.
#
# == 2026-11-08 . WHY THE PRE-COMMIT HOOK EXISTS =======================
# A step was inserted INSIDE another step in nba_pipeline.yml and
# nhl_pipeline.yml, leaving two `continue-on-error` keys in one step. GitHub
# refuses a workflow file with duplicate mapping keys and DE-REGISTERS the
# whole workflow, so both pipelines fired ZERO runs from 11-06 to 11-08 --
# three days, two sports, nothing ran, and NHL's grading step lives in that
# file. `yaml.safe_load` accepts duplicate keys silently, so the breakage
# was invisible to every check we had.
#
# The commit that broke it should never have been possible. Now it isn't.
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
hook_dir="$repo_root/.git/hooks"
mkdir -p "$hook_dir"

cat > "$hook_dir/pre-commit" <<'HOOK'
#!/usr/bin/env bash
# Refuse a commit that would break a GitHub workflow file.
# Installed by .github/scripts/install_hooks.sh -- do not edit here.
set -uo pipefail

staged="$(git diff --cached --name-only --diff-filter=ACM \
          -- '.github/workflows/*.yml' '.github/workflows/*.yaml' || true)"
[ -z "$staged" ] && exit 0

repo_root="$(git rev-parse --show-toplevel)"
validator="$repo_root/.github/scripts/validate_workflows.py"
[ -f "$validator" ] || exit 0

# Validate the STAGED content, not the working tree: the commit is what
# GitHub will read. Each staged file is written to a temp path first.
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT
paths=()
while IFS= read -r f; do
    [ -z "$f" ] && continue
    out="$tmp_dir/$(basename "$f")"
    git show ":$f" > "$out" 2>/dev/null || continue
    paths+=("$out")
done <<< "$staged"

[ "${#paths[@]}" -eq 0 ] && exit 0

if ! python "$validator" "${paths[@]}"; then
    echo
    echo "pre-commit: REFUSED -- a staged workflow file is one GitHub will"
    echo "not register. It would vanish from the Actions sidebar (listed by"
    echo "path instead of its name:) and NO cron would fire for it."
    echo
    echo "This exact bug took NBA and NHL dark for three days (11-06..11-08)."
    echo "Fix the file, or re-run with --no-verify only if Andy says so."
    exit 1
fi
exit 0
HOOK

chmod +x "$hook_dir/pre-commit"
echo "installed: $hook_dir/pre-commit"
echo "it validates any staged .github/workflows/*.yml with a STRICT yaml"
echo "loader (duplicate keys, orphaned step headers) before the commit lands."
