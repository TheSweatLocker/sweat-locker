---
name: feedback_undefined_palette_key_renders_black
description: "An undefined palette key does NOT fall back in React Native — {color: undefined} clears the inherited colour and paints default black; concatenated it yields the string 'undefined30'. Audit palettes for used-but-undeclared keys."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 785f60eb-2896-47d0-b93c-5a98f036e862
  modified: 2026-09-25T05:27:35.286Z
---

# An undefined colour key ships black text, it does not fall back

Andy 2026-09-25, QA screenshot: "colors of stats are dark not green or red
there is an issue." GameDetailV2's local `C` palette was missing `win`,
`loss`, `surfaceAlt` and `borderSoft` — 28 references to keys never declared
on the object.

Two distinct failure shapes, both silent:

1. `{color: C.win}` with `C.win === undefined` does **not** inherit. In a RN
   style array the later entry still wins, so the explicit undefined clears
   the colour and the platform default (black) paints. On a dark background
   that is invisible. It hit only the *advantaged* rows, because neutral rows
   never enter that branch — so the card showed readable labels and ranks
   above blacked-out values, which reads as a data bug rather than a CSS one.
2. `C.win + '30'` evaluates to the **string** `"undefined30"` — an invalid
   colour RN drops entirely. Tinted chips simply lost their fill. Green and
   red tiers vanished while amber and blue (real keys) rendered, on a card
   whose own legend says "green = better".

**Why:** `borderStrong` and `surface3` were defined and unused — a rename
whose call sites were never updated. Nothing failed loudly at any point.

**How to apply:** when a user reports "dark/black/invisible text" or a colour
that never appears, FIRST diff the palette's declared keys against `X.<key>`
usages before touching any rendering logic. One-liner that found it:

    keys  = set(re.findall(r'^\s*(\w+)\s*:', palette_body, re.M))
    used  = set(re.findall(r'\bC\.(\w+)', src))
    missing = used - keys

Sweep every component's local palette, not just the reported one — though in
this case the shared `app/theme.ts` THEME and all sibling components were
clean.

**The compiler already knew.** tsc went 1914 -> 1888 on the fix: those 26
errors had been reported all along, buried in a 1,914-error baseline, and
Expo builds through Babel without typechecking, so it shipped to paying
users with the compiler pointing straight at it. A large known-noise baseline
is not harmless — it hides real user-visible defects. Related:
[[feedback_explicit_select_silent_blanks]] (same family: the missing thing
does not error, it just renders empty).
