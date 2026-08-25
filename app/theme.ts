/**
 * The Sweat Locker — design tokens
 *
 * Single source of truth for every color in the app. Every screen and
 * component reads from here. No inline hex allowed once the palette-port
 * PR lands.
 *
 * Palette pulled from the brand logo (2026-07-30) — mahi gold + cyan on a
 * deep steel-blue ground. Two tokens (DANGER, LEAN) sit outside the strict
 * brand set because the brand doesn't ship a red or a mid-gold — chosen to
 * harmonize with the mahi/cyan family.
 *
 * Contrast notes (WCAG):
 *   GOLD on SURFACE       ≈ 3.4:1  — chip fills / large heads only, NOT body
 *   CYAN on SURFACE       ≈ 3.5:1  — same rule
 *   CREAM on BG           ≈ 11:1   — safe everywhere
 *   INK on GOLD chip fill = HIGH   — this is the pairing to use for chip labels
 *
 * Tier → token map (kept here so it never drifts across files):
 *   PRIME   → GOLD          (top signal, matches wordmark emphasis)
 *   STRONG  → CYAN          (secondary conviction)
 *   LEAN    → GOLD_DEEP     (drops off from PRIME, same family)
 *   READ    → SLATE_LINE    (analytical take, thin edge — not actionable)
 *   LIGHT   → TEXT_DIM      (muted grey — reads "informational")
 *   PASS    → TEXT_MUTE     (invisible-ish grey)
 *   LOSS    → DANGER
 *   LIVE    → DANGER (pulsing)
 *   ALIGNED → CYAN_BRIGHT   (subtle highlight variant)
 */

// ─── Brand palette (verbatim from logo extraction) ─────────────────────
export const BRAND = {
  goldMahi:   '#E5B227',
  goldDeep:   '#B8801A',
  cyan:       '#3BAECF',
  cyanBright: '#6FD8EC',
  steelBlue:  '#41586B',
  deepLocker: '#22323E',
  slateLine:  '#5C7285',
  cream:      '#F4EEDC',
  nearBlack:  '#15181B',
} as const;

// ─── Semantic tokens (what components reference) ───────────────────────
export const THEME = {
  // Surface hierarchy — three elevation levels + hero
  bg:          BRAND.deepLocker,    // #22323E — app canvas, tab bar, header
  bgModal:     '#1B2831',           // slightly deeper than bg — onboarding + modals for contrast
  surface:     BRAND.steelBlue,     // #41586B — game cards, RecapCard, betCard
  surfaceHero: '#2E4353',           // between bg + surface — hero panels (SWEAT CARD, POTD)
  surfaceAlt:  '#364954',           // chip pill fills, statBox, inputs, chipBtn
  border:      BRAND.slateLine,     // #5C7285 — visible dividers
  borderSoft:  '#364954',           // barely-there separator (same as surfaceAlt)

  // Text
  text:        BRAND.cream,         // #F4EEDC — primary
  textDim:     '#A8BAC7',           // secondary / meta / captions
  textMuted:   '#7C8FA0',           // placeholder / disabled — lighter than slateLine so it stays legible on surface
  ink:         BRAND.nearBlack,     // #15181B — text on GOLD/CYAN chip fills

  // Brand accents
  accent:      BRAND.goldMahi,      // #E5B227 — CTAs, PRIME tier, header logo, sport tab active, HR Watch
  accentDeep:  BRAND.goldDeep,      // #B8801A — LEAN, gradient stop, warn
  sharp:       BRAND.cyan,          // #3BAECF — STRONG tier, links, "info" sport pill
  highlight:   BRAND.cyanBright,    // #6FD8EC — subtle glow, ALIGNED chip

  // Outcome states (universal sportsbook convention — explicit red/green so
  // wins and losses read at a glance. NOT brand — semantic outcomes shouldn't
  // be forced through brand hues).
  win:         '#22C55E',           // clean green — WIN chip, hit, +EV, resultColor.win
  loss:        '#EF4444',           // clean red — LOSS chip, resultColor.loss
  push:        '#F0B34A',           // amber — PUSH / voided (visually distinct from win/loss)

  // Non-outcome states
  aligned:     BRAND.cyanBright,    // = highlight — model+book+lens agreement
  warn:        BRAND.goldDeep,      // = accentDeep — juice-fav caution
  muted:       '#A8BAC7',           // LIGHT tier chip, disabled UI

  // Combat/intensity — UFC banner stays red (universal combat-sport convention;
  // brand alignment would lose the visual cue)
  combat:      '#EF4444',           // = loss — UFC banner header

  // Book / third-party (contractual color; not in brand set)
  hrb:         '#FFB800',           // Hard Rock Bet yellow — HRB tiles + POTD tag only
} as const;

// ─── Tier → color map (single source; DO NOT redefine elsewhere) ───────
// Kill every inline `tierColor = ... PRIME ... STRONG ...` ternary and read
// from here. Audit found 12 inline definitions with 3 different STRONG hues.
// 2026-08-09: READ added as the "analytical take, thin edge" tier.
// Backend emits it from _jerry_fallback_for_game (conv < 50) and prop
// validators (post-collapse LEAN cap). Distinct color signals to the
// user: "we have a directional read here but no strong edge — bet at
// your own risk." Sits between LEAN and LIGHT visually.
export type Tier = 'PRIME' | 'STRONG' | 'LEAN' | 'READ' | 'LIGHT' | 'PASS';
export const TIER_COLOR: Record<Tier, string> = {
  PRIME:  THEME.accent,      // gold — matches wordmark emphasis
  STRONG: THEME.sharp,       // cyan
  LEAN:   THEME.accentDeep,  // deep gold — drops off from PRIME, same family
  READ:   BRAND.slateLine,   // slate blue-grey — analytical, non-actionable, distinct from STRONG's cyan
  LIGHT:  THEME.textDim,     // muted grey
  PASS:   THEME.textMuted,   // near-invisible grey
};

// Outcome → color map (matches resultColor() semantics)
export type Outcome = 'win' | 'loss' | 'push' | 'pending';
export const OUTCOME_COLOR: Record<Outcome, string> = {
  win:     THEME.win,
  loss:    THEME.loss,
  push:    THEME.push,
  pending: THEME.textMuted,
};

// Chip fill helpers — background at 14% alpha, border at 44% alpha, text solid.
// Matches the pattern already used in GameDetailV2's chipStyleFor. Consumers
// call chipStyle(THEME.accent) instead of hard-coding hex.
export const chipAlpha = (hex: string, aa: number) => {
  // hex → hex+aa (aa is 0–100 int like 14 → 24 hex)
  const h = Math.round((aa / 100) * 255).toString(16).padStart(2, '0');
  return `${hex}${h}`;
};
export const chipStyle = (color: string) => ({
  backgroundColor: chipAlpha(color, 14),
  borderColor:     chipAlpha(color, 44),
  borderWidth:     1,
  color,  // consumers spread this + read `.color` for the Text
});

// Wordmark gradient (splash / branded moments)
export const WORDMARK_GRADIENT = [BRAND.cream, BRAND.goldMahi, BRAND.goldDeep] as const;

// Legacy alias — kept during the port so files not yet ported still compile.
// Delete after 100% of index.tsx / GameDetailV2.tsx / etc. reference THEME.
export const COLORS = THEME;
