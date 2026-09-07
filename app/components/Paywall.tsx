/**
 * Paywall — custom-styled subscription screen.
 *
 * Reads offerings from SubscriptionContext (which pulls from RevenueCat).
 * Shows monthly + annual options with savings call-out, restore button,
 * Terms + Privacy links (Apple requires both visible before subscribe).
 *
 * Triggered when a free user hits gated content (POTD, picks, sweat
 * card data, etc.). The gating logic lives in the calling component:
 *   const { isPro } = useSubscription();
 *   if (!isPro) return <Paywall onDismiss={...} />;
 *   return <PremiumContent ... />;
 *
 * Apple App Store requirements implemented:
 *   - Restore Purchases button (visible, not buried)
 *   - Subscription terms clearly disclosed (length, renewal, price)
 *   - Terms + Privacy links (required for review approval)
 *   - Cancel-anytime language (reduces buyer's remorse complaints)
 */
import React, { useState } from 'react';
import {
  View, Text, TouchableOpacity, ActivityIndicator, ScrollView,
  Modal, Linking, Alert, StyleSheet, Platform,
} from 'react-native';
import { useSubscription } from '../contexts/SubscriptionContext';

import { THEME, TIER_COLOR, OUTCOME_COLOR } from '../theme';
const BRAND_GREEN = THEME.accent;
const BRAND_AMBER = THEME.hrb;
const BG_DARK = THEME.bg;
const CARD_BG = THEME.surface;
const TEXT_PRIMARY = THEME.text;
const TEXT_MUTED = THEME.textDim;
const BORDER = THEME.border;

// Match what's configured in RevenueCat dashboard + App Store Connect.
// 2026-09-06 launch decision: $14.99/mo, $119.99/yr (33% off vs monthly),
// 7-day free trial on both. Trial length owned by ASC introductoryPrice
// config; if you change it in ASC, update the hasTrial copy below to match.
const PACKAGE_MONTHLY = '$rc_monthly';   // RevenueCat standard identifier
const PACKAGE_ANNUAL = '$rc_annual';
const TRIAL_DAYS = 7;

// Legal links — must be live URLs by App Store submission.
// 2026-09-06 CRITICAL FIX: thesweatlocker.app doesn't resolve — App Store
// reviewer would hit DNS failure and reject on 5.1.1. Real ToS/Privacy
// content lives at thesweatlocker.com as anchor sections on the homepage.
// Anchors are UUID-suffixed by the site builder; keep them in sync
// whenever the site is regenerated. Post-launch: consider a subdomain
// (docs.thesweatlocker.com) with stable /terms + /privacy paths.
const TERMS_URL = 'https://thesweatlocker.com/#4a867da5-8815-43cc-9c70-bf875edd04dc';
const PRIVACY_URL = 'https://thesweatlocker.com/#882ed6c9-c6cc-4b65-8e38-24249b2ae893';

type Props = {
  visible: boolean;
  onDismiss: () => void;
  // Optional: feature name that triggered the paywall (for analytics + copy)
  triggerFeature?: string;
};

