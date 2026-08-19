/**
 * Universal stat / model / tier / signal glossary (2026-08-15).
 *
 * Single source of truth for every jargon term the app surfaces to
 * users — stat abbreviations across sports, model names, tier labels,
 * signal names, prop concepts. Rendered inline via <Explainer term="..."/>
 * so users tap the term where they see it and get a plain-english
 * one-liner — no glossary page, no leaving the screen.
 *
 * Each entry: { term: {label, help} }.
 *   - label — how it displays if you want normalized text (optional; the
 *             caller usually provides its own display text).
 *   - help  — one sentence: what it is + which direction is better.
 *
 * When adding sport-specific stats, prefer a unique key (e.g. "mlb.era")
 * to avoid collisions with same-abbrev-different-meaning across sports.
 * The <Explainer> component looks up the exact `term` prop against this
 * map — no fuzzy matching.
 */

export const GLOSSARY: Record<string, {label?: string; help: string}> = {
  // ─── TIERS (universal across sports) ─────────────────────────────
  'PRIME': {help: "Top tier — model, market, and cohorts all align strongly. Best plays we surface, but not free money."},
  'STRONG': {help: 'Second tier — clean edge with model + one supporting signal. Historically outperforms PRIME in some markets.'},
  'LEAN': {help: 'Third tier — edge is there but noise is higher. Smaller stakes or pass-if-book-moves.'},
  'PASS': {help: "We looked and didn't find enough edge. Publishing the pass is honesty, not laziness."},
  'SKIP': {help: "Model + market conflict enough that we can't recommend either side. Move on."},

  // ─── MODELS (MLB primary; used cross-sport where noted) ────────────
  'MC': {help: 'Monte Carlo simulation — plays out the game 10,000 times using team + lineup + pitcher inputs and reports the win/total distribution.'},
  'V4': {help: 'V4 XGBoost — our machine-learning runs model trained on ~5 seasons of MLB games. Best for direction, weaker for exact totals.'},
  'PANEL': {help: 'Panel projection — averages 4 external projection sources into a stable "market consensus" baseline for totals.'},
  'JERRY': {help: 'LLM synthesis narrator — reads all model + cohort + splits data and writes the plain-english call. Not a model itself, just the translator.'},
  'lens_consensus': {help: '5-of-6 model lenses agreeing on a side. Highest-hitting configuration we track.'},

  // ─── MLB PITCHING ─────────────────────────────────────────────────
  'ERA': {help: 'Earned Run Average — earned runs allowed per 9 innings. LOWER is better. Season stat, slow to react to recent form.'},
  'xERA': {help: 'Expected ERA — quality-of-contact-adjusted ERA. Strips out luck (BABIP swings, sequencing). More predictive than raw ERA.'},
  'SIERA': {help: 'Skill-Interactive ERA — like xERA but weighted more toward K/BB/GB rates. Best single-number pitcher-skill metric.'},
  'FIP': {help: 'Fielding Independent Pitching — only counts K/BB/HR/HBP. Removes defense. LOWER is better.'},
  'K/9': {help: 'Strikeouts per 9 innings. HIGHER is better; anything ≥10 is elite in 2026.'},
  'BB/9': {help: 'Walks per 9 innings. LOWER is better; ≥4 is a control-issue red flag.'},
  'WHIP': {help: 'Walks + Hits per Inning Pitched. LOWER is better; sub-1.10 is elite.'},
  'BABIP': {help: 'Batting Average on Balls In Play. League avg ~.290; pitcher extremes (>.320 or <.280) usually regress toward mean.'},
  'CSW%': {help: 'Called + Swinging Strike rate. Best in-season proxy for whiff/K upside. HIGHER is better.'},
  'HR/FB': {help: 'Home Runs per Fly Ball. Regresses hard to ~11-13% — extremes flag luck.'},

  // ─── MLB HITTING ──────────────────────────────────────────────────
  'wOBA': {help: 'Weighted On-Base Average — one number for total offensive value. HIGHER is better; .350+ is above average.'},
  'wRC+': {help: 'Weighted Runs Created Plus — offensive value vs league (100 = average, park-adjusted). HIGHER is better.'},
  'ISO': {help: 'Isolated Power = SLG - AVG. Measures raw power. HIGHER is better; .200+ is elite.'},
  'OPS': {help: 'On-Base + Slugging. Rough one-number offense stat. HIGHER is better; .800+ is above avg.'},
  'BABIP-hit': {help: 'Hitter BABIP. Elite hitters run high (.320+); regression watch when far above career norm.'},
  'Barrel%': {help: 'Percentage of batted balls hit with elite exit velo + launch angle. HIGHER = more damage per contact.'},
  'HardHit%': {help: 'Percentage of batted balls hit ≥95 mph. HIGHER is better; correlates with expected stats.'},
  'K%': {help: 'Strikeout rate. LOWER is better for hitters; league avg ~22%.'},
  'BB%': {help: 'Walk rate. HIGHER is better for hitters; 10%+ = strong plate discipline.'},

  // ─── MLB TEAM / SITUATIONAL ───────────────────────────────────────
  'BP xERA': {help: "Bullpen xERA — quality-of-contact-adjusted ERA for the team's bullpen. Weighs late-game exposure."},
  'park factor': {help: '100 = neutral. >100 = hitter-friendly, <100 = pitcher-friendly. Applies to runs, sometimes HR/K/BB separately.'},
  'OAA': {help: 'Outs Above Average — defensive metric. HIGHER is better; elite defense can suppress BABIP by 10-15 pts.'},

  // ─── NFL ──────────────────────────────────────────────────────────
  'EPA': {help: 'Expected Points Added — how much each play added to expected scoring. Best per-play efficiency metric. HIGHER = better offense / worse defense.'},
  'DVOA': {help: 'Defense-adjusted Value Over Average — Football Outsiders efficiency stat vs league (0 = avg, opponent-adjusted). HIGHER is better for offense; LOWER for defense.'},
  'YPA': {help: 'Yards Per Attempt (pass). HIGHER is better; 7.5+ is above avg for QBs.'},
  'YPC': {help: 'Yards Per Carry (rush). HIGHER is better; 4.5+ is above avg.'},
  'PROE': {help: 'Pass Rate Over Expected — how much a team passes vs game-script neutral baseline. Signals scheme identity.'},
  'ANY/A': {help: 'Adjusted Net Yards per Attempt — YPA adjusted for sacks + TDs + INTs. Best single QB efficiency stat.'},

  // ─── NCAAF ────────────────────────────────────────────────────────
  'SP+': {help: "Bill Connelly's tempo + opponent-adjusted efficiency rating. Top-5 = elite; used for matchup totals + spreads."},
  'FEI': {help: 'Fremeau Efficiency Index — drive-based efficiency (points per possession). Alternative to SP+.'},
  'Returning production': {help: 'Percent of last-season snaps returning by roster. Higher = more continuity, tends to over-perform preseason spreads.'},

  // ─── NCAAB ────────────────────────────────────────────────────────
  'AdjO': {help: 'Adjusted Offensive Efficiency — points scored per 100 possessions vs pace-neutral opponents. HIGHER is better.'},
  'AdjD': {help: 'Adjusted Defensive Efficiency — points allowed per 100 possessions vs pace-neutral opponents. LOWER is better.'},
  'AdjT': {help: 'Adjusted Tempo — possessions per 40 minutes vs pace-neutral opponents. HIGHER = faster game.'},
  'eFG%': {help: 'Effective FG% — adjusts FG% for 3PT worth 1.5x. Best one-number shooting stat.'},
  'TS%': {help: 'True Shooting % — accounts for 3PT + free throws. Most accurate scoring efficiency stat.'},

  // ─── NBA (offseason, will fill on relaunch) ──────────────────────
  'USG%': {help: 'Usage Rate — % of team plays a player finishes (shot / TO / FTA). HIGHER = more offensive load.'},
  'PER': {help: 'Player Efficiency Rating — per-minute efficiency (15 = avg). Rewards volume shooters.'},
  'PACE': {help: 'Possessions per 48 minutes. Higher = more scoring opportunities = higher totals.'},

  // ─── UFC (already inline in UfcFightDetail; kept here for cross-ref) ─
  'SLpM': {help: 'Significant Strikes Landed per Minute. HIGHER = more active + effective striker.'},
  'Str Acc': {help: 'Striking accuracy — % of significant strikes attempted that actually land. HIGHER is better.'},
  'Str Def': {help: "Striking defense — % of opponent's significant strikes avoided. HIGHER is better."},
  'SApM': {help: 'Significant Strikes Absorbed per Minute. LOWER is better.'},
  'TD Avg': {help: 'Takedowns landed per 15 minutes. Higher = more wrestling-heavy fighter.'},
  'TD Acc': {help: 'Takedown accuracy — % of takedown attempts that succeed. HIGHER is better.'},
  'TD Def': {help: "Takedown defense — % of opponent's takedown attempts stuffed. HIGHER is better."},
  'Sub Avg': {help: 'Submission attempts per 15 minutes. High = active sub threat.'},
  'Finish %': {help: 'Career wins ending by KO/TKO or submission (not decision). Higher = finisher.'},

  // ─── ODDS / MARKET CONCEPTS (universal) ──────────────────────────
  'EV': {help: 'Expected Value — long-run profit per $1 wagered if you keep taking this line. Positive = edge exists.'},
  'EV%': {help: 'EV expressed as % of stake. +5% EV means $5 profit per $100 bet over infinite tries.'},
  'implied prob': {help: 'The win probability the sportsbook line implies. -110 = 52.4%. Bet only when your model beats this.'},
  'juice': {help: "The sportsbook's built-in edge (the 'vig'). -110 means you risk $110 to win $100. Higher juice = worse price."},
  'RLM': {help: 'Reverse Line Movement — line moves opposite the public bet %. Signal that sharp money is on the unpopular side.'},
  'steam': {help: 'Sharp money hitting multiple books at once, moving the line fast. Following steam is the OG sharp signal.'},
  // Signal-tier definitions (unbranded per feedback_brand_attribution_803)
  'SIGNAL': {help: 'A single public-split source flags this side — one Split agrees. Directional lean, low confidence.'},
  'SHARP LEAN': {help: 'One of our Splits (public-money sources) shows sharp money on this side. Weakest tier — needs more agreement to lock.'},
  'SHARP CONFIRMED': {help: 'Two Splits independently agree sharps are on this side. Two-source confirmation = medium confidence.'},
  'SHARP_CONFIRMED': {help: 'Two Splits independently agree sharps are on this side. Two-source confirmation = medium confidence.'},
  'SHARP TRIPLE': {help: 'All three Splits agree — every public-money source we track says sharps hit this side. Highest confidence.'},
  'SHARP_TRIPLE_CONFIRMED': {help: 'All three Splits agree — every public-money source we track says sharps hit this side. Highest confidence.'},
  // RLM = reverse line move (line moves opposite public %)
  'RLM LEAN': {help: 'One Split shows Reverse Line Movement — line drifted opposite the public bet %. Sharp signal from a contrarian move.'},
  'RLM CONFIRMED': {help: 'Two Splits confirm the line moved AGAINST public tickets. Sharps are on the unpopular side.'},
  'RLM_CONFIRMED': {help: 'Two Splits confirm the line moved AGAINST public tickets. Sharps are on the unpopular side.'},
  'RLM TRIPLE': {help: 'All three Splits show RLM — every source we track says the line ran opposite the public. Textbook sharp fade.'},
  'RLM_TRIPLE_CONFIRMED': {help: 'All three Splits show RLM — every source we track says the line ran opposite the public. Textbook sharp fade.'},
  // Public-money-moves the line (usually the FADE side per our data)
  'PUBLIC LEAN': {help: 'One Split shows a public-money move — line moved WITH the crowd. Usually a FADE spot per our historical hit rate.'},
  'PUBLIC CONFIRMED': {help: 'Two Splits show a public-money move together. Line moved with tickets → the FADE side is what we back.'},
  'PUBLIC_CONFIRMED': {help: 'Two Splits show a public-money move together. Line moved with tickets → the FADE side is what we back.'},
  'PUBLIC TRIPLE': {help: 'All three Splits confirm a public move. Fading the popular side has historically been the +EV play in these spots.'},
  'PUBLIC_TRIPLE_CONFIRMED': {help: 'All three Splits confirm a public move. Fading the popular side has historically been the +EV play.'},
  'CONSENSUS': {help: 'Every Split agrees on the same side across markets — rare full-source alignment on the same team.'},
  'SOURCES DISAGREE': {help: 'The Splits point different ways — one has sharp on home, another on away. Skip unless you have your own read.'},
  'SOURCES_SPLIT': {help: 'The Splits point different ways — one has sharp on home, another on away. Skip unless you have your own read.'},
  'sharp %': {help: '% of the total money (handle) on this side, per book data. Above 65% = notably sharp; above 80% = heavy.'},
  'public %': {help: '% of bets (tickets) on this side — casual money. Divergence from sharp % is what creates the fade opportunity.'},

  // ─── PROP / BET STRUCTURE (universal) ────────────────────────────
  'refit': {help: "Our post-line model's revised probability after accounting for cohort adjustments + weather + late scratches."},
  'conviction': {help: "Our 0-100 confidence score for a pick. Combines model + market + cohorts. 70+ is where we start recommending."},
  'cohort': {help: 'Historical bucket of similar situations (e.g. "home dog +7 in prime time"). Tracks how the market prices vs how it plays out.'},
  'lens': {help: "One of six model perspectives (MC, V4, Panel, Jerry, cohort, refit). Agreement across lenses is the strongest signal."},

  // ─── MISC ────────────────────────────────────────────────────────
  'BACK': {help: "Jerry's call to bet the side. Verdict is not the same as PRIME/STRONG tier — verdict is direction, tier is confidence."},
  'FADE': {help: "Jerry's call to bet the OPPOSITE side (the market is wrong the other way)."},
  'NEUTRAL': {help: "Jerry says the market is close to fair — don't force a play."},
};

/** Get a term's plain-english help, or null if not glossed. */
export function explain(term: string): string | null {
  if (!term) return null;
  // Try exact, then case-insensitive fallback for user-typed variants
  return GLOSSARY[term]?.help
      ?? GLOSSARY[term.trim()]?.help
      ?? null;
}
