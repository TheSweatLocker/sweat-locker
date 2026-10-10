# Prop Playbook vs Legacy — 21-day shadow-mode comparison
_Generated 2026-08-20 · window 2026-07-30 → 2026-08-20 · sport MLB_

## Sample

- **630** `prop_playbook_decisions` rows in window (17 Aug 41 · 18 Aug 124 · 19 Aug 281 · 20 Aug 184).
- **2,727** legacy `mlb_pipeline_props` rows in window; **2,523** graded.
- **430** playbook decisions matched a graded legacy prop by natural key
  `(game_date, player_name, prop_type, direction, prop_line)` — the 200
  unmatched rows are **all from 2026-08-20** (today; games not final).
- **Matched sample is 8/17–8/19 (three days), n=430.** 21-day framing per
  spec, but that's the real backfill horizon once games grade.

> Grading columns were not yet applied to `prop_playbook_decisions` when
> this report ran (see caveat at the bottom). Every number below comes
> from an in-memory join of ppd → legacy props results — arithmetically
> identical to what `grade_prop_playbook.py` will write once the
> migration lands.

## Hit rate by tier — playbook

| Tier   | W-L-P     | n   | HR      |
|--------|-----------|-----|---------|
| PRIME  | 17-8-0    | 25  | **68.0%** |
| STRONG | 43-37-0   | 80  | 53.8%   |
| LEAN   | 159-164-2 | 323 | 49.2%   |

Playbook tiers **do descend correctly** (68 → 54 → 49) — grading floor
holds — but STRONG is a coin flip and the PRIME sample (n=25) is thin.

## Hit rate by tier — legacy (on the same 430 picks)

| Tier   | W-L-P     | n   | HR      |
|--------|-----------|-----|---------|
| PRIME  | 8-2-0     | 10  | **80.0%** |
| STRONG | 21-6-0    | 27  | **77.8%** |
| LEAN   | 166-168-0 | 334 | 49.7%   |

Legacy tiers grade **cleanly steeper**: PRIME and STRONG both above 77%,
LEAN a hair better than playbook LEAN at similar volume.

## Confusion matrix (playbook row × legacy col)

```
                 COVERAGE  LEAN  PRIME  SKIP  STRONG
  playbook LEAN         8   263      0    44      10
  playbook PRIME        0    16      3     1       5
  playbook STRONG       0    55      7     6      12
```

**Grade inflation is the story.** Playbook labels 71 legacy LEANs (55
STRONG + 16 PRIME) as its top two tiers, and promotes 10 legacy STRONGs
down to LEAN. Same-side hit rate when playbook re-tiered:

| Regrade                 | HR    | n  |
|-------------------------|-------|----|
| PB PRIME / LEG LEAN     | 62.5% | 16 |
| PB PRIME / LEG STRONG   | 60.0% | 5  |
| PB STRONG / LEG PRIME   | 71.4% | 7  |
| PB STRONG / LEG LEAN    | 49.1% | 55 |
| PB LEAN / LEG STRONG    | 90.0% | 10 |

The two rows that matter: **PB STRONG / LEG LEAN (n=55, 49%)** — playbook
is promoting coin-flip legacy LEANs to STRONG in bulk. And
**PB LEAN / LEG STRONG (n=10, 90%)** — the 10 legacy STRONGs playbook
demoted still hit at 90%; playbook is throwing away money in shadow on
that bucket.

## Disagreement — who's right when sides differ?

Playbook takes a different side than legacy on **59 of 430** picks:

| pb side · leg side | count |
|--------------------|-------|
| BACK · PASS        | 43    |
| FADE · PASS        | 16    |
| FADE · BACK        | 2     |

Head-to-head on those 59:

- **Legacy right:** 26
- **Playbook right:** 18
- Both right: 7
- Both wrong: 8

Legacy wins the disagreement head-to-head **26–18** (a 44% win rate for
playbook when it deviates). The 43 BACK-when-legacy-PASSed picks are
the biggest bucket and the main leak: playbook is finding "signal" the
legacy scorer correctly ignored. FADEs (18 total, all against
PASS/BACK) are too small a sample to judge.

## Verdict — **do not promote yet**

Three concrete blockers:

1. **PRIME/STRONG under-perform legacy by 12–24 pp** on matched picks.
   Legacy STRONG hits 77.8%; playbook STRONG hits 53.8% on 3× the volume.
   That is not a promotion-ready gap.
2. **Grade inflation.** Playbook mints 55 STRONGs from legacy LEANs at
   49% (coin flip) and 16 PRIMEs from legacy LEANs at 62.5%. Public
   tier labels have to mean something; PRIME at 68% n=25 is not the
   PRIME users have been trained to trust.
3. **Playbook loses the disagreement fight** 26–18 when it deviates
   from legacy on side (BACK vs PASS/FADE).

Two things to fix before re-audit:

- **Recalibrate playbook tier thresholds upward.** Current
  score → tier cutoffs are too loose. Target: PRIME ≥ 75%, STRONG ≥ 65%
  on matched-pick backtest before flipping cutover.
- **Gate FADE behind stronger evidence.** 18 FADEs in 3 days from a
  system running its first shadow week is too aggressive; require
  n≥3 opposing signals or a dedicated fade signal source before letting
  FADE ship to production.

Re-run this audit weekly. Cutover trigger: playbook meets/beats legacy
hit-rate on same picks over **14 consecutive days** and disagreement
head-to-head flips to playbook-favored.

---

## Caveat — grader run status

Migration `20260820_prop_playbook_grading.sql` is written but not yet
applied — the harness has no direct Postgres access, no `exec_sql` RPC,
and no `supabase` CLI on this machine. Two steps to complete the loop:

1. Paste the migration into the Supabase SQL editor and run.
2. `python mlb_pipeline/grade_prop_playbook.py --date 2026-08-19 --backfill 21`
   — will grade all 446 pre-8/20 rows using the pass-1 legacy inheritance
   above, then stats-API fallback for any playbook-only picks. Preflight
   in the script will bail with a nudge if the migration hasn't landed.

The numbers in this report **will match** what the grader writes
(same join, same result mapping).
