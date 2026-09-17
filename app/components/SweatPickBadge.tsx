import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { THEME } from '../theme';

type Props = {
  reason?: string | null;
  size?: 'sm' | 'md';
};

export function SweatPickBadge({ reason, size = 'md' }: Props) {
  const isSm = size === 'sm';
  return (
    <View
      style={[styles.wrap, isSm && styles.wrapSm]}
      accessibilityLabel={reason ? `Sweat Pick — ${reason}` : 'Sweat Pick'}
    >
      <Text style={[styles.star, isSm && styles.starSm]}>★</Text>
      <Text style={[styles.label, isSm && styles.labelSm]}>SWEAT PICK</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    flexDirection: 'row',
    alignItems: 'center',
    alignSelf: 'flex-start',
    backgroundColor: THEME.accent,
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 4,
    gap: 4,
  },
  wrapSm: {
    paddingHorizontal: 6,
    paddingVertical: 2,
  },
  star: {
    fontSize: 12,
    color: THEME.bg,
    fontWeight: '700',
  },
  starSm: {
    fontSize: 10,
  },
  label: {
    fontSize: 10,
    letterSpacing: 0.8,
    color: THEME.bg,
    fontWeight: '800',
  },
  labelSm: {
    fontSize: 9,
    letterSpacing: 0.6,
  },
});