export const Paywall: React.FC<Props> = ({ visible, onDismiss, triggerFeature }) => {
  const { currentOffering, purchase, restore, isLoading } = useSubscription();
  const [selected, setSelected] = useState<'monthly' | 'annual'>('annual');
  const [busy, setBusy] = useState(false);

  const monthlyPkg = currentOffering?.availablePackages.find(p => p.packageType === 'MONTHLY');
  const annualPkg = currentOffering?.availablePackages.find(p => p.packageType === 'ANNUAL');

  const monthlyPrice = monthlyPkg?.product?.priceString || '$14.99';
  const annualPrice = annualPkg?.product?.priceString || '$119.99';
  const annualPerMonth = annualPkg?.product?.price ? `$${(annualPkg.product.price / 12).toFixed(2)}/mo` : '$10.00/mo';

  // 7-day free trial is configured as an introductoryPrice in App Store Connect
  const hasTrial = monthlyPkg?.product?.introPrice || annualPkg?.product?.introPrice;

  const handlePurchase = async () => {
    // 2026-09-06 DIAGNOSTIC — verbose logging on tap to catch silent
    // no-op bugs where the CTA appears enabled but the tap either
    // doesn't fire, hits the !pkg guard, or the purchase call rejects
    // and the Alert doesn't surface (modal layering issue on iOS).
    console.log('[Paywall] handlePurchase tap fired', {
      busy,
      isLoading,
      selected,
      hasCurrentOffering: !!currentOffering,
      packageCount: currentOffering?.availablePackages?.length ?? 0,
      packageIdentifiers: (currentOffering?.availablePackages || []).map((p: any) => ({
        id: p.identifier, type: p.packageType, productId: p.product?.identifier,
      })),
      monthlyPkgFound: !!monthlyPkg,
      annualPkgFound: !!annualPkg,
    });
    if (busy) { console.log('[Paywall] bail: busy'); return; }
    const pkg = selected === 'monthly' ? monthlyPkg : annualPkg;
    if (!pkg) {
      console.log('[Paywall] bail: no pkg for selected=', selected);
      Alert.alert('Unavailable', `${selected === 'monthly' ? 'Monthly' : 'Annual'} plan isn't loaded from RevenueCat. Check RC offering packages.`);
      return;
    }
    console.log('[Paywall] calling purchase() with pkg.identifier=', pkg.identifier);
    setBusy(true);
    const result = await purchase(pkg.identifier);
    console.log('[Paywall] purchase() returned', result);
    setBusy(false);
    if (result.success) {
      onDismiss();
    } else if (result.error && result.error !== 'cancelled') {
      Alert.alert('Purchase failed', result.error);
    }
  };

  const handleRestore = async () => {
    if (busy) return;
    setBusy(true);
    const result = await restore();
    setBusy(false);
    if (result.success) {
      Alert.alert('Restored', 'Welcome back to Sweat Locker Pro.');
      onDismiss();
    } else {
      Alert.alert('Nothing to restore', 'No active subscription found on this Apple ID.');
    }
  };

  return (
    <Modal visible={visible} animationType="slide" presentationStyle="pageSheet" onRequestClose={onDismiss}>
      <View style={styles.container}>
        <ScrollView contentContainerStyle={styles.scroll} showsVerticalScrollIndicator={false}>
          {/* Close button */}
          <View style={styles.closeRow}>
            <TouchableOpacity onPress={onDismiss} style={styles.closeBtn}>
              <Text style={styles.closeText}>✕</Text>
            </TouchableOpacity>
          </View>

          {/* Hero */}
          <Text style={styles.brand}>THE SWEAT LOCKER</Text>
          <Text style={styles.headline}>Unlock Sweat Locker Pro</Text>
          <Text style={styles.subhead}>
            More data, less sweat. Model consensus, sharp money flow, and Jerry's per-game read explaining every call — with every pick tracked in the open.
          </Text>

          {/* Feature list */}
          <View style={styles.featuresBox}>
            <Feature icon="🏆" title="Play of the Day" desc="Audit-driven daily best bet" />
            <Feature icon="🐕" title="Dawg of the Day" desc="Model-identified plus-money dog" />
            <Feature icon="🎯" title="Full Sweat Card" desc="Locks, props, total edges, skip alerts" />
            <Feature icon="🧠" title="Jerry game reads" desc="Per-game narrative w/ mastery context" />
            <Feature icon="📊" title="Audit history" desc="Live W-L tracking by tier" />
            <Feature icon="🌐" title="All sports covered" desc="MLB, NFL, NCAAF, NCAAB, NBA, NHL, UFC" />
          </View>

          {/* Plan selectors */}
          <PlanCard
            selected={selected === 'annual'}
            onPress={() => setSelected('annual')}
            badge="BEST VALUE • SAVE 33%"
            title="Annual"
            price={annualPrice}
            sub={`${annualPerMonth} • billed yearly`}
          />
          <PlanCard
            selected={selected === 'monthly'}
            onPress={() => setSelected('monthly')}
            title="Monthly"
            price={`${monthlyPrice}/mo`}
            sub={hasTrial ? `${TRIAL_DAYS}-day free trial` : 'Cancel anytime'}
          />

          {/* CTA */}
          <TouchableOpacity
            style={[styles.cta, busy && styles.ctaDisabled]}
            onPress={handlePurchase}
            disabled={busy || isLoading || !currentOffering}
            activeOpacity={0.85}
          >
            {busy ? (
              <ActivityIndicator color="#000" />
            ) : (
              <Text style={styles.ctaText}>
                {hasTrial ? `Start ${TRIAL_DAYS}-Day Free Trial` : 'Subscribe'}
              </Text>
            )}
          </TouchableOpacity>

          {/* Cancellation copy + legal */}
          <Text style={styles.cancelCopy}>
            {hasTrial
              ? `Free for ${TRIAL_DAYS} days, then ${selected === 'annual' ? annualPrice + '/year' : monthlyPrice + '/month'}. Cancel anytime in Settings.`
              : 'Cancel anytime in Settings.'}
          </Text>

          {/* Restore + legal links */}
          <View style={styles.linksRow}>
            <TouchableOpacity onPress={handleRestore} disabled={busy}>
              <Text style={styles.linkText}>Restore Purchases</Text>
            </TouchableOpacity>
            <Text style={styles.dot}>·</Text>
            <TouchableOpacity onPress={() => Linking.openURL(TERMS_URL)}>
              <Text style={styles.linkText}>Terms</Text>
            </TouchableOpacity>
            <Text style={styles.dot}>·</Text>
            <TouchableOpacity onPress={() => Linking.openURL(PRIVACY_URL)}>
              <Text style={styles.linkText}>Privacy</Text>
            </TouchableOpacity>
          </View>

          <Text style={styles.disclaimer}>
            Picks are for informational purposes only. Sweat Locker is not affiliated
            with any sportsbook. If you or someone you know has a gambling problem,
            call 1-800-GAMBLER.
          </Text>
        </ScrollView>
      </View>
    </Modal>
  );
};

