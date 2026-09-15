# The Sweat Locker — Claude Working Rules

## HARD RULES (violate and Andy loses money / trust)

### 1. Never present a specific play to Andy without direct DB verification

If Andy asks for "picks", "favorites", "what should I post", "sharp card status", or anything that could result in him betting/posting a play, EVERY specific (player + prop_type + line + odds + tier + conviction) tuple you present MUST be verified by a direct DB query BEFORE presenting.

**Never trust subagent-composed pick lists.** Subagents have fabricated plays that don't exist (documented case: 2026-09-14 "Angel Martinez hits OVER 0.5 @ -150 conv 92" — real player, plausible shape, ZERO rows in mlb_pipeline_props on any date). This bug is invisible on read and undermines the "we show the receipts" brand.

**Enforcement pattern**:
1. Compose the pick list yourself via direct Bash query against `mlb_pipeline_props`, `nfl_pipeline_props`, `jerry_reads`, or `jerry_cache`.
2. If you must use a subagent for research, follow up with a direct query citing the exact `(player_name, prop_type, prop_line, direction, game_date)` for each recommended row.
3. If the DB doesn't contain the tuple you were about to recommend, DROP it. Never present unverified plays.
4. Preferred: cite the source (`mlb_pipeline_props.id` or the composite key) inline so Andy can verify.

Applies equally to: PRIME lists, socials picks, Sharp Card recaps, morning audit "favorites", POTD calls.

### 2. Trust the pipeline data over your training-data recall

Player-team assignments, roster changes, and prop shapes reflect current pipeline state. When your training data disagrees with what's in `mlb_pipeline_props` / `nfl_pipeline_props` / depth chart JSON, **default to trusting the DB**. Andy has corrected this multiple times (Alonso→BAL, Suarez→BOS, Semien→NYM, Walker→KC, Waddle→DEN). See `user_2026_roster_corrections` in memory.

### 3. Never skip git hooks, never bypass signing, never force-push

`--no-verify`, `--no-gpg-sign`, `git push --force` — only if Andy explicitly asks. If a hook fails, fix the underlying issue.

### 4. Never commit `.env` files, API keys, or secrets

Andy's `.env` is at repo root + `mlb_pipeline/.env`. Both are gitignored. If you're about to write a file that includes a key or a `.env`-like blob, STOP.

### 5. Never mention Madden externally

Rebrand to "Sweat Stats" for anything user-facing. Internal code can keep the `madden_*` field names.

### 6. No sportsbook links in the app

Compliance / ToS.

### 7. Sport terminology

- **Sweat Card** = the dashboard/home screen (yesterday recap + POTD + Dawg + composite)
- **Sharp Card** = the Steam Room tab (composed picks from `sharp_card` surface)
- Do not conflate the two. When you say "Sharp Card" you mean the Steam Room tab; the dashboard is Sweat Card.

## Working conventions

- **Morning audit canonical order**: Sweat Card → POTD → Jerry → Prop → Steam Room. Show ungraded plays.
- **LR daily verify**: shadow must be distinct per game today. If all games have the same p_home_win, that's the stale-shadow bug — investigate before publishing.
- **Prop odds band**: -300 to +150. Anything outside → SKIP.
- **Batter Hits O 0.5 juice trap**: worse than -200 → don't publish even if PRIME.
- **Heavy fav ML trap**: -200+ moneylines have a documented 29% cover rate on -1.5.
- **POTD juice gate**: max -200 juice.
- **Sides > totals discipline**: card composition prefers sides over totals in tight races.
- **No NRFI/YRFI on cards** unless PRIME+ AND extreme edge.

## Session hygiene

- Session start: open `docs/daily/YYYY-MM-DD.md`. If it doesn't exist, create it with a stub.
- Commit → push same turn. Verify SHA. Never leave uncommitted intent.
- `mlb_pipeline/_*.py` files are gitignored (scratch convention).

## When in doubt

Ask Andy. Silent assumptions on player teams, tier gates, or pick composition have cost real money and real credibility. A 30-second question beats a fabricated play in a public post.
