/**
 * <TierChip tier="PRIME"/> — reusable tier badge with built-in tap-to-explain.
 *
 * 18+ inline tier chip renders scattered across index.tsx were duplicating
 * the same padding/color/tier→color mapping and none were tap-explainable.
 * This component centralizes that so PRIME/STRONG/LEAN/PASS/SKIP have
 * consistent color + shape everywhere AND every one is a tap-target that
 * opens a plain-english explanation.
 *
 * Migrate inline chips one site at a time — this component reads the same
 * TIER_COLOR palette used in the app so drop-in replacements look identical.
 */
import React from 'react';
import {View, Text, TouchableOpacity} from 'react-native';
import {explain} from '../lib/glossary';

// Palette matches the app-wide TIER_COLOR map. Kept local so this file
// stays self-contained.
const TIER_COLORS: Record<string, string> = {
  PRIME:  '#4ade80',
  STRONG: '#5ea9e6',
  LEAN:   '#f5b800',
  PASS:   '#7a92a8',
  SKIP:   '#7a92a8',
};

type Props = {
  tier: string;                         // 'PRIME' | 'STRONG' | 'LEAN' | 'PASS' | 'SKIP' | anything
  size?: 'sm' | 'md' | 'lg';            // sm=chip, md=badge, lg=hero
  outlined?: boolean;                   // border + transparent bg vs filled
  showInfoDot?: boolean;                // show ⓘ next to tier text (default true)
  // Override colors when the caller wants a different palette
  color?: string;                       // text + border color
  bgColor?: string;                     // background color
  // External expansion control — parent can own if it wants only-one-open
  isOpen?: boolean;
  onToggle?: () => void;
};

const SIZES = {
  sm: {padH: 6, padV: 2, font: 9,  radius: 4, dotSize: 8},
  md: {padH: 8, padV: 3, font: 11, radius: 5, dotSize: 9},
  lg: {padH: 12, padV: 6, font: 14, radius: 7, dotSize: 11},
};

export default function TierChip({
  tier, size = 'sm', outlined = false, showInfoDot = true,
  color: colorOverride, bgColor: bgOverride,
  isOpen, onToggle,
}: Props) {
  const [selfOpen, setSelfOpen] = React.useState(false);
  const tierUp = String(tier || '').toUpperCase();
  const help = explain(tierUp);
  const color = colorOverride || TIER_COLORS[tierUp] || '#7a92a8';
  const bg = bgOverride || (outlined ? 'transparent' : color + '22');
  const border = outlined ? color + '77' : color + '44';
  const s = SIZES[size];
  const open = isOpen !== undefined ? isOpen : selfOpen;
  const toggle = () => onToggle ? onToggle() : setSelfOpen(o => !o);

  const chip = (
    <View style={{
      backgroundColor: bg, borderWidth: 1, borderColor: border,
      paddingHorizontal: s.padH, paddingVertical: s.padV,
      borderRadius: s.radius, flexDirection: 'row', alignItems: 'center', gap: 3,
    }}>
      <Text style={{color, fontSize: s.font, fontWeight: '800', letterSpacing: 0.4}}>{tierUp}</Text>
      {help && showInfoDot && (
        <Text style={{color: color + 'BB', fontSize: s.dotSize, fontWeight: '700'}}>ⓘ</Text>
      )}
    </View>
  );

  if (!help) return chip;

  return (
    <View>
      <TouchableOpacity onPress={toggle} hitSlop={{top: 6, bottom: 6, left: 6, right: 6}}>
        {chip}
      </TouchableOpacity>
      {open && (
        <View style={{
          marginTop: 5, paddingVertical: 6, paddingHorizontal: 10,
          backgroundColor: color + '15', borderRadius: 6,
          borderLeftWidth: 2, borderLeftColor: color,
          maxWidth: 260,
        }}>
          <Text style={{color: '#9db1c3', fontSize: 11, lineHeight: 15}}>{help}</Text>
        </View>
      )}
    </View>
  );
}
