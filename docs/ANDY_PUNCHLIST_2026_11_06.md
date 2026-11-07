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
| A7 | Multiple CBB teams have no stats / missing data, suspected name matching | OPEN |

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
NEW FINDING:  returns **HTTP 500, statement timeout** when
queried by sport, so the aggregated view cannot be read at all.

### A5 - PARTLY CONFIRMED. Not dark everywhere, but NBA and NHL fell off.
external_picks by sport per day: NBA went 131 (11-04) -> 20 -> 2 (11-06) ->
0 (11-07). NHL 31 -> 87 -> 3 -> 0. NCAAB and NCAAF stayed healthy
(238 and 48 on 11-06). MLB is 0 every day, which is correct in the
offseason. UFC has ONE source (bfo, 9 picks).
Source counts over the last 7 days: NCAAF 8, NFL 8, NCAAB 7, NBA 6, NHL 6,
UFC 1.
