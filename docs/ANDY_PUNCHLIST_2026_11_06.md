# ANDY'S PUNCHLIST — raised 2026-11-06 evening

Items Andy observed IN THE APP that I missed because I stayed in the DB and
never checked what the app renders. Each gets investigated and either fixed
or shown to be fine, in order, with evidence. Status is updated in place.

**Rule for this list:** nothing is marked VERIFIED without a query or a
render path traced. Andy's instruction on item 6 applies to all of them —
"not certain so don't just fucking believe that, find the fuck out."

| # | item | status |
|---|---|---|
| A1 | In-app fires/alerts surfacing to users that I never looked at | OPEN |
| A2 | Money flow not listed in app for any NBA or NHL game, probably NCAAB too | OPEN |
| A3 | NCAAB games missing spreads | OPEN |
| A4 | NCAAB still surfacing super-juiced chalk (e.g. Ohio State ML) | OPEN |
| A5 | Externals likely gone dark across some sports | OPEN |
| A6 | Purdue: app shows Purdue +21, pre-game Jerry read says Purdue −19.5; no situational records | OPEN |
| A8 | Why was USF ML the POTD today | **ROOT CAUSE FOUND** |
| A7 | CBB teams with no stats | **ROOT CAUSE FOUND** - 31 of 34 already in the alias table |

---

## A1 — In-app fires / alerts

WHAT ANDY SAW: alerts firing to users right now that I did not look at even
after being asked.

INVESTIGATION: pending.

---

## A2 — Money flow absent for NBA / NHL / NCAAB in the app

WHAT ANDY SAW: no money flow shown on any NBA or NHL game, likely NCAAB too.

WHAT I ALREADY KNOW (from tonight's sweep, `docs/TRIAGE_2026_11_06.md`):
`public_splits_archive` rows by sport since 09-01 — NCAAF 115,421 · NFL
38,621 · MLB 31,474 · NHL 8,840 · NCAAB 2,855 · **NBA 0**. So NBA has no
splits data at all, which matches what Andy sees. NHL has 8,840 rows, so for
NHL the question is the RENDER path, not the data.

INVESTIGATION: pending — needs the app's money-flow query traced per sport.

---

## A3 — NCAAB games missing spreads

WHAT I ALREADY KNOW: `ncaab_game_context` `close_spread` / `close_total`
sat at **61%** fill since 08-01, and 0% of `ncaab_game_results` rows carry
`projected_spread`. So missing spreads are consistent with what is stored.

INVESTIGATION: pending.

---

## A4 — Super-juiced NCAAB chalk still surfacing

WHAT I ALREADY KNOW: tonight's ML ceiling fix capped NCAAB moneylines to
COVERAGE and took publishable MLs from 5 to 0 on the 11-06 board, and the
resolver's own juice cap suppresses ML past −300. But that was applied to
the 32 games that had NOT tipped; the 27 already in progress kept their
published picks. And the READS still name `ml` on 11 of 32 upcoming games
(R189), which is a user-visible surface.

INVESTIGATION: pending — find whether an Ohio State ML is live anywhere.

---

## A5 — Externals dark in some sports

WHAT I ALREADY KNOW: `external_picks` holds 19,168 rows across 8 sources
with `game_date` through 2026-11-12, by sport MLB 9,494 · NCAAF 3,917 · NFL
1,870 · NHL 1,778 · NCAAB 1,012 · NBA 999. That is lifetime, NOT recency —
a sport can be dark for a week and still look healthy in a lifetime count.
Needs a per-sport, per-day freshness check.

INVESTIGATION: pending.

---

## A6 — Purdue spread sign mismatch, and no situational records

WHAT ANDY SAW: app shows **Purdue +21** while the pre-game Jerry read says
**Purdue −19.5**. Also no situational records on the game.

WHY THIS IS SERIOUS: a sign flip on a published spread is the
`feedback_magnitude_cannot_express_a_side` class — the two numbers do not
disagree by a point, they disagree about WHO IS FAVOURED. One of the two
surfaces is telling a user to back the wrong team.

INVESTIGATION: pending — this one goes first.

---

## A7 — CBB teams with no stats, suspected name matching

WHAT ANDY SAID: "likely due to name matching issue (not certain so don't
just fucking believe that, find the fuck out)."

