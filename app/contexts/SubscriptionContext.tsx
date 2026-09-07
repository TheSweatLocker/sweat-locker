/**
 * SubscriptionContext — single source of truth for the user's Pro
 * entitlement status across the app. Wraps RevenueCat SDK.
 *
 * Module loading: react-native-purchases is loaded via an indirected
 * require so Metro can't statically resolve it. If the module isn't
 * installed (npm install pending) or the native binding isn't built
 * into the current dev client (eas build pending), the provider degrades
 * to a free-tier stub — app still boots, paywall is dormant. Once
 * `npm install react-native-purchases` + an EAS build of the dev client
 * run, this same code activates RevenueCat with no source edits.
 *
 * Reads RevenueCat customer info on app launch, subscribes to updates
 * (so a successful purchase or restore flow updates state app-wide),
 * exposes:
 *   - isPro: boolean — true if user has active 'pro' entitlement
 *   - isLoading: boolean — initial fetch in-flight
 *   - offerings: Offerings | null — the current paywall products
 *   - refresh(): re-fetch customer info on demand
 *
 * Initialization happens in app/_layout.tsx via <SubscriptionProvider>.
 * Premium features check isPro and render either the real content or
 * the <Paywall /> component.
 *
 * Entitlement name 'pro' must match what's configured in RevenueCat
 * dashboard (Project Settings → Entitlements).
 *
 * Required env: EXPO_PUBLIC_REVENUECAT_KEY_IOS
 * Set in .env (or build profile env). App fails fast with a console
 * warning if missing — checkout flow won't work but app still renders.
 */
import React, { createContext, useContext, useEffect, useState, useCallback } from 'react';
import { Platform } from 'react-native';

let Purchases: any = null;
let LOG_LEVEL: any = { DEBUG: 'debug' };
try {
  const modName = 'react-native-purchases';
  const mod = require(modName);
  Purchases = mod.default || mod.Purchases || mod;
  LOG_LEVEL = mod.LOG_LEVEL || LOG_LEVEL;
} catch {
  if (__DEV__) {
    console.warn('[Subscription] react-native-purchases not available — running in free-tier stub mode. Run `npm install react-native-purchases` + rebuild dev client to activate paywall.');
  }
}

// 2026-09-06 entitlement ID matches whatever's in the RC dashboard —
// the string RC returns in customerInfo.entitlements.active[<id>] is
// exact-match, case-sensitive, spaces included. This one was created
// with the display name as the identifier ("The Sweat Locker Pro")
// and RC locks identifiers after creation, so easier to align the code
// than delete + recreate the entitlement. If you ever DO recreate,
// prefer a lowercase-underscore convention like 'pro' or 'sweat_pro'.
const ENTITLEMENT_ID = 'The Sweat Locker Pro';

type SubscriptionContextValue = {
  isPro: boolean;
  isLoading: boolean;
  offerings: any | null;
  currentOffering: any | null;
  customerInfo: any | null;
  refresh: () => Promise<void>;
  purchase: (packageIdentifier: string) => Promise<{ success: boolean; error?: string }>;
  restore: () => Promise<{ success: boolean; error?: string }>;
};

const SubscriptionContext = createContext<SubscriptionContextValue>({
  isPro: false,
  isLoading: true,
  offerings: null,
  currentOffering: null,
  customerInfo: null,
  refresh: async () => {},
  purchase: async () => ({ success: false, error: 'not_initialized' }),
  restore: async () => ({ success: false, error: 'not_initialized' }),
});

export const useSubscription = () => useContext(SubscriptionContext);

