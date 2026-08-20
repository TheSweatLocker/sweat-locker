/**
 * <Explainer term="..."/>  — inline tap-to-explain for any glossary term.
 *
 * Drop it anywhere we surface jargon (stat abbreviations, model names,
 * tier labels, signal names, prop concepts). Users tap the term where
 * they see it, get a one-sentence explanation right below, tap again to
 * close. No modal, no glossary page, no leaving the screen.
 *
 * Two visual modes:
 *   <Explainer term="SLpM"/>                    — renders term text as
 *                                                  underlined + ⓘ tappable
 *   <Explainer term="SLpM">custom text</...>    — wraps arbitrary child
 *                                                  content with the same
 *                                                  tap-to-open affordance
 *
 * Positioning: caller controls layout. The explanation row is rendered
 * INSIDE the same View as the term via an anchor pattern — parents pass
 * `expanded` state up if they need to close all others when one opens.
 * For standalone one-off usage, pass no `expanded` prop and the component
 * self-manages.
 *
 * When the term isn't in GLOSSARY, the component falls back to plain text
 * with no tap affordance — safe to sprinkle anywhere.
 */
import React from 'react';
import {View, Text, TouchableOpacity, TextStyle, ViewStyle} from 'react-native';
import {explain} from '../lib/glossary';

type Props = {
  term: string;
  children?: React.ReactNode;
  // Optional style overrides for the term text
  textStyle?: TextStyle;
  // Optional style for the popover container
  popStyle?: ViewStyle;
  // Colors for the theme (default matches app's dark palette)
  color?: string;              // term text color when closed
  activeColor?: string;        // term text color when open
  helpColor?: string;          // explanation text color
  helpBg?: string;             // explanation background color
  // External state control (optional). If provided, parent owns the toggle.
  isOpen?: boolean;
  onToggle?: () => void;
};

const DEFAULT_COLOR = '#7a92a8';
const DEFAULT_ACTIVE = '#ff6b3d';
const DEFAULT_HELP = '#9db1c3';
const DEFAULT_HELP_BG = '#ff6b3d12';

export default function Explainer({
  term, children, textStyle, popStyle,
  color = DEFAULT_COLOR, activeColor = DEFAULT_ACTIVE,
  helpColor = DEFAULT_HELP, helpBg = DEFAULT_HELP_BG,
  isOpen, onToggle,
}: Props) {
  const [selfOpen, setSelfOpen] = React.useState(false);
  const help = explain(term);
  const open = isOpen !== undefined ? isOpen : selfOpen;
  const toggle = () => {
    if (onToggle) onToggle();
    else setSelfOpen(o => !o);
  };

  // Term isn't in the glossary — render as plain text, no tap affordance.
  // Prevents dead taps and keeps sprinkling safe.
  if (!help) {
    if (children) return <>{children}</>;
    return <Text style={[{color}, textStyle]}>{term}</Text>;
  }

  return (
    <View>
      <TouchableOpacity onPress={toggle} hitSlop={{top:6,bottom:6,left:6,right:6}}
                        style={{flexDirection:'row', alignItems:'center', gap:3}}>
        {/* 2026-08-20: dropped dotted-underline styling — read as "weird
            underscores" under xERA / wRC+ etc labels. The ⓘ icon alone
            signals tap-to-explain, cleaner visual. */}
        {children ? children : (
          <Text style={[{
            color: open ? activeColor : color,
          }, textStyle]}>{term}</Text>
        )}
        <Text style={{color: open ? activeColor : color + 'AA', fontSize:10, fontWeight:'700'}}>ⓘ</Text>
      </TouchableOpacity>
      {open && (
        <View style={[{
          marginTop:4, paddingVertical:6, paddingHorizontal:10,
          backgroundColor: helpBg, borderRadius:6,
          borderLeftWidth:2, borderLeftColor: activeColor,
        }, popStyle]}>
          <Text style={{color: helpColor, fontSize:11, lineHeight:15}}>{help}</Text>
        </View>
      )}
    </View>
  );
}