WHAT I ALREADY KNOW: `ncaab_team_stats` holds 1,094 rows fresh to 11-06 and
`ncaab_team_efficiency` 1,356 rows; `ncaab_team_aliases` held 1,404 keys for
365 teams as of 11-03. Tonight's own roster work hit 5 of 365 teams absent
from ESPN. So the candidate causes are (a) alias gaps, (b) teams genuinely
absent from a source, (c) a resolver only some callers use — the
`feedback_a_blocklist_duplicating_a_lookup_table` pattern, where the client
had its own 190-name list while the server had all 1,404.

INVESTIGATION: pending — must distinguish those three, not assume (a).

---

## A6 RESULT — three defects, one of them mine, price defect FIXED

### A6-1 · PUBLISHED SPREADS CARRIED MONEYLINE PRICES — FIXED

Andy's Purdue game, `ncaab_20261106_Valparaiso_Purdue`. The read said
`Purdue -19.5` and carried **price_american = −4500**. `line_history` holds
the real quotes for that exact market and line:

    spread / home / -19.5   betmgm -110 · fanduel -110 · espnbet -105 · draftkings -108
    ml     / home           betmgm -3000 · fanduel -4500 · espnbet -5000

−4500 is the fanduel **moneyline**. The pick was priced as an ML, the market
then moved to a spread, and nothing invalidated the price.

**Scope, measured across all sports on side/total reads since 09-01:**

    sport   side/total reads   impossible price   pct
    MLB                   67                  0   0.0%
    NCAAF                246                  0   0.0%
    NFL                   90                  0   0.0%
    NBA                   76                  1   1.3%
    NHL                   63                  2   3.2%
    NCAAB                 42                 15  35.7%

**And 15 of the 18 are NCAAB from 11-06 — caused by my own ML ceiling** ,
which pushed picks off the moneyline onto the spread hours after they were
priced. I made this worse tonight.

Why it bit only NCAAB: the invalidation written on 10-27 lives in
`sync_jerry_reads_from_ctx.py`, which nulls the price when IT changes a
read's market — and that works, which is why MLB/NCAAF/NFL are at 0 of 403.
But NCAAB's sync skips every read whose `long_read` is real prose (all of
them), so it never ran, and the market was moved by the RESOLVER instead.

**Fix at the chokepoint, not in one writer.** `price_picks.price_impossible()`
now refuses to freeze a price that cannot belong to its market — a spread,
run line, puck line or total is a juice market priced inside ±300, a
moneyline is not. `_has_started` still protects a started game's price, but
only when it could have been that market's price at all. 10 unit cases pass
(−20000 on rl → impossible; −20000 on ml → fine; −300 on spread → fine;
−301 → impossible; None → fine).

Cleared all 18 and re-priced. Readback: **0 impossible prices remaining**,
and the eight NCAAB spreads now read:

    Kansas -28.5    -110 (5 books)      Purdue -19.5    -109 (4 books)
    Virginia -34.5  -105 (5 books)      Baylor -38.5    -109 (4 books)
    UCLA -33.5      -110 (5 books)      Villanova -34.5 -110 (4 books)
    Miami FL -38.5  -112 (4 books)      Georgia -24.5   -110 (5 books)

3 of the 18 had already been GRADED against the wrong price, so those
records were wrong until now.

### A6-2 · projected_spread IS ON THE OPPOSITE SIGN CONVENTION — OPEN

`ncaab_game_context.projected_spread` disagrees in SIGN with the adjusted
efficiency gap on **122 of 122 rows that carry both** — 100%:

    game                      proj_spread   adjEM gap   close_spread
    Delaware @ Wisconsin             +8.7       +26.1         -25.5
    Northern Il @ Dayton             +8.6       +25.9         -19.5
    Lafayette @ Saint Joseph's       +6.7       +20.1         -17.5
    Mercer @ Davidson                +4.2       +12.8         -11.5
    Valparaiso @ Purdue              +6.9       +20.7         -19.5

`close_spread` and the adjEM gap agree with each other on every row. Only
`projected_spread` is inverted — and its magnitude is compressed about 3x
(+6.9 where the gap says 20.7). So it is not merely a sign flip, the number
itself does not reproduce from its own inputs.