export const SubscriptionProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [isPro, setIsPro] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [customerInfo, setCustomerInfo] = useState<any | null>(null);
  const [offerings, setOfferings] = useState<any | null>(null);

  useEffect(() => {
    const init = async () => {
      if (!Purchases) {
        // Module missing or native binding unavailable — free-tier stub
        setIsLoading(false);
        return;
      }
      const iosKey = process.env.EXPO_PUBLIC_REVENUECAT_KEY_IOS;
      if (!iosKey) {
        console.warn('[Subscription] EXPO_PUBLIC_REVENUECAT_KEY_IOS not set — paywall will not work');
        setIsLoading(false);
        return;
      }
      if (Platform.OS !== 'ios') {
        // v1.0 ships iOS-only. Android stub: leave free-tier mode.
        setIsLoading(false);
        return;
      }
      // 2026-09-06 async-safe wrapper. RC v9's setLogLevel/configure return
      // Promises. A raw try/catch around a non-awaited Promise doesn't catch
      // the rejection — it fires as an unhandled promise rejection later.
      // In Expo Go where the native binding is missing, every RC call
      // rejects. Wrap each call in await + .catch() so failures degrade
      // silently to free-tier stub mode instead of spamming red screens.
      try {
        if (__DEV__) await Purchases.setLogLevel(LOG_LEVEL.DEBUG).catch(() => {});
        await Purchases.configure({ apiKey: iosKey }).catch(() => {
          throw new Error('native binding unavailable — likely running in Expo Go');
        });

        Purchases.addCustomerInfoUpdateListener((info: any) => {
          setCustomerInfo(info);
          setIsPro(!!info?.entitlements?.active?.[ENTITLEMENT_ID]);
        });

        const info = await Purchases.getCustomerInfo();
        setCustomerInfo(info);
        setIsPro(!!info?.entitlements?.active?.[ENTITLEMENT_ID]);

        const off = await Purchases.getOfferings();
        setOfferings(off);
      } catch (e: any) {
        // Silent in Expo Go (expected). Loud in dev-client (real bug).
        if (__DEV__ && !String(e?.message || '').includes('Expo Go')) {
          console.warn('[Subscription] init failed:', e?.message);
        }
      } finally {
        setIsLoading(false);
      }
    };
    init();
  }, []);

  const refresh = useCallback(async () => {
    if (!Purchases || Platform.OS !== 'ios') return;
    try {
      const info = await Purchases.getCustomerInfo();
      setCustomerInfo(info);
      setIsPro(!!info?.entitlements?.active?.[ENTITLEMENT_ID]);
    } catch (e: any) {
      console.warn('[Subscription] refresh failed:', e?.message);
    }
  }, []);

  const purchase = useCallback(async (packageIdentifier: string) => {
    if (!Purchases) return { success: false, error: 'purchases_unavailable' };
    try {
      if (!offerings?.current) {
        return { success: false, error: 'No offerings available' };
      }
      const pkg = offerings.current.availablePackages.find((p: any) => p.identifier === packageIdentifier);
      if (!pkg) {
        return { success: false, error: `Package ${packageIdentifier} not found` };
      }
      const { customerInfo: info } = await Purchases.purchasePackage(pkg);
      setCustomerInfo(info);
      const newIsPro = !!info?.entitlements?.active?.[ENTITLEMENT_ID];
      setIsPro(newIsPro);
      return { success: newIsPro };
    } catch (e: any) {
      if (e?.userCancelled) return { success: false, error: 'cancelled' };
      return { success: false, error: e?.message || 'Purchase failed' };
    }
  }, [offerings]);

  const restore = useCallback(async () => {
    if (!Purchases) return { success: false, error: 'purchases_unavailable' };
    try {
      const info = await Purchases.restorePurchases();
      setCustomerInfo(info);
      const newIsPro = !!info?.entitlements?.active?.[ENTITLEMENT_ID];
      setIsPro(newIsPro);
      return { success: newIsPro };
    } catch (e: any) {
      return { success: false, error: e?.message || 'Restore failed' };
    }
  }, []);

  return (
    <SubscriptionContext.Provider
      value={{
        isPro,
        isLoading,
        offerings,
        currentOffering: offerings?.current ?? null,
        customerInfo,
        refresh,
        purchase,
        restore,
      }}
    >
      {children}
    </SubscriptionContext.Provider>
  );
};
