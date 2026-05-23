/**
 * SubscriptionContext — single source of truth for the user's Pro
 * entitlement status across the app. Wraps RevenueCat SDK.
 *
 * Reads RevenueCat customer info on app launch, subscribes to updates
 * (so a successful purchase or restore flow updates state app-wide),
 * exposes:
 *   - isPro: boolean — true if user has active 'pro' entitlement
 *   - isLoading: boolean — initial fetch in-flight
 *   - offerings: Offerings | null — the current paywall products
 *   - refresh(): re-fetch customer info on demand
 *   - presentPaywall(): convenience method (used by gated screens)
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
import Purchases, { CustomerInfo, PurchasesOffering, PurchasesOfferings, LOG_LEVEL } from 'react-native-purchases';

const ENTITLEMENT_ID = 'pro';

type SubscriptionContextValue = {
  isPro: boolean;
  isLoading: boolean;
  offerings: PurchasesOfferings | null;
  currentOffering: PurchasesOffering | null;
  customerInfo: CustomerInfo | null;
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
  const [customerInfo, setCustomerInfo] = useState<CustomerInfo | null>(null);
  const [offerings, setOfferings] = useState<PurchasesOfferings | null>(null);

  // SDK initialization. iOS-only for v1.0 (Android can be wired later
  // by adding EXPO_PUBLIC_REVENUECAT_KEY_ANDROID and a Platform branch).
  useEffect(() => {
    const init = async () => {
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
      try {
        if (__DEV__) Purchases.setLogLevel(LOG_LEVEL.DEBUG);
        Purchases.configure({ apiKey: iosKey });

        // Listen for customer info changes (purchases, restores, expirations)
        Purchases.addCustomerInfoUpdateListener((info) => {
          setCustomerInfo(info);
          setIsPro(!!info.entitlements.active[ENTITLEMENT_ID]);
        });

        // Initial fetch
        const info = await Purchases.getCustomerInfo();
        setCustomerInfo(info);
        setIsPro(!!info.entitlements.active[ENTITLEMENT_ID]);

        // Fetch offerings for paywall display
        const off = await Purchases.getOfferings();
        setOfferings(off);
      } catch (e: any) {
        console.warn('[Subscription] init failed:', e?.message);
      } finally {
        setIsLoading(false);
      }
    };
    init();
  }, []);

  const refresh = useCallback(async () => {
    if (Platform.OS !== 'ios') return;
    try {
      const info = await Purchases.getCustomerInfo();
      setCustomerInfo(info);
      setIsPro(!!info.entitlements.active[ENTITLEMENT_ID]);
    } catch (e: any) {
      console.warn('[Subscription] refresh failed:', e?.message);
    }
  }, []);

  const purchase = useCallback(async (packageIdentifier: string) => {
    try {
      if (!offerings?.current) {
        return { success: false, error: 'No offerings available' };
      }
      const pkg = offerings.current.availablePackages.find(p => p.identifier === packageIdentifier);
      if (!pkg) {
        return { success: false, error: `Package ${packageIdentifier} not found` };
      }
      const { customerInfo: info } = await Purchases.purchasePackage(pkg);
      setCustomerInfo(info);
      const newIsPro = !!info.entitlements.active[ENTITLEMENT_ID];
      setIsPro(newIsPro);
      return { success: newIsPro };
    } catch (e: any) {
      // RevenueCat throws userCancelled = user-initiated cancel (not an error)
      if (e?.userCancelled) return { success: false, error: 'cancelled' };
      return { success: false, error: e?.message || 'Purchase failed' };
    }
  }, [offerings]);

  const restore = useCallback(async () => {
    try {
      const info = await Purchases.restorePurchases();
      setCustomerInfo(info);
      const newIsPro = !!info.entitlements.active[ENTITLEMENT_ID];
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
