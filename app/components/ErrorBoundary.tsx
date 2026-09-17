import React from 'react';
import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { THEME } from '../theme';

type Props = { children: React.ReactNode };
type State = { hasError: boolean; message?: string };

export class ErrorBoundary extends React.Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(err: Error): State {
    return { hasError: true, message: err?.message || 'Unknown error' };
  }

  componentDidCatch(err: Error, info: React.ErrorInfo) {
    console.error('[ErrorBoundary]', err, info?.componentStack);
  }

  handleReload = () => {
    this.setState({ hasError: false, message: undefined });
  };

  render() {
    if (!this.state.hasError) return this.props.children;
    return (
      <View style={styles.wrap}>
        <Text style={styles.title}>Something went wrong</Text>
        <Text style={styles.body}>
          The app hit an unexpected error. Tap below to reload.
        </Text>
        {this.state.message ? (
          <Text style={styles.debug} numberOfLines={3}>
            {this.state.message}
          </Text>
        ) : null}
        <TouchableOpacity style={styles.btn} onPress={this.handleReload}>
          <Text style={styles.btnText}>Reload</Text>
        </TouchableOpacity>
      </View>
    );
  }
}

const styles = StyleSheet.create({
  wrap: {
    flex: 1,
    backgroundColor: THEME.bg,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 32,
  },
  title: {
    color: THEME.text,
    fontSize: 22,
    fontWeight: '700',
    marginBottom: 12,
  },
  body: {
    color: THEME.textMuted,
    fontSize: 15,
    textAlign: 'center',
    marginBottom: 20,
  },
  debug: {
    color: THEME.textMuted,
    fontSize: 12,
    textAlign: 'center',
    marginBottom: 24,
    opacity: 0.6,
  },
  btn: {
    backgroundColor: THEME.accent,
    paddingVertical: 12,
    paddingHorizontal: 32,
    borderRadius: 8,
  },
  btnText: {
    color: THEME.bg,
    fontSize: 16,
    fontWeight: '700',
  },
});
