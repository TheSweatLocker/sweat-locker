"""Judge every allowlisted prop cell on its FORWARD record, not the one that chose it.

══ 2026-11-06 · R171 ════════════════════════════════════════════════════
`prop_cell_allowlist` gates non-MLB prop publishing to cells with a measured
positive record. Pooled, what it publishes runs +4.49pp on n=476 against
−2.59pp on the 4,517 rows it blocks, so the gate is doing real work.

But that +4.49pp is **in-sample**: the cells were selected on that data, and
picking the best three of ~40 cells guarantees a positive number. A gate
chosen on a record cannot be validated by that record
([[feedback_walk_forward_misses_feature_leak]]).

So this reports each cell twice, split at its `CELL_SINCE` ship date, and
the number to act on is always the OUT-OF-SAMPLE one. It also applies the
allowlist's own rule 4 — an edge must clear one standard error — to the
forward half, and names any cell whose forward sign has flipped.

Everything is reported with n, and a cell under `--min-n` is reported as
UNMEASURED rather than given a verdict
([[feedback_sample_size_with_pct]], [[feedback_unmeasured_signal_must_not_outweigh_measured]]).

  python audit_prop_cells.py
  python audit_prop_cells.py --min-n 50 --json out.json
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import os
import sys
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                              # noqa: BLE001
    pass

for _l in Path(__file__).with_name('.env').read_text().split('\n'):
    if '=' in _l and not _l.startswith('#'):
        _k, _v = _l.split('=', 1)
        os.environ.setdefault(_k.strip(), _v.strip())
SB = os.environ['SUPABASE_URL'].rstrip('/')
K = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ['SUPABASE_KEY']
H = {'apikey': K, 'Authorization': 'Bearer ' + K}

sys.path.insert(0, str(Path(__file__).parent))
from prop_cell_allowlist import (                              # noqa: E402
    ALLOWLIST, ODDS_MAX, ODDS_MIN, PROP_TABLE, cell_for, cell_since, row_odds)

GRADED = ('WIN', 'LOSS', 'PUSH')


def paged(table, select, extra=''):
    out, off = [], 0
    while True:
        r = requests.get('%s/rest/v1/%s' % (SB, table), headers=H, timeout=180,
                         params={'select': select, 'limit': 1000,
                                 'offset': off, 'order': 'id.asc'})
        if r.status_code not in (200, 206):
            raise RuntimeError('%s fetch HTTP %s %s'
                               % (table, r.status_code, r.text[:200]))
        ch = r.json()
        out += ch
        if len(ch) < 1000:
            return out
        off += 1000


class Acc:
    __slots__ = ('w', 'l', 'p', 'units', 'be')

    def __init__(self):
        self.w = self.l = self.p = 0
        self.units = self.be = 0.0

    def add(self, res, o):
        if res == 'WIN':
            self.w += 1
            self.units += (o / 100.0) if o > 0 else (100.0 / abs(o))
        elif res == 'LOSS':
            self.l += 1
            self.units -= 1.0
        else:
            self.p += 1
        self.be += (100.0 * abs(o) / (abs(o) + 100.0)) if o < 0 \
            else (100.0 * 100.0 / (o + 100.0))

    @property
    def dec(self):
        return self.w + self.l

    def stats(self):
        n = self.dec
        if not n:
            return None
        hit = 100.0 * self.w / n
        be = self.be / (n + self.p)
        se = math.sqrt(0.25 / n) * 100.0
        return {'n': n, 'w': self.w, 'l': self.l, 'push': self.p,
                'hit_pct': round(hit, 2), 'be_pct': round(be, 2),
                'edge_pp': round(hit - be, 2), 'se_pp': round(se, 2),
                'edge_over_se': round((hit - be) / se, 2) if se else 0.0,
                'roi_pct': round(100.0 * self.units / n, 2),
                'units': round(self.units, 2)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--min-n', type=int, default=30,
                    help='below this the forward half is UNMEASURED, not a verdict')
    ap.add_argument('--json', help='also write the report here')
    a = ap.parse_args()

    print('=== allowlisted prop cells · in-sample vs FORWARD ===\n')
    report = {'min_n': a.min_n, 'cells': []}
    verdicts = collections.Counter()

    for sport, cells in sorted(ALLOWLIST.items()):
        if not cells:
            print('%-6s no allowlisted cell' % sport)
            continue
        table = PROP_TABLE.get(sport)
        if not table:
            print('%-6s no prop table mapping — cannot measure' % sport)
            continue
        rows = paged(table, 'id,game_date,prop_type,tier,direction,'
                            'book_over_odds,book_under_odds,result')
        pre = collections.defaultdict(Acc)
        post = collections.defaultdict(Acc)
        for r in rows:
            res = str(r.get('result') or '').upper()
            if res not in GRADED:
                continue
            ck = (str(r.get('prop_type')), str(r.get('tier') or '').upper())
            if cell_for(sport, *ck) is None:
                continue
            o = row_odds(r)
            if o is None or not (ODDS_MIN <= o <= ODDS_MAX):
                continue
            since = cell_since(sport, *ck)
            gd = str(r.get('game_date') or '')
            tgt = post if (since and gd >= since) else pre
            tgt[ck].add(res, o)
            tgt[('_ALL', '')].add(res, o)

        print('%-6s %d allowlisted cell(s)' % (sport, len(cells)))
        for ck in sorted(cells):
            s_in = pre[ck].stats()
            s_out = post[ck].stats()
            since = cell_since(sport, *ck)
            name = '%s %s' % ck

            # ── verdict on the FORWARD half only ──
            if s_out is None or s_out['n'] < a.min_n:
                v = 'UNMEASURED'
                why = ('forward n=%d is under %d — no verdict, and this cell '
                       'must not be sized as an edge'
                       % (0 if s_out is None else s_out['n'], a.min_n))
            elif s_out['edge_pp'] <= 0:
                v = 'SIGN FLIPPED'
                why = ('forward edge %+.2fpp has gone the wrong way; the '
                       'in-sample %+.2fpp was selection'
                       % (s_out['edge_pp'],
                          s_in['edge_pp'] if s_in else 0.0))
            elif s_out['edge_over_se'] < 1.0:
                v = 'HOLDING, THIN'
                why = ('forward edge %+.2fpp does not clear one SE (%.2f) — '
                       'rule 4 would not admit it on these numbers alone'
                       % (s_out['edge_pp'], s_out['se_pp']))
            else:
                v = 'HOLDING'
                why = ('forward edge %+.2fpp at %+.2f SE'
                       % (s_out['edge_pp'], s_out['edge_over_se']))
            verdicts[v] += 1

            print('  %-36s since %s   %s' % (name, since or '(unrecorded)', v))
            for lbl, st in (('in-sample ', s_in), ('FORWARD   ', s_out)):
                if st is None:
                    print('    %s n=0' % lbl)
                    continue
                print('    %s n=%-5d %4d-%-4d hit %6.2f%% BE %6.2f%% '
                      'edge %+6.2fpp (%+.2f SE) ROI %+7.2f%%'
                      % (lbl, st['n'], st['w'], st['l'], st['hit_pct'],
                         st['be_pct'], st['edge_pp'], st['edge_over_se'],
                         st['roi_pct']))
            print('    -> %s' % why)
            report['cells'].append({'sport': sport, 'prop_type': ck[0],
                                    'tier': ck[1], 'since': since,
                                    'verdict': v, 'why': why,
                                    'in_sample': s_in, 'forward': s_out})

        st = post[('_ALL', '')].stats()
        si = pre[('_ALL', '')].stats()
        print('  %-36s' % ('%s ALL allowlisted' % sport))
        for lbl, x in (('in-sample ', si), ('FORWARD   ', st)):
            if x:
                print('    %s n=%-5d edge %+6.2fpp (%+.2f SE) ROI %+7.2f%%'
                      % (lbl, x['n'], x['edge_pp'], x['edge_over_se'],
                         x['roi_pct']))
        print()

    print('verdicts: %s' % dict(verdicts))
    if verdicts['SIGN FLIPPED']:
        print('\n*** %d cell(s) have flipped sign out-of-sample. Remove them '
              'from ALLOWLIST before the next slate. ***'
              % verdicts['SIGN FLIPPED'])
    if a.json:
        Path(a.json).write_text(json.dumps(report, indent=1, default=str))
        print('wrote %s' % a.json)
    # Never fail the pipeline on a thin or unmeasured cell; a red step that
    # is permanently red is a muted step
    # ([[feedback_a_detector_must_know_the_intentional_case]]).
    return 0


if __name__ == '__main__':
    sys.exit(main())
