/**
 * FAQ — frequently asked questions (2026-08-09).
 *
 * Launch-blocker per project_launch_queue_806. Answers the questions
 * new users hit before they read anywhere else. Grouped:
 *   - The Basics (what am I looking at)
 *   - How Picks Work (tiers, timing, grading)
 *   - Reading the Card (money%/bets%, badges, cohorts)
 *   - Sports & Coverage
 *   - Subscription & Trial
 *   - Responsible Betting
 *
 * All content is static and edited here. No CMS.
 */
import React, { useState } from 'react';
import {
  View, Text, ScrollView, TouchableOpacity, StatusBar, StyleSheet,
} from 'react-native';
import { Stack, useRouter } from 'expo-router';

import { THEME } from './theme';

const BG        = THEME.bg;
const CARD_BG   = THEME.surface;
const CARD_HL   = THEME.surfaceAlt;
const TEXT_P    = THEME.text;
const TEXT_M    = THEME.textDim;
const BORDER    = THEME.border;
const ACCENT    = THEME.accent;

type Item = { q: string; a: string };
type Section = { title: string; items: Item[] };

const SECTIONS: Section[] = [
  {
    title: 'The Basics',
    items: [
      {
        q: 'What is The Sweat Locker?',
        a: 'A daily sports analytics app. Every night we run 5+ models across the slate, cross-check with sharp money and cohort history, and publish a curated card of the best plays for the day. You get the picks, the reasoning, and the receipts.',
      },
      {
        q: 'Who is Jerry?',
        a: 'Jerry is our voice — the sharp handicapper narrator who translates the model output into plain English. Every game read you see is generated live from that day\'s numbers, then validated to strip hallucinations before it hits your screen.',
      },
      {
        q: 'Is this a tout service?',
        a: 'No. We show you what our data says, why, and the historical hit rate of similar spots. If a signal is weak we say so. We do not sell "locks" or hide records.',
      },
    ],
  },
  {
    title: 'How Picks Work',
    items: [
      {
        q: 'What do the tiers mean?',
        a: 'PRIME (80-100) = multiple signals aligned + calibration audit ≥60% + no trap flag. STRONG (65-79) = real edge, 2-3 signals aligned. LEAN (50-64) = soft directional edge worth noting. READ (30-49) = analytical take, thin edge. PASS = broken/blank data.',
      },
      {
        q: 'When do picks come out?',
        a: 'Morning: initial card drops ~11am ET after model runs. Final: refreshed by ~4pm ET after lineups + line moves are baked in. If a pitcher scratches or weather changes, the pick can shift between those windows.',
      },
      {
        q: 'How do you grade?',
        a: 'Auto-graded overnight against official box scores. Every pick gets W/L/P and lands on the Track Record page. Grader runs single-pass per date so results are consistent — no cherry-picking.',
      },
      {
        q: 'Do you ever change a pick after publishing?',
        a: 'No. Once a pick is on the card and the game hasn\'t started, that\'s the pick. If we recompute mid-day and a game flips direction, we surface it as a new "Late Move" — original stays on the record.',
      },
    ],
  },
  {
    title: 'Reading the Card',
    items: [
      {
        q: 'What\'s money% vs bets%?',
        a: 'money% = share of DOLLARS on a side. bets% = share of TICKETS. When money% is much higher than bets% (say 87/73), it usually means larger accounts are on that side. Our 30-day audit says treat big divergence with caution — it has been a fade signal more often than a follow signal.',
      },
      {
        q: 'What does the alignment badge mean?',
        a: 'It tells you if our model, sharp money, and public consensus agree. ALIGNED = all three on the same side. MIXED = split. OPPOSED = model on one side, public on the other. Aligned games have historically hit best.',
      },
      {
        q: 'What are cohorts?',
        a: 'Cohorts are historical "similar spots" — e.g. "home team with elite starter + hitter park + tired bullpen." When multiple cohorts stack on the same side, that\'s our confluence signal.',
      },
      {
        q: 'Why do some picks show a "correlation warning"?',
        a: 'When two picks in the same game push the same direction (e.g. UNDER 8 + starter ER OVER 2.5) they can split — one wins, one loses. The warning tells you the picks aren\'t independent — bet each on merit, don\'t stack blindly.',
      },
    ],
  },
  {
    title: 'Sports & Coverage',
    items: [
      {
        q: 'Which sports do you cover?',
        a: 'MLB (year-round in season), UFC (fight nights), NBA (starting Oct 2026), NFL + NCAAF (starting Sept 2026), NHL + NCAAB (winter). Every sport gets its own tuned pipeline — same architecture, different models.',
      },
      {
        q: 'How many picks do I get per day?',
        a: 'Depends on slate quality. Cards typically publish 4-8 picks per day. On thin slates we may publish fewer — we don\'t force volume just to fill space. If we don\'t have signals worth trusting, we say so.',
      },
      {
        q: 'What about props?',
        a: 'MLB props (pitcher K/BB/ER/hits + batter hits) are covered daily. NBA + NFL props ship with those sports\' seasons. Every prop gets tier + book line + edge% + calibration data.',
      },
    ],
  },
  {
    title: 'Subscription & Trial',
    items: [
      {
        q: 'How much does it cost?',
        a: '$14.99/month or $119.99/year. 7-day free trial to start. Cancel anytime through the App Store.',
      },
      {
        q: 'What do I get on the free trial?',
        a: 'Everything — full card, Jerry reads, all sports, track record. If we don\'t earn your subscription in 7 days, we don\'t deserve it.',
      },
      {
        q: 'Do you offer refunds?',
        a: 'App Store handles refunds per Apple policy. If you have a specific issue, message support and we\'ll do right by you.',
      },
    ],
  },
  {
    title: 'Responsible Betting',
    items: [
      {
        q: 'Should I bet every pick on the card?',
        a: 'No. The card is a menu, not a mandate. Pick the plays that fit your bankroll and comfort. If in doubt, take fewer bets at proper size rather than more at reduced size.',
      },
      {
        q: 'What if I\'m struggling with gambling?',
        a: 'Please reach out for help: 1-800-GAMBLER (National Council on Problem Gambling), 1-888-ADMIT-IT (Florida), or text "HELPLINE" to 800-889-9789. There is no picking your way out of a problem — take a break, get support.',
      },
      {
        q: 'Are these picks legal advice?',
        a: 'No. This is analytics content, not financial or legal advice. Sports betting is only legal where regulated. You must be of legal betting age in your jurisdiction (varies 18-21 by state). Bet with money you can afford to lose.',
      },
    ],
  },
];