This is the [[feedback_magnitude_cannot_express_a_side]] class: two surfaces
do not disagree by a point, they disagree about WHO IS FAVOURED.

### A6-3 · WHERE "Purdue +21" COMES FROM — OPEN

Purdue's adjEM gap is **20.71**, which rounds to the +21 Andy saw, against a
read of `Purdue -19.5`. So the app is rendering a MARGIN (Purdue by ~21)
in a slot a user reads as a SPREAD (Purdue +21, the underdog). Needs the
render path traced. Situational records absent on the same card — NCAAB
`away_l10` and `home_l10` are **0% populated** (null audit), which is the
likely cause and is checkable.

---

## A2 / A3 / A4 / A5 RESULTS

### A4 - CONFIRMED, AND LIVE. Juiced NCAAB chalk is still on the reads.
Andy named Ohio State and it is there.

    2026-11-06  57 NCAAB reads, 18 still call ml, 5 priced worse than -300
       Wisconsin ML      -10000   conv 51
       Iowa ML           -10000   conv 50
       Texas A&M ML      -13333   conv 50
       North Carolina ML  -5833   conv 51
       Oregon ML          -2100   conv 51
       Ohio St. ML        price NULL, conv 52     <- Andy called this exactly
    2026-11-07  34 reads, 8 call ml, 6 worse than -300
       Utah St. ML       -20000   Dayton ML -10000   Ohio ML -800
       UNLV ML            -865    Davidson ML lean -709   Longwood ML lean -874

Tonight ml ceiling capped the primary_play TIER, and the resolver already
suppresses ML past -300 in that path. Neither touches the READ, which is the
surface a user opens. This is R189 with a price attached: the reads keep
their own call_market from the generator, the sync that would reconcile them
writes 0 rows (prose guard), and regeneration needs the LLM key.
NOT a price-impossible case - -10000 IS a real moneyline price. The defect is
that we surface an ML at all at that price, against the 11-02 measurement
where NOT ONE NCAAB favourite-ML band clears breakeven.

### A3 - PARTLY CONFIRMED, and the gate is holding.
close_spread is posted on 36 of 59 games today (61%) and 25 of 39 tomorrow
(64%). So Andy is right that spreads are missing - on 39% of the board. But
of the 23 games with no close_spread, publishable picks: 0. The
posted-market gate is doing its job; the display just has nothing to show.

### A2 - CONFIRMED and worse than described.
Splits rows per sport per day: NCAAF 827-2,216, NFL 232-911, NHL 26-256,
NCAAB 0-900 (zero on 10-29, 10-31, 11-07; six on 10-30), **NBA has no
column at all - zero rows ever**. So NBA money flow cannot render because it
does not exist, NCAAB is erratic to the point of unusable, and NHL is an
order of magnitude below NCAAF.
NEW FINDING: `public_splits_v2` returns **HTTP 500, statement timeout** when
queried by sport, so the aggregated view cannot be read at all.

### A5 - PARTLY CONFIRMED. Not dark everywhere, but NBA and NHL fell off.
external_picks by sport per day: NBA went 131 (11-04) -> 20 -> 2 (11-06) ->
0 (11-07). NHL 31 -> 87 -> 3 -> 0. NCAAB and NCAAF stayed healthy
(238 and 48 on 11-06). MLB is 0 every day, which is correct in the
offseason. UFC has ONE source (bfo, 9 picks).
Source counts over the last 7 days: NCAAF 8, NFL 8, NCAAB 7, NBA 6, NHL 6,
UFC 1.

---

## A7 RESULT — it IS name matching, and the mapping already exists

Andy: "likely due to name matching issue (not certain so don't just fucking
believe that find the fuck out)." Found out. All three candidate causes
separated.

**34 of 203 teams on the 11-06/07 board have no `ncaab_team_stats` row and
no `ncaab_team_efficiency` row. Every single one carries a MASCOT:**

    Arizona St Sun Devils · Florida St Seminoles · Oklahoma St Cowboys
    San Diego St Aztecs · Oregon St Beavers · Washington St Cougars
    Mt. St. Mary's Mountaineers · Texas A&M-CC Islanders · ... 34 total

