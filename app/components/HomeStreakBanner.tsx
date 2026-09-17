/**
 * HomeStreakBanner — dynamic 1-line banner surfacing what's currently hot.
 *
 * 2026-09-17 · v1.0.1 #4 Part B (hot-streak banner, per
 * project_home_screen_hot_streak_907).
 *
 * Sits at the top of the Home tab above the POTD hero. Silently hides when
 * nothing meets threshold — never surfaces filler stats.
 *
 * Eligible banner classes (highest-priority wins):
 *   1. Sharp Card 3-day hot streak    — 60%+ hit AND +5u+ over last 3 days
 *   2. Per-sport 7d run                — 65%+ hit AND 10+ picks AND net positive
 *   3. Ledger green streak             — 3+ consecutive positive-pnl days
 *   4. Daily Degen consecutive wins    — 2+ in a row
 *
 * Copy is composed client-side from templates (single source of truth here).
 * When the backend `home_streak_banners` table lands (v1.1 spec), swap the
 * template composer for a `.select()` on that table and delete this
 * scoring logic — the component render stays identical.
 *
 * Data path:
 *   * `surface_records` (window d7 + d30) — passed in via prop
 *   * `daily_surface_records` last ~5 days — fetched inside the component
 */
import React from 'react';
import {View, Text, TouchableOpacity, StyleSheet} from 'react-native';
import {THEME} from '../theme';

type SurfaceRecord = {
  sport?: string;
  surface?: string;
  window_key?: string;
  wins?: number;
  losses?: number;
  units_net?: number | string;
  picks_count?: number;
};

type DailyRecord = {
  record_date: string;
  surface: string;
  sport?: string | null;
  wins: number;
  losses: number;
  /** Net PnL for the day — leg wins × payoff − leg losses × stake.
   *  Despite the name, this is NOT gross return; it's already net.
   *  Verified against daily_surface_records 9/16 sharp_card MLB. */
  units_won?: number | string;
  units_bet?: number | string;
  pick_count?: number;
};

type Banner = {
  icon: string;
  text: string;
  priority: number;
  onPress?: () => void;
};

type Props = {
  supabase: any;
  /** Aggregate rollups the parent already fetched
   *  (surface_records rows keyed like `${sport}|${surface}|${window}`) */
  surfaceRecords?: Record<string, SurfaceRecord>;
  /** Optional taps — deep-link to the surface (e.g. Ladder banner opens
   *  Steam Room → Ladder). No-op when not provided. */
  onOpenSharp?: () => void;
  onOpenLadder?: () => void;
  onOpenLedger?: () => void;
  onOpenDailyDegen?: () => void;
};

const _num = (v: any): number => {
  const n = typeof v === 'number' ? v : parseFloat(v);
  return Number.isFinite(n) ? n : 0;
};