function FAQItem({ item }: { item: Item }) {
  const [open, setOpen] = useState(false);
  return (
    <TouchableOpacity
      activeOpacity={0.7}
      onPress={() => setOpen(o => !o)}
      style={[styles.qaCard, open && styles.qaCardOpen]}
    >
      <View style={styles.qaRow}>
        <Text style={styles.qText}>{item.q}</Text>
        <Text style={styles.qChevron}>{open ? '−' : '+'}</Text>
      </View>
      {open && <Text style={styles.aText}>{item.a}</Text>}
    </TouchableOpacity>
  );
}

export default function FAQ() {
  const router = useRouter();
  return (
    <View style={styles.root}>
      <StatusBar barStyle="light-content" backgroundColor={BG} />
      <Stack.Screen options={{ title: 'FAQ', headerShown: false }} />
      <ScrollView contentContainerStyle={styles.scroll}>
        <View style={styles.headerRow}>
          <TouchableOpacity onPress={() => router.back()} style={styles.backBtn}>
            <Text style={styles.backText}>←</Text>
          </TouchableOpacity>
          <Text style={styles.headerTitle}>FAQ</Text>
          <View style={{ width: 40 }} />
        </View>
        <Text style={styles.subtitle}>Everything you might want to know before you use the card.</Text>
        {SECTIONS.map((section) => (
          <View key={section.title} style={styles.section}>
            <Text style={styles.sectionTitle}>{section.title}</Text>
            {section.items.map((item, idx) => (
              <FAQItem key={idx} item={item} />
            ))}
          </View>
        ))}
        <Text style={styles.footer}>
          Something still unclear? Send us a note through Settings → Contact Support.
        </Text>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: BG },
  scroll: { paddingBottom: 60 },
  headerRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    padding: 16, paddingTop: 44,
  },
  backBtn: { width: 40, height: 40, justifyContent: 'center' },
  backText: { color: ACCENT, fontSize: 28, fontWeight: '600' },
  headerTitle: { color: TEXT_P, fontSize: 24, fontWeight: '700' },
  subtitle: {
    color: TEXT_M, fontSize: 14, lineHeight: 20,
    paddingHorizontal: 16, marginBottom: 20,
  },
  section: { paddingHorizontal: 16, marginBottom: 24 },
  sectionTitle: {
    color: ACCENT, fontSize: 12, fontWeight: '700',
    letterSpacing: 1.5, textTransform: 'uppercase',
    marginBottom: 10,
  },
  qaCard: {
    backgroundColor: CARD_BG, borderRadius: 12,
    padding: 14, marginBottom: 8,
    borderWidth: 1, borderColor: BORDER,
  },
  qaCardOpen: { backgroundColor: CARD_HL, borderColor: ACCENT },
  qaRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
  },
  qText: {
    flex: 1, color: TEXT_P, fontSize: 15, fontWeight: '600',
    lineHeight: 22, paddingRight: 12,
  },
  qChevron: { color: ACCENT, fontSize: 24, fontWeight: '400' },
  aText: {
    color: TEXT_M, fontSize: 14, lineHeight: 21,
    marginTop: 12, paddingTop: 12,
    borderTopWidth: 1, borderTopColor: BORDER,
  },
  footer: {
    color: TEXT_M, fontSize: 13, fontStyle: 'italic',
    textAlign: 'center', paddingHorizontal: 40, marginTop: 20,
  },
});