The stats tables key on clean names (`Arizona St.`, 367 distinct teams).
`ncaab_game_context.home_team/away_team` carries the mascot-suffixed
Odds-API name for these 34.

### Cause, by team

| cause | n | meaning |
|---|---|---|
| **(c) RESOLVER NOT USED** | **31** | the alias table already maps it |
| (a) ALIAS GAP | 1 | `Florida Int'l` — needs one alias added |
| (b) ABSENT at source | 2 | `IUPUI Jaguars`, `West Florida Argonauts` |

`ncaab_team_aliases` holds **365 rows and 1,405 lookup keys** across
`canonical_name`, `kenpom_name`, `bart_name`, `odds_api_name` and
`alt_names`. 20 of the 34 resolve on a DIRECT key hit; another 11 resolve
through an abbreviation already in the table (`CSU Northridge`→`CSUN`,
`GW`→`George Washington`, `Miss Valley St`→`Mississippi Valley St.`,
`Mt.`→`Mount St. Mary's`, `Texas A&M-CC`→`Texas A&M Corpus Christi`).

**So this is the `feedback_a_blocklist_duplicating_a_lookup_table` pattern
again** — the server has all 1,405 keys and whatever populates the stats
panel is not going through them. Fix is to resolve at the one chokepoint,
not to add aliases.

### The one real trap, and I nearly shipped it myself

`Florida Int'l Golden Panthers` has no alias entry, and a naive mascot strip
resolves it to **`Florida`** — a different school with its own stats. That
is `feedback_prefix_strip_can_name_another_school` exactly. It needs an
explicit `Florida Int'l` → `FIU` alias, never a strip.

