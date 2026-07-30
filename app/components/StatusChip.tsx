/**
 * StatusChip — one primitive for every colored chip in the app.
 *
 * Replaces the ~15 inline chip patterns scattered across index.tsx (SWEAT
 * chip, LIVE pill, PRIME NRFI, UFC tier chip, playoff badge, alignment
 * verdict, etc). All read from theme tokens; no inline hex.
 *
 * Design contract:
 * - Consumers pick a variant that maps to a semantic role (tier / outcome /
 *   alignment / custom). Never pass raw colors unless variant='custom'.
 * - Hidden entirely when its data is null — so game cards can render a
 *   chip row of ~5 possible chips and the user sees only the ones with
 *   real data. That's how NFL/NCAAF/NHL cards get chips as soon as their
 *   primary_play + align_status populate.
 */
import React from 'react';
import { View, Text, StyleSheet, ViewStyle } from 'react-native';
import { THEME, TIER_COLOR, OUTCOME_COLOR, Tier, Outcome } from '../theme';

type Variant = 'tier' | 'outcome' | 'alignment' | 'score' | 'custom';

type Alignment = 'aligned_strong' | 'aligned' | 'aligned_soft' | 'split'
                | 'no_data' | 'no_ext_data' | 'no_money_data';

export type StatusChipProps = {
  variant: Variant;

  // Tier variant
  tier?: Tier | string | null;

  // Outcome variant
  outcome?: Outcome | 'live' | null;

  // Alignment variant
  alignment?: Alignment | null;
  alignmentCount?: number;         // e.g. "2/3 aligned"
  alignmentTotal?: number;

  // Score variant (SWEAT, NRFI etc.)
  score?: number | null;
  scoreLabel?: string;             // "SWEAT" / "NRFI"

  // Custom variant
  color?: string;

  // Common
  label?: string;                  // main text (e.g., "PRIME · BOS ML")
  value?: string | number | null;  // trailing text (e.g., "+150", "89%")
  icon?: string;                   // emoji prefix ("🎸", "⚾", "🏆")
  size?: 'sm' | 'md';              // default 'md'
  hideOnNull?: boolean;            // default true — hide when data missing
  style?: ViewStyle;               // container override
};

// ─── Alignment → chip config ─────────────────────────────────────────────
const ALIGN_CFG: Record<Alignment, {color: string; label: string; icon: string}> = {
  aligned_strong:  {color: THEME.win,       label: 'Strongly aligned', icon: '●●●'},
  aligned:         {color: THEME.win,       label: 'Aligned',          icon: '●●○'},
  aligned_soft:    {color: THEME.aligned,   label: 'Soft-aligned',     icon: '●○○'},
  split:           {color: THEME.warn,      label: 'Split',            icon: '◐'},
  no_data:         {color: THEME.textMuted, label: 'No data',          icon: '·'},
  no_ext_data:     {color: THEME.textMuted, label: 'No externals',     icon: '·'},
  no_money_data:   {color: THEME.textMuted, label: 'No money data',    icon: '·'},
};

// ─── Outcome → chip config ─────────────────────────────────────────────
const OUTCOME_CFG = {
  win:     {color: THEME.win,       label: 'WIN',     icon: '✓'},
  loss:    {color: THEME.loss,      label: 'LOSS',    icon: '✗'},
  push:    {color: THEME.push,      label: 'PUSH',    icon: '='},
  pending: {color: THEME.textMuted, label: 'PENDING', icon: '·'},
  live:    {color: THEME.loss,      label: 'LIVE',    icon: '●'},
};

// ─── Style helpers ───────────────────────────────────────────────────────
const withAlpha = (color: string, alphaHex: string) => color + alphaHex;

function resolveConfig(props: StatusChipProps): {color: string; text: string; iconText?: string} | null {
  const {variant} = props;
  if (variant === 'tier') {
    if (!props.tier) return null;
    const t = String(props.tier).toUpperCase() as Tier;
    const color = TIER_COLOR[t] || THEME.textDim;
    const text = props.label ?? t;
    return {color, text, iconText: props.icon};
  }
  if (variant === 'outcome') {
    const o = props.outcome;
    if (!o) return null;
    const cfg = OUTCOME_CFG[o];
    if (!cfg) return null;
    return {color: cfg.color, text: props.label ?? cfg.label, iconText: props.icon ?? cfg.icon};
  }
  if (variant === 'alignment') {
    const a = props.alignment as Alignment;
    if (!a) return null;
    const cfg = ALIGN_CFG[a];
    if (!cfg) return null;
    const suffix = (props.alignmentCount != null && props.alignmentTotal != null)
      ? ` ${props.alignmentCount}/${props.alignmentTotal}`
      : '';
    return {color: cfg.color, text: (props.label ?? cfg.label) + suffix, iconText: cfg.icon};
  }
  if (variant === 'score') {
    if (props.score == null) return null;
    // If a tier is provided, use that color; else neutral highlight
    let color: string = THEME.textDim;
    if (props.tier) {
      const t = String(props.tier).toUpperCase() as Tier;
      color = TIER_COLOR[t] || THEME.textDim;
    }
    return {color, text: props.scoreLabel ?? '', iconText: props.icon};
  }
  if (variant === 'custom') {
    if (!props.color || !props.label) return null;
    return {color: props.color, text: props.label, iconText: props.icon};
  }
  return null;
}

export default function StatusChip(props: StatusChipProps) {
  const cfg = resolveConfig(props);
  if (!cfg) return null;
  const size = props.size ?? 'md';
  const {color, text, iconText} = cfg;
  const fontSize = size === 'sm' ? 10 : 12;
  const paddingH = size === 'sm' ? 6 : 8;
  const paddingV = size === 'sm' ? 2 : 4;

  return (
    <View
      style={[{
        backgroundColor: withAlpha(color, '22'),
        borderColor:     withAlpha(color, '44'),
        borderWidth:     1,
        borderRadius:    8,
        paddingHorizontal: paddingH,
        paddingVertical:   paddingV,
        flexDirection:   'row',
        alignItems:      'center',
        gap:             4,
        alignSelf:       'flex-start',
      }, props.style]}
    >
      {iconText != null && <Text style={{color, fontSize, fontWeight: '800'}}>{iconText}</Text>}
      {text ? <Text style={{color, fontSize, fontWeight: '800'}}>{text}</Text> : null}
      {props.variant === 'score' && props.score != null && (
        <Text style={{color, fontSize: fontSize + 1, fontWeight: '800'}}>{props.score}</Text>
      )}
      {props.value != null && (
        <Text style={{color, fontSize, fontWeight: '800'}}>{props.value}</Text>
      )}
    </View>
  );
}
