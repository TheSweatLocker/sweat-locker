---
name: project_nhl_projection_calibration_927
description: NHL goal projection was wrong twice; three league invariants caught it and set every constant
metadata:
  type: project
---

Built 2026-09-27 (nhl_projection.py). Every constant is DERIVED from a
league invariant, not guessed — recording them so nobody re-tunes by feel.

**The three checks the NHL gives you for free:**
    mean total          6.10   (league scoring rate, 3.05 per team)
    OT rate             23%    (share of games reaching overtime)
    p(home) even teams  54.5%  (home win rate including OT)

**First version failed all three:** Edmonton projected 4.19 goals (worst
real defence allows ~3.5), OT rate 13-17%, p(home) 0.70.

**SPREAD_SHRINK = 0.62.** The relative spread in 5v5 shot-quality rates is
WIDER than in full-game goals — special teams, score effects and goalie
variance compress it — so a ratio taken at 5v5 and applied whole
over-amplifies every mismatch. 0.62 is the value that puts simulated OT
on 23%. OT rate is the cleanest check on the margin distribution because
it is determined entirely by how often the sim ties.

**TIE_PULL = 0.22.** Independent Poisson at league scoring gives P(tie) =
0.165 against a real 0.230, and NO re-tuning of the goal rates closes it:
the cause is score effects (trailing team pulls the goalie), which two
independent processes cannot produce. Modelled as it happens — a one-goal
regulation margin equalises with probability 0.22, since P(margin=1) is
~0.29 and 0.165 + 0.29*0.22 = 0.230.
WHY IT MATTERS FOR THE PICK, not just the OT chip: every tie the sim
fails to produce is handed to the stronger side as a regulation win,
inflating favourites' moneyline probability — the exact error that makes
a model look like it has edge on chalk when it has none.

**HOME_ICE_GOALS = 0.15**, applied SYMMETRICALLY so it moves the margin
without inventing scoring (an earlier +0.20/-0.10 split was adding a
tenth of a goal to every game in the league). Calibrated to the home WIN
RATE, not a goal differential, because that is what the moneyline pick
depends on: 0.10 -> 0.531, 0.15 -> 0.546, 0.20 -> 0.564.

**Other structure:** multiplicative matchup (additive understates
mismatches and on a 2.5-goal scale that is most of the signal); xGF/60
carries 65% of attack because it stabilises in ~a quarter of the sample
GF/60 needs, actual goals keep 35% because finishing talent is real;
goaltending applied SEPARATELY from team defence since shot suppression
and shot stopping are different skills with different persistence.
Scaling from 5v5 rates to a full game is handled by calibrating to
NHL_AVG_GOALS rather than hard-coding an ice-time split, so a change in
source scale is absorbed instead of drifting silently.

Verified across all 992 matchups: total 6.10, OT 22.7%, p(home) range
33-67%.

Related: [[project_nhl_buildout_927]]