And a note on my own method: I added a refusal rule that rejects a strip
whose answer does not share the input's FIRST TOKEN. It fired on four
CORRECT resolutions — `CSU`→`CSUN`, `GW`→`George Washington`,
`Miss`→`Mississippi`, `Mt.`→`Mount`. First-token equality breaks on
abbreviation EXPANSION, so the memory rule ("refuse a strip whose dropped
tokens name a different school") is the subtler and correct test, not the
one I coded. Recorded so the next version of that guard is not built on the
wrong rule.

### Remaining two are genuinely absent, and that is fine
`IUPUI Jaguars` (the school rebranded to IU Indianapolis) and
`West Florida Argonauts` (Division II). Both should be alias'd or excluded
deliberately rather than silently rendering a blank panel.

---

## A8 — WHY WAS SOUTH FLORIDA ML THE POTD ON 11-06?

`South Florida @ East Carolina`, published 12:56 UTC as
`South Florida ML (Jerry 79/100)` at **+112**, `daily_best_bet_history` id
1130, receipt 5218724.

### What the engine actually had

    close_spread            -1.5      (ECU favoured by 1.5)
    open_spread             +1.5      <- the line moved A FULL 3 POINTS to ECU
    close_home_ml           -130      open_home_ml -108   <- money came in on ECU
    close_away_ml           +107
    projected_spread        -3.03
    model_pred              ECU 24.5, USF 25.9   (USF by 1.4 — a coin flip)
    signal_confluence_net   0
    sweat_score (ctx)       65        <- note the POTD published 79
    away_covers_as_dog_pct  66.7      (2 of 3)
    away_sor / home_sor     5.767 / 0.643
    away_sos / home_sos     -0.499 / +0.625   <- USF's edge is on a WEAKER schedule
    cohort_tags             ncaaf_home_fav, ncaaf_grinder
    confluence breakdown    hfa→home, explosiveness→home,
                            sp_plus→away, success_rate→away      = 2-2 SPLIT

`primary_play`: `ml / AWAY / STRONG / conviction 79 / **score 0.15**`, with
**only two supporting signals**:

    ncaaf_projected_spread         model        AWAY_ML  w=0.42  n=101  contrib 0.10
    ncaaf_home_dog_letdown_fade    situational  AWAY_ML  w=0.22  n=21   contrib 0.07

and the sub: **"South Florida — 81% vs 53% implied — +28.2pp edge"**.

### THE ROOT CAUSE: conviction is a display score consumed as a WIN PROBABILITY

`conviction_scale.py:152`, in the repo already:

> `conviction` is consumed as a WIN PROBABILITY, not as a display score:
> `model_edge.edge_pp()` is literally `conviction - implied_prob*100` …
> Rescaling NBA conviction from 51 to 75 would print a ~20pp edge on every
> NBA pick, switch off the cap that is currently telling the truth, and
> **manufacture plays out of nothing.**

That is precisely what happened here. Conviction 79 is read as "USF wins
79% of the time". The market at +107/+112 implies ~48%. So the engine
printed a **+28.2pp edge on a pick-em dog**, and that edge is what made it
Play of the Day.

And the same file's conviction formula carries its own warning:

    base = 50 + min(win_score * 10, 30)
    margin_boost = min(max(margin, 0) * 20, 15)
    classes_boost = 0      # zeroed 09-29, measured inversely calibrated

> win_score … is ALSO non-monotonic on the same sample: 0.0-0.3 → 64.2%
> (n=95), 1.0-1.5 → 46.0% (n=50). So **conviction still does not rank pick
> quality and must not be trusted for stake sizing** until `base` has its
> own study.

So the POTD is selected on a number the codebase documents as not ranking
pick quality, and then that number is spent a second time as a probability.

### Four more things wrong with this specific pick

1. **Score 0.15 is BELOW NCAAF's own median.** `SIGNAL_SPANS` measured
   NCAAF at score p50 **0.43**, p90 1.43, max 3.51 (10-30). A 0.15 pick is
   in the bottom half of the sport's own distribution and was published as
   the single best play of the day.
2. **The engine's own confluence is neutral** — `signal_confluence_net = 0`,
   a literal 2-2 split of its four lenses.
3. **Two signals, one under-sampled.** `ncaaf_home_dog_letdown_fade` carries
   **n=21**, below the n≥30 bar this product uses for any quoted rate.
4. **The market moved against us all day.** Open +1.5 → close −1.5 and the
   home ML −108 → −130. We published the side money was leaving.
5. **The SOR case is a schedule artifact.** USF 5.767 vs ECU 0.643 looks
   decisive until you read the next row: USF's SOS is **−0.499** against
   ECU's **+0.625**.

### And the advertised price is wrong

The POTD published **+112**. The Jerry read for the same pick priced it at
**−104 across 11 books, best −103**. `open_away_ml` was −112 and
`close_away_ml` +107. So we advertised a plus price on a pick the books had
at roughly even-money-minus, and +112 happens to be |−112| — the opening
away ML with its sign lost, which is the
`feedback_magnitude_cannot_express_a_side` shape. At +112 the implied
breakeven is 47.2%; at −104 it is 51.0%. Every ROI this pick ever reports
will be computed off a price nobody could get.

### Overall thought

**This should not have been Play of the Day, and the reason is structural,
not a bad read.** The analytical case is not stupid — USF's offensive EPA
edge and a +6.2 SP+ gap are real inputs, and `away_covers_as_dog_pct` of
66.7% is at least directionally interesting. But the engine's *own* evidence
was a 2-2 confluence, a below-median score, two signals, and a line moving
away from us. Nothing in that set says "best play of the day".

What made it POTD was conviction 79 being treated as a 79% win probability
against a 48% market — a 28-point edge the model has no basis for. This is
the same failure as the NCAAB `/3.0` divisor fixed earlier tonight: a number
that is wrong by construction gets fed into an edge calculation, the edge
comes out enormous, and the gate that should have stopped it instead
promotes it. One produced phantom 11-point spread edges across the whole
NCAAB board; this produces phantom 28-point probability edges on football.

**Fix, in order:**
1. POTD must rank on `conviction_rank`, not `conviction` — that is exactly
   what `conviction_scale.py` built it for, and the POTD still ranks on the
   raw number.
2. A plausibility guard on `edge_pp`: a model edge past ~10pp against a
   liquid market with 11 books is a bug, not an edge
   (`feedback_model_output_needs_a_plausibility_guard`).
3. POTD requires more than two supporting signals, and none under n=30.
4. Fix the published price to the real one (−104, 11 books) and find why
   `odds_american` took the opening away ML with the sign dropped.
