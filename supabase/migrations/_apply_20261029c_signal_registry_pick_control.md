# Apply 20261029c — store the pick control on signal_registry

Three `ADD COLUMN IF NOT EXISTS` on `public.signal_registry`. Idempotent,
no data written, no NOTIFY, pure ASCII. Safe to run twice.

## Why

`refit_signal_registry.py` now computes, for every prop signal:

* `pick_control_pct` — the signal-blind win rate of OUR BET on the exact
  rows that signal fired on.
* `discrimination_pp` — win rate when the signal agreed with the pick minus
  win rate when it disagreed. The only non-circular evidence a component of
  a decision can offer about itself.
* `disagree_n` — how many disagreements that number rests on.

Today those three are printed and thrown away, because the only free text
column (`notes`) holds hand-written annotations on 479 rows and a PATCH
would erase them. A number that decides a weight has to be auditable.

## After applying

Nothing. `refit_signal_registry.py` probes for the columns at startup and
starts persisting them on the next run; until then it only prints them.