const Feature: React.FC<{ icon: string; title: string; desc: string }> = ({ icon, title, desc }) => (
  <View style={styles.featureRow}>
    <Text style={styles.featureIcon}>{icon}</Text>
    <View style={{ flex: 1 }}>
      <Text style={styles.featureTitle}>{title}</Text>
      <Text style={styles.featureDesc}>{desc}</Text>
    </View>
  </View>
);

const PlanCard: React.FC<{
  selected: boolean;
  onPress: () => void;
  badge?: string;
  title: string;
  price: string;
  sub: string;
}> = ({ selected, onPress, badge, title, price, sub }) => (
  <TouchableOpacity
    onPress={onPress}
    activeOpacity={0.85}
    style={[styles.planCard, selected && styles.planCardSelected]}
  >
    {badge && (
      <View style={styles.badge}>
        <Text style={styles.badgeText}>{badge}</Text>
      </View>
    )}
    <View style={styles.planRow}>
      <View style={{ flex: 1 }}>
        <Text style={styles.planTitle}>{title}</Text>
        <Text style={styles.planSub}>{sub}</Text>
      </View>
      <Text style={styles.planPrice}>{price}</Text>
    </View>
  </TouchableOpacity>
);

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: BG_DARK },
  scroll: { padding: 24, paddingBottom: 48 },
  closeRow: { alignItems: 'flex-end', marginBottom: 8 },
  closeBtn: { width: 32, height: 32, alignItems: 'center', justifyContent: 'center' },
  closeText: { color: TEXT_MUTED, fontSize: 20 },
  brand: { color: TEXT_MUTED, fontSize: 11, fontWeight: '700', letterSpacing: 2, marginBottom: 8 },
  headline: { color: TEXT_PRIMARY, fontSize: 28, fontWeight: '800', marginBottom: 10 },
  subhead: { color: TEXT_MUTED, fontSize: 14, lineHeight: 20, marginBottom: 24 },
  featuresBox: { backgroundColor: CARD_BG, borderRadius: 14, padding: 16, marginBottom: 24, borderWidth: 1, borderColor: BORDER },
  featureRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: 8 },
  featureIcon: { fontSize: 20, marginRight: 12, width: 28 },
  featureTitle: { color: TEXT_PRIMARY, fontWeight: '700', fontSize: 14, marginBottom: 2 },
  featureDesc: { color: TEXT_MUTED, fontSize: 12 },
  planCard: { backgroundColor: CARD_BG, borderRadius: 12, padding: 16, marginBottom: 10, borderWidth: 2, borderColor: BORDER },
  planCardSelected: { borderColor: BRAND_GREEN, backgroundColor: THEME.accent + '0F' },
  badge: { backgroundColor: BRAND_AMBER, alignSelf: 'flex-start', paddingHorizontal: 8, paddingVertical: 3, borderRadius: 4, marginBottom: 8 },
  badgeText: { color: '#000', fontSize: 10, fontWeight: '800', letterSpacing: 0.5 },
  planRow: { flexDirection: 'row', alignItems: 'center' },
  planTitle: { color: TEXT_PRIMARY, fontWeight: '700', fontSize: 16 },
  planSub: { color: TEXT_MUTED, fontSize: 12, marginTop: 2 },
  planPrice: { color: TEXT_PRIMARY, fontWeight: '800', fontSize: 18 },
  cta: { backgroundColor: BRAND_GREEN, borderRadius: 12, paddingVertical: 16, alignItems: 'center', marginTop: 16, marginBottom: 12 },
  ctaDisabled: { opacity: 0.5 },
  ctaText: { color: '#000', fontWeight: '800', fontSize: 16 },
  cancelCopy: { color: TEXT_MUTED, fontSize: 11, textAlign: 'center', marginBottom: 16, lineHeight: 16 },
  linksRow: { flexDirection: 'row', justifyContent: 'center', alignItems: 'center', marginBottom: 16, flexWrap: 'wrap' },
  linkText: { color: TEXT_MUTED, fontSize: 12, textDecorationLine: 'underline' },
  dot: { color: TEXT_MUTED, marginHorizontal: 8 },
  disclaimer: { color: THEME.textMuted, fontSize: 10, lineHeight: 14, textAlign: 'center', marginTop: 8 },
});
