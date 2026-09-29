/**
 * Expo dynamic config — exists solely to stop a provider key shipping in the
 * binary again.
 *
 * ── WHY ──
 * On 2026-09-28 a leaked Anthropic key cost $548.41 in a single day: 30
 * auto-recharges against a normal rate of one every twelve days. The key was
 * set as EXPO_PUBLIC_ANTHROPIC_API_KEY, and Expo INLINES every EXPO_PUBLIC_*
 * value into the JS bundle at build time, so the raw key shipped inside App
 * Store build 1.0.1 and anyone who downloaded the app could read it out.
 * 99.1% of that day's tokens were on Opus and Sonnet models the app cannot
 * request — our real traffic was the 0.9% Haiku line.
 *
 * The claude-proxy and odds-proxy edge functions had already removed the need
 * for those keys client-side, and the app had zero direct calls left to either
 * provider. The declarations were dead code. But a dead declaration is still
 * a shipped secret, because the bundler does not care whether the value is
 * read — only that it is referenced.
 *
 * A comment on 2026-09-22 made exactly this point about a retired
 * balldontlie key ("EXPO_PUBLIC_* values are compiled into the IPA and
 * extractable, so a retired key must not keep shipping in the binary") and
 * three live keys kept shipping anyway. A comment is not a control. This is.
 *
 * ── WHY A DENYLIST, NOT A PATTERN ──
 * Blocking every EXPO_PUBLIC_*_KEY would be wrong and would train people to
 * bypass the guard. Two of ours are public by design:
 *
 *   EXPO_PUBLIC_SUPABASE_ANON_KEY   public by design; RLS is the control
 *   EXPO_PUBLIC_REVENUECAT_KEY_IOS  public SDK key by design
 *
 * So this names the provider secrets that must never be client-side. Add to
 * BLOCKED when a new paid provider appears.
 *
 * ── KENPOM IS A WARNING, NOT A FAILURE ──
 * EXPO_PUBLIC_KENPOM_KEY is genuinely still in use: five call sites hit
 * kenpom.com directly, and nothing server-side populates kenpom_cache, so
 * the client is its only writer. Failing the build on it would block every
 * release for a problem that needs a proxy or a pipeline puller to fix
 * properly. NCAAB opens 2026-11-03, so it warns loudly until then rather
 * than silently persisting.
 */

const BLOCKED = [
  'EXPO_PUBLIC_ANTHROPIC_API_KEY',
  'EXPO_PUBLIC_ANTHROPIC_KEY',
  'EXPO_PUBLIC_ODDS_API_KEY',
  'EXPO_PUBLIC_OPENAI_API_KEY',
  // Promoted from WARN to BLOCKED on 2026-09-28, once kenpom_pull.py existed
  // to fill kenpom_cache server-side and the client's five direct
  // kenpom.com calls were removed. There is no longer any reason for this to
  // be readable by the app.
  'EXPO_PUBLIC_KENPOM_KEY',
  // Added 2026-09-29 during the 1.0.2 build preflight. balldontlie was
  // retired as a data source on 2026-09-22 and the app has ZERO calls left
  // to it — every reference in index.tsx is a comment explaining its removal.
  // The key was still set in all THREE EAS environments (production, preview,
  // development) and therefore still being inlined into every build.
  //
  // That is precisely the failure this file was created for: the 09-22 note
  // quoted at the top of this comment block said a retired key "must not keep
  // shipping in the binary", and a week later it was still shipping. Dead
  // credentials are the easiest kind to forget, because nothing breaks when
  // they leak — until someone uses them.
  'EXPO_PUBLIC_BDL_API_KEY',
];

// Known client-side secrets that cannot be blocked yet. Empty today — KenPom
// was the last one and moved server-side on 2026-09-28. Keep the mechanism:
// the next provider that cannot be proxied immediately belongs here, loudly,
// rather than living in a comment nobody reads.
const WARN = [];

module.exports = ({ config }) => {
  const present = BLOCKED.filter((k) => {
    const v = process.env[k];
    return typeof v === 'string' && v.trim() !== '';
  });

  if (present.length) {
    throw new Error(
      '\n\n' +
      '  BUILD BLOCKED — provider secret would be bundled into the app\n' +
      '  ---------------------------------------------------------------\n' +
      present.map((k) => `    ${k}`).join('\n') + '\n\n' +
      '  Expo inlines every EXPO_PUBLIC_* value into the JS bundle, so these\n' +
      '  would ship inside the IPA and be extractable by anyone who downloads\n' +
      '  the app. This is what cost $548.41 on 2026-09-28.\n\n' +
      '  Fix: remove these from .env (and from EAS secrets), and call the\n' +
      '  provider through its edge function instead —\n' +
      '    Anthropic -> supabase/functions/claude-proxy\n' +
      '    Odds API  -> supabase/functions/odds-proxy\n' +
      '  The real keys belong in edge-function secrets only.\n'
    );
  }

  for (const [k, why] of WARN) {
    if (process.env[k]) {
      console.warn(`\n  ⚠ ${k} is set and WILL be bundled into this build.\n` +
                   `    ${why}\n`);
    }
  }

  // app.json stays the single source of truth for the config itself; this
  // file only gates the build.
  return config;
};
