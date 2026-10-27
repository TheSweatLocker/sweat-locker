#!/usr/bin/env python3
"""19 prop families that lose money in BOTH halves. Stop publishing them.

══ WHY (2026-10-27) ════════════════════════════════════════════════════════
Andy: "the current product as it is isn't great, I need great ... we have
some surfaces and sports hitting at 40%, that is unacceptable."

Measured on 87,875 GRADED props across four sports, priced from the stored
over/under odds (never flat -110):

    sport  side      n     win%     units       ROI
    NFL    OVER    2,598   47.0%    -134u     -5.15%
    MLB    OVER   19,049   34.0%  -3,313u    -17.39%
    NBA    OVER    4,910   42.7%    -779u    -15.87%
    NHL    OVER   23,885   25.7%  -4,778u    -20.00%
    ALL    OVER   50,442   31.6%  -9,004u    -17.85%
    ALL    UNDER  37,433   64.4%    -850u     -2.27%

**But this is NOT an over-ban**, and that distinction is the whole point.
A blanket ban on overs would have destroyed MLB `hits_over`, which runs
+7.14% on n=2,788. Six of the nineteen families below are UNDERS. The ban
is per (sport, prop_type), on measured evidence only.

ADMISSION CRITERIA, all three required:
  * n >= 150 graded props
  * ROI worse than -5%
  * NEGATIVE IN BOTH HALVES of its own date range (>=30 rows per half).
    One bad half is a regime, not an edge — that test is what kept
    `threes_over` (-11.68% pooled but +0.86% late) off this list.

MLB OVERS ARE DELIBERATELY NOT BANNED WHOLESALE. Their split-half reads
+15.16% early and -21.12% late, which fails the both-halves rule. The early
window is when the L5 lookback leak was live (repaired 10-26, 13,265 rows),
so that +15% is very likely the leak rather than a real regime. Individual
MLB families that fail on their own merits are listed; the sport is not
condemned on a contaminated half.

THE 11 FAMILIES THAT MUST SURVIVE — any change that kills these is wrong:
    MLB outs_under +8.66%   MLB hits_over +7.14%   MLB hits_under +5.30%
    NHL saves_under +4.78%  NFL receptions_under +3.87%
    NFL rush_attempts_under +3.42%  NBA pra_under +2.62%
    MLB total_bases_under +1.32%    NFL reception_yds_under +1.14%
    NBA pts_under +0.66%    NFL rush_yds_under +0.48%

THREE LAYERS. feedback_prop_family_ban_three_layer: a ban at the scorer
alone leaks, because the publishable VIEW has its own WHERE clause. This
module is layer one (source/compose). The scorer and the view must consult
it too — `--check` prints what is still reachable so the other two layers
can be verified rather than assumed.

REVERT: set BANNED = {} .
"""
from __future__ import annotations

import argparse
import sys

# ══ MLB IS JUDGED ON THE POST-LEAK WINDOW ONLY (>= 2026-09-19) ═══════════
# The first cut of this list used MLB's full history and let hr_over through
# at -37.95% on n=3,415, because its EARLY half looked positive. That early
# half is the window the L5 lookback leak was live in (repaired 10-26,
# 13,265 rows). The leak was protecting the worst family in the product.
#
# Re-measured on the clean window, two families I had listed as PROFITABLE
# and protected are among the worst:
#     hits_over    all-time +7.14%  ->  clean -17.50%  n=713
#     outs_under   all-time +8.66%  ->  clean -15.70%  n=132
# That is project_prop_edge_was_the_l5_leak_1026 confirmed on a second cut:
# the MLB prop "edge" was the leak, and any list built on pre-repair rows
# inherits it. MLB entries below carry CLEAN-window figures.
#
# (sport, prop_type) -> (n, win_pct, roi, note)
BANNED = {
    # ── MLB, clean window >= 2026-09-19 ──
    ('MLB', 'hr_over'):           (1834,  8.6, -41.63, 'clean window'),
    ('MLB', 'runs_over'):         (1776, 29.7, -26.29, 'clean window'),
    ('MLB', 'total_bases_over'):  (1744, 29.1, -26.07, 'clean window'),
    ('MLB', 'rbis_over'):         (1808, 24.6, -21.27, 'clean window'),
    ('MLB', 'outs_over'):         (150,  44.7, -18.14, 'clean window'),
    ('MLB', 'hits_over'):         (713,  46.7, -17.50, 'was +7.14% on leaked rows'),
    ('MLB', 'outs_under'):        (132,  45.5, -15.70, 'was +8.66% on leaked rows'),
    ('MLB', 'bb_over'):           (120,  47.5, -15.33, 'clean window'),
    ('MLB', 'bb_under'):          (132,  50.8, -14.09, 'clean window'),
    ('MLB', 'ha_under'):          (166,  50.0,  -7.84, 'clean window'),
    ('MLB', 'ks_over'):           (61,   49.2,  -6.58, 'clean window, thin'),
    # ── NHL, negative in both halves ──
    ('NHL', 'goals_over'):        (6818,  9.2, -26.26, 'both halves neg'),
    ('NHL', 'saves_over'):        (517,  39.7, -19.39, 'both halves neg'),
    ('NHL', 'assists_over'):      (3468, 22.7, -18.17, 'both halves neg'),
    ('NHL', 'sog_over'):          (8554, 38.1, -17.95, 'both halves neg'),
    ('NHL', 'points_over'):       (4528, 28.2, -15.94, 'both halves neg'),
    # ── NBA, negative in both halves ──
    ('NBA', 'ast_over'):          (693,  38.0, -23.43, 'both halves neg'),
    ('NBA', 'pra_over'):          (820,  43.0, -20.85, 'both halves neg'),
    ('NBA', 'pts_over'):          (1036, 42.5, -18.10, 'both halves neg'),
    ('NBA', 'reb_over'):          (878,  42.3, -15.64, 'both halves neg'),
    ('NBA', 'blocks_under'):      (354,  54.5, -10.42, 'both halves neg'),
    ('NBA', 'steals_under'):      (482,  47.5, -10.36, 'both halves neg'),
    ('NBA', 'reb_under'):         (595,  49.7, -10.28, 'both halves neg'),
    ('NBA', 'blocks_over'):       (355,  45.4,  -6.40, 'both halves neg'),
    # ── NFL, negative in both halves ──
    ('NFL', 'rush_yds_over'):     (233,  46.4, -12.55, 'both halves neg'),
    ('NFL', 'receptions_over'):   (511,  49.5,  -6.06, 'both halves neg'),
    # ── UNGRADEABLE, banned on a different ground ──
    # 430 receipts, 430 NO_ACTION, 0 W/L in all of history: the receipt
    # layer has never graded one. A prop we cannot grade cannot be in a
    # record, and its conviction is inverted besides (76+ went 1-12,
    # -72.7%, project_anytime_td_conviction_inverted_1022). It was the
    # single largest surviving NFL family by volume (230 since 10-20).
    ('NFL', 'anytime_td_over'):   (0,     0.0,   0.0,  'NEVER GRADED, 430 rows'),
    ('MLB', 'anytime_td_over'):   (0,     0.0,   0.0,  'NEVER GRADED'),
    ('NBA', 'anytime_td_over'):   (0,     0.0,   0.0,  'NEVER GRADED'),
}

