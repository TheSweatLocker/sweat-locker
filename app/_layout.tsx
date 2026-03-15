import { Stack } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import { Animated, Image, StyleSheet } from 'react-native';

const logo = require('../assets/images/splash-logo.png');

export default function RootLayout() {
  const [showSplash, setShowSplash] = useState(true);
  const fadeAnim = useRef(new Animated.Value(1)).current;

  useEffect(() => {
    const timer = setTimeout(() => {
      Animated.timing(fadeAnim, {
        toValue: 0,
        duration: 600,
        useNativeDriver: true,
      }).start(() => setShowSplash(false));
    }, 2500);
    return () => clearTimeout(timer);
  }, []);

  return (
    <>
      <Stack screenOptions={{ headerShown: false }}>
        <Stack.Screen name="index" />
      </Stack>

      {showSplash && (
        <Animated.View style={[styles.splash, { opacity: fadeAnim }]}>
          <Image
            source={logo}
            style={styles.logo}
            resizeMode="contain"
          />
        </Animated.View>
      )}
    </>
  );
}

const styles = StyleSheet.create({
  splash: {
    position: 'absolute',
    top: 0, left: 0, right: 0, bottom: 0,
    backgroundColor: '#080c10',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 999,
  },
  logo: {
    width: '100%',
    height: '100%',
  },
});