function _composeBanners(
  daily: DailyRecord[],
  agg: Record<string, SurfaceRecord>,
  handlers: {
    onOpenSharp?: () => void;
    onOpenLadder?: () => void;
    onOpenLedger?: () => void;
    onOpenDailyDegen?: () => void;
  },
): Banner[] {
  const out: Banner[] = [];
  const now = new Date();
  const daysAgo = (d: string) => {
    const t = new Date(d).getTime();
    return Math.floor((now.getTime() - t) / (86_400_000));
  };

  // 0. PRIME props recent record (v1.0.1 flagship angle). Uses the
  //    surface_records[MLB|prop_prime|d7] aggregate that our socials
  //    numbers come from. Andy 9/17: "prime MLB props record over L7D
  //    — that's the vision to promote whatever is hot recently." This
  //    IS the highest-priority banner — PRIME props at 82%+ is our
  //    strongest single stat. Fires at n>=30 + hit>=70% + net positive.
  for (const sp of ['MLB', 'NFL', 'NCAAF']) {
    const rec = agg[`${sp}|prop_prime|d7`];
    if (!rec) continue;
    const w = rec.wins || 0, l = rec.losses || 0;
    const total = w + l;
    const un = _num(rec.units_net);
    if (total >= 30 && w / total >= 0.70 && un > 0) {
      const pct = Math.round((w / total) * 100);
      out.push({
        icon: '🎯',
        text: `${sp} Prime Props L7D: ${w}-${l} (${pct}%), +${un.toFixed(1)}u`,
        priority: 100,   // top billing — flagship product angle
        onPress: handlers.onOpenSharp,
      });
    }
  }

  // 1. Sharp Card last-3d hot streak — sum wins/losses/pnl across recent
  //    graded days. Uses units_won - units_bet as PnL proxy.
  const sharpRecent = daily.filter((r) => r.surface === 'sharp_card' && daysAgo(r.record_date) <= 3
                                          && (r.sport === 'ALL' || (r.sport && ['MLB','NFL','NCAAF'].includes(r.sport))));
  if (sharpRecent.length) {
    // Prefer the ALL rollup rows if present; else sum per-sport rows.
    const allRows = sharpRecent.filter((r) => r.sport === 'ALL');
    const rows = allRows.length ? allRows : sharpRecent;
    let w = 0, l = 0, pnl = 0;
    for (const r of rows) {
      w += r.wins || 0;
      l += r.losses || 0;
      pnl += _num(r.units_won);
    }
    const total = w + l;
    if (total >= 5 && w / total >= 0.60 && pnl >= 5) {
      out.push({
        icon: '🔥',
        text: `The Sharp is hot — ${w}-${l} last 3d, ${pnl >= 0 ? '+' : ''}${pnl.toFixed(1)}u`,
        priority: 90,
        onPress: handlers.onOpenSharp,
      });
    }
  }

  // 2. Per-sport 7d run — 65%+ hit AND 10+ picks AND net positive.
  //    Reads from surface_records d7 aggregate (already fetched by parent).
  for (const sp of ['MLB', 'NFL', 'NCAAF']) {
    const k = `${sp}|sharp_card|d7`;
    const rec = agg[k];
    if (!rec) continue;
    const w = rec.wins || 0, l = rec.losses || 0;
    const total = w + l;
    const un = _num(rec.units_net);
    if (total >= 10 && w / total >= 0.65 && un > 0) {
      const pct = Math.round((w / total) * 100);
      out.push({
        icon: sp === 'NFL' ? '🏈' : sp === 'NCAAF' ? '🎓' : '⚾',
        text: `${sp} 7d: ${w}-${l} (${pct}%), +${un.toFixed(1)}u`,
        priority: 75,
        onPress: handlers.onOpenSharp,
      });
    }
  }

  // 3. Ledger green streak — 3+ consecutive positive-pnl days on ledger
  //    surfaces (chalk_parlay + prime_teased_single). Same pnl proxy.
  const ledgerBySport = daily
    .filter((r) => ['chalk_parlay', 'prime_teased_single', 'ledger'].includes(r.surface))
    .sort((a, b) => (a.record_date < b.record_date ? 1 : -1));  // newest first
  const dayPnls: Record<string, number> = {};
  for (const r of ledgerBySport) {
    // units_won is already the net pnl (leg wins × payoff − leg losses × stake),
    // not gross return. Verified 2026-09-17 against daily_surface_records
    // 9/16 sharp_card MLB row (5-10 W-L, units_bet=27, units_won=-13.1).
    dayPnls[r.record_date] = (dayPnls[r.record_date] || 0) + _num(r.units_won);
  }
  const sortedDays = Object.keys(dayPnls).sort().reverse();  // newest first
  let streak = 0;
  let streakPnl = 0;
  for (const d of sortedDays) {
    if (dayPnls[d] > 0) { streak++; streakPnl += dayPnls[d]; } else break;
  }
  if (streak >= 3) {
    out.push({
      icon: '📈',
      text: `Ledger ${streak}-day green streak — +${streakPnl.toFixed(1)}u`,
      priority: 60,
      onPress: handlers.onOpenLedger,
    });
  }

  // 4. Daily Degen consecutive wins — 2+ in a row.
  const degens = daily
    .filter((r) => r.surface === 'daily_degen')
    .sort((a, b) => (a.record_date < b.record_date ? 1 : -1));
  let degenWinStreak = 0;
  for (const r of degens) {
    if ((r.wins || 0) >= 1 && (r.losses || 0) === 0) degenWinStreak++;
    else break;
  }
  if (degenWinStreak >= 2) {
    out.push({
      icon: '🎯',
      text: `Daily Degen ${degenWinStreak} in a row — going for ${degenWinStreak + 1} tonight`,
      priority: 70,
      onPress: handlers.onOpenDailyDegen,
    });
  }

  return out.sort((a, b) => b.priority - a.priority);
}

export function HomeStreakBanner({
  supabase,
  surfaceRecords = {},
  onOpenSharp,
  onOpenLadder,
  onOpenLedger,
  onOpenDailyDegen,
}: Props) {
  const [banners, setBanners] = React.useState<Banner[] | null>(null);
  const [idx, setIdx] = React.useState(0);

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!supabase) return;
      try {
        const cutoff = new Date(Date.now() - 6 * 86_400_000).toISOString().slice(0, 10);
        const {data} = await supabase
          .from('daily_surface_records')
          .select('record_date,surface,sport,wins,losses,units_won,pick_count')
          .gte('record_date', cutoff)
          .in('surface', ['sharp_card', 'daily_degen', 'chalk_parlay', 'prime_teased_single', 'ledger']);
        if (cancelled) return;
        const composed = _composeBanners(
          Array.isArray(data) ? (data as DailyRecord[]) : [],
          surfaceRecords,
          {onOpenSharp, onOpenLadder, onOpenLedger, onOpenDailyDegen},
        );
        setBanners(composed);
      } catch {
        if (!cancelled) setBanners([]);
      }
    })();
    return () => { cancelled = true; };
  }, [supabase, surfaceRecords]);

  // Rotate through eligible banners every 8s. Silent-hide when none.
  React.useEffect(() => {
    if (!banners || banners.length <= 1) return;
    const t = setInterval(() => {
      setIdx((i) => (i + 1) % banners.length);
    }, 8000);
    return () => clearInterval(t);
  }, [banners]);

  if (!banners || banners.length === 0) return null;
  const b = banners[idx % banners.length];
  const inner = (
    <View style={styles.wrap}>
      <Text style={styles.icon}>{b.icon}</Text>
      <Text style={styles.text} numberOfLines={2}>{b.text}</Text>
    </View>
  );
  return b.onPress ? (
    <TouchableOpacity onPress={b.onPress} activeOpacity={0.8}>
      {inner}
    </TouchableOpacity>
  ) : inner;
}

const styles = StyleSheet.create({
  wrap: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingHorizontal: 12,
    paddingVertical: 8,
    marginBottom: 8,
    borderRadius: 8,
    backgroundColor: THEME.accent + '18',
    borderLeftWidth: 3,
    borderLeftColor: THEME.accent,
  },
  icon: {
    fontSize: 15,
  },
  text: {
    flex: 1,
    fontSize: 12,
    fontWeight: '700',
    color: THEME.accent,
    letterSpacing: 0.2,
  },
});