# Families still positive ON THE CLEAN WINDOW. Asserted by --selftest so a
# future edit to BANNED cannot quietly take one of them out. Note that
# MLB hits_over and outs_under are NO LONGER here -- they only looked
# profitable on leaked rows.
MUST_SURVIVE = (
    ('MLB', 'hits_under'),          # clean +14.11%
    ('MLB', 'ks_under'),            # clean  +2.91%
    ('MLB', 'runs_under'),          # clean  +1.36%
    ('MLB', 'ha_over'),             # clean  +1.05%
    ('MLB', 'total_bases_under'),   # clean  +0.78%
    ('NHL', 'saves_under'),         # +4.78%
    ('NFL', 'receptions_under'),    # +3.87%
    ('NFL', 'rush_attempts_under'), # +3.42%
    ('NBA', 'pra_under'),           # +2.62%
    ('NFL', 'reception_yds_under'), # +1.14%
    ('NBA', 'pts_under'),           # +0.66%
    ('NFL', 'rush_yds_under'),      # +0.48%
)


def is_banned(sport, prop_type):
    """-> (True, reason) | (False, '')   Never raises."""
    if not BANNED:
        return False, ''
    key = (str(sport or '').upper().strip(), str(prop_type or '').lower().strip())
    ev = BANNED.get(key)
    if not ev:
        return False, ''
    n, pct, roi, note = ev
    if n == 0:
        return True, '%s %s is BANNED: %s' % (key[0], key[1], note)
    return True, ('%s %s is BANNED: %.1f%% on n=%d, ROI %+.2f%% (%s)'
                  % (key[0], key[1], pct, n, roi, note))


def banned_for_sport(sport):
    s = str(sport or '').upper().strip()
    return sorted(pt for (sp, pt) in BANNED if sp == s)


def _selftest():
    bad = 0
    print('  banned families: %d' % len(BANNED))
    for sp in sorted({s for s, _ in BANNED}):
        print('     %-5s %s' % (sp, ', '.join(banned_for_sport(sp))))
    print('')
    print('  every banned family must report banned:')
    for (sp, pt) in BANNED:
        ok, _why = is_banned(sp, pt)
        if not ok:
            print('     FAIL %s %s' % (sp, pt))
            bad += 1
    print('     %d checked' % len(BANNED))
    print('')
    print('  every PROFITABLE family must report NOT banned:')
    for sp, pt in MUST_SURVIVE:
        ok, why = is_banned(sp, pt)
        if ok:
            print('     FAIL %s %s is banned but is profitable -- %s'
                  % (sp, pt, why))
            bad += 1
    print('     %d checked' % len(MUST_SURVIVE))
    print('')
    print('  case and whitespace must not matter:')
    for sp, pt in (('nhl', ' goals_over '), ('NHL', 'GOALS_OVER')):
        ok, _ = is_banned(sp, pt)
        print('     %-5s %-14r -> %s' % (sp, pt, 'banned' if ok else 'FAIL'))
        if not ok:
            bad += 1
    print('')
    print('  an unknown family must NOT be banned by accident:')
    for sp, pt in (('MLB', 'strikeouts_over'), ('NFL', 'sacks_over'),
                   ('NHL', 'blocked_shots_over'), ('XXX', 'goals_over')):
        ok, _ = is_banned(sp, pt)
        print('     %-5s %-20s -> %s' % (sp, pt, 'FAIL' if ok else 'allowed'))
        if ok:
            bad += 1
    print('')
    print('  %d failure(s)' % bad)
    return 1 if bad else 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--sport')
    ap.add_argument('--prop-type')
    a = ap.parse_args()
    if a.selftest:
        sys.exit(_selftest())
    if a.sport and a.prop_type:
        ok, why = is_banned(a.sport, a.prop_type)
        print(why if ok else 'allowed')
        sys.exit(0)
    print('banned families by sport:')
    for sp in sorted({s for s, _ in BANNED}):
        print('  %-5s %s' % (sp, ', '.join(banned_for_sport(sp))))
    sys.exit(0)
