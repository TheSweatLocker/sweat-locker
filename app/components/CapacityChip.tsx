/**
 * CapacityChip — degraded-service banner for the 1500-user launch spike.
 *
 * 2026-09-14 · v1.0.1 #13d.
 *
 * Andy's launch context: at 1500 users clicking Sunday morning, some
 * fetches WILL time out because Pro tier maxes at 200 concurrent DB
 * connections. Without a UI signal, users see a blank tab and assume
 * the app is broken. This chip says the app KNOWS it's overloaded and
 * to try again — much better UX than white-screen.
 *
 * Wire-up (three call sites):
 *
 *   1. Render `<CapacityChip />` once at the root of every screen that
 *      has any dbFetch-backed load (Games, Sharp Card, POTD, Receipts).
 *
 *   2. In every fetch error handler, call `notifyCapacity()` when
 *      the error is retryable/timeout-shaped:
 *
 *        const {data, error} = await dbFetchWith(() => query);
 *        if (error?.isTimeout) notifyCapacity();
 *
 *   3. Optional: pass `label` to identify the surface. Not surfaced in
 *      copy but written to telemetry so ops can see which surface
 *      degraded first during the surge.
 *
 * Behavior:
 *   * Auto-hides after 20 seconds so a stale banner doesn't linger
 *     into a working session.
 *   * Debounced — repeated notify() calls within 3s don't restart
 *     the timer or re-render.
 *   * Never blocks interaction — always renders above content, tap
 *     to dismiss early.
 *
 * See project_scale_1500_users_911, project_v1_0_1_client_priorities #13d.
 */
import React from 'react';
import {View, Text, TouchableOpacity, Platform} from 'react-native';

/** Global visibility state — module-scoped so any consumer can toggle. */
let _visible = false;
let _hideAt = 0;
let _lastLabel = '';
const _subscribers: Set<() => void> = new Set();

const AUTO_HIDE_MS = 20_000;
const DEBOUNCE_MS = 3_000;

function _emit() {
  _subscribers.forEach((cb) => {
    try { cb(); } catch {}
  });
}

/**
 * Signal that a fetch just failed with a retryable/timeout error.
 * Call this from every fetch error handler where the error is capacity-
 * shaped. Debounced so a burst of failures doesn't spam re-renders.
 *
 * @param label surface identifier for telemetry (e.g. 'games_MLB',
 *              'sharp_card', 'receipts')
 */
export function notifyCapacity(label?: string): void {
  const now = Date.now();
  // Debounce: if the banner is already up and we just notified, ignore.
  if (_visible && (_hideAt - now) > (AUTO_HIDE_MS - DEBOUNCE_MS)) {
    return;
  }
  _visible = true;
  _hideAt = now + AUTO_HIDE_MS;
  if (label) _lastLabel = label;
  // Log for telemetry — ops can see which surface tripped first.
  try {
    console.log(`[CapacityChip] shown${label ? ` for ${label}` : ''}`);
  } catch {}
  _emit();
}

/** Manually dismiss the banner (tap-to-close). */
export function dismissCapacity(): void {
  _visible = false;
  _emit();
}

/**
 * Chip component. Renders as a compact top-anchored banner when
 * `_visible` is true. Non-blocking; tap to dismiss.
 */
export function CapacityChip(): React.ReactElement | null {
  // Force re-render on state change.
  const [, force] = React.useState(0);
  React.useEffect(() => {
    const cb = () => force((n) => n + 1);
    _subscribers.add(cb);
    return () => { _subscribers.delete(cb); };
  }, []);
  // Auto-hide timer: separate from the emit so we don't fire cross-mounts.
  React.useEffect(() => {
    if (!_visible) return;
    const remaining = Math.max(0, _hideAt - Date.now());
    if (remaining <= 0) {
      _visible = false;
      _emit();
      return;
    }
    const t = setTimeout(() => {
      _visible = false;
      _emit();
    }, remaining);
    return () => clearTimeout(t);
  });

  if (!_visible) return null;

  return (
    <TouchableOpacity
      onPress={dismissCapacity}
      activeOpacity={0.85}
      accessibilityRole="alert"
      accessibilityLabel="At capacity — try again shortly"
      style={{
        marginHorizontal: 12,
        marginTop: Platform.OS === 'ios' ? 6 : 4,
        marginBottom: 8,
        paddingVertical: 8,
        paddingHorizontal: 12,
        backgroundColor: '#3F2A1A',
        borderColor: '#B57B2D',
        borderWidth: 1,
        borderRadius: 8,
        alignSelf: 'center',
        maxWidth: 460,
      }}
    >
      <Text
        style={{
          color: '#F4C078',
          fontSize: 12,
          fontWeight: '700',
          letterSpacing: 0.3,
          textAlign: 'center',
        }}
      >
        ⚡ We're at capacity — try again in a moment
      </Text>
      <Text
        style={{
          color: '#B57B2D',
          fontSize: 10,
          fontWeight: '600',
          marginTop: 2,
          textAlign: 'center',
        }}
      >
        tap to dismiss
      </Text>
    </TouchableOpacity>
  );
}
