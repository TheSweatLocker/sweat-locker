"""Slate artifact renderer v2 — separates WINS_ML and COVERS_SPREAD columns.

Fixes the 7/27 DET ML mispitch by making it structurally impossible to
conflate the two markets. Every game shows:
  - WINS ML column (5 lens + confluence = 6 votes) → who wins outright
  - COVERS SPREAD column (5 lens + confluence = 6 votes) → who beats runline
  - TOTAL column (5 lens = 5 votes) → over/under
Diverge banner fires prominently when ML and RL point different sides.

Reads slate_YYYYMMDD.json (enriched by _slate_analyze_v2.py).
Writes slate_YYYYMMDD_v2.html.

Usage:
  python _slate_render_v2.py                # today's slate
  python _slate_render_v2.py --date 2026-07-28
"""
import argparse
import html
import json
import sys
from datetime import date as _date
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

SCRATCH = r'C:\Users\gomez\AppData\Local\Temp\claude\c--Users-gomez-SweatShop\785f60eb-2896-47d0-b93c-5a98f036e862\scratchpad'


TEAM_SHORT = {
    'Kansas City Royals':'KC','Detroit Tigers':'DET','Colorado Rockies':'COL','Milwaukee Brewers':'MIL',
    'Toronto Blue Jays':'TOR','Boston Red Sox':'BOS','Seattle Mariners':'SEA','Texas Rangers':'TEX',
    'New York Yankees':'NYY','Philadelphia Phillies':'PHI','Houston Astros':'HOU','Chicago White Sox':'CWS',
    'Athletics':'OAK','Minnesota Twins':'MIN','Los Angeles Angels':'LAA','San Francisco Giants':'SF',
    'San Diego Padres':'SD','Miami Marlins':'MIA','Chicago Cubs':'CHC','Pittsburgh Pirates':'PIT',
    'Atlanta Braves':'ATL','Baltimore Orioles':'BAL','Cleveland Guardians':'CLE','Tampa Bay Rays':'TB',
    'Cincinnati Reds':'CIN','St. Louis Cardinals':'STL','Los Angeles Dodgers':'LAD','New York Mets':'NYM',
    'Arizona Diamondbacks':'ARI','Washington Nationals':'WSH',
}


def short(t): return TEAM_SHORT.get(t, (t[:3].upper() if t else '?'))
def fmt(v, prec=1):
    if v is None: return '—'
    try: return f'{float(v):+.{prec}f}'
    except: return str(v)
def fmtn(v, prec=1):
    if v is None: return '—'
    try: return f'{float(v):.{prec}f}'
    except: return str(v)
def pct(v):
    if v is None: return '—'
    try: return f'{float(v)*100:.0f}%'
    except: return '—'


def side_chip(letter):
    """H/A/O/U chip with color coding."""
    if not letter or letter == '-':
        return '<span class="chip na">—</span>'
    color = {'H':'home','A':'away','O':'over','U':'under'}.get(letter, 'na')
    return f'<span class="chip {color}">{letter}</span>'


def render(payload_path: str, out_path: str):
    with open(payload_path, encoding='utf-8') as f:
        d = json.load(f)

    games = d.get('games', [])
    # Rank by strongest confluence (ML + RL + Total combined)
    def score(g):
        return (g.get('_ml_lead', ['-',0])[1] +
                g.get('_rl_lead', ['-',0])[1] +
                g.get('_total_lead', ['-',0])[1])
    games = sorted(games, key=lambda g: -score(g))

    rows = []
    for g in games:
        away = g.get('away','?'); home = g.get('home','?')
        sh_a = short(away); sh_h = short(home)
        sp = g.get('close_spread'); tot = g.get('close_total')
        mc = g.get('mc_probs') or {}

        ml_picks = g.get('_ml_picks') or {}
        rl_picks = g.get('_rl_picks') or {}
        tot_picks = g.get('_total_picks') or {}
        margins = g.get('_lens_margins') or {}

        ml_side, ml_n = g.get('_ml_lead', ['-', 0])
        rl_side, rl_n = g.get('_rl_lead', ['-', 0])
        tot_side, tot_n = g.get('_total_lead', ['-', 0])

        # Diverge = ML pick ≠ RL pick (different market call)
        diverge = ml_side != rl_side and ml_side in ('H','A') and rl_side in ('H','A')
        diverge_html = ''
        if diverge:
            fav_team = sh_h if ml_side == 'H' else sh_a
            cov_team = sh_h if rl_side == 'H' else sh_a
            diverge_html = f'''
            <div class="diverge-banner">
              ⚠ ML vs RL DIVERGE — <b>{fav_team}</b> wins ML ({ml_n}/6) but <b>{cov_team}</b> covers spread ({rl_n}/6).
              Take ML on {fav_team} OR runline on {cov_team} — different bets on the same game.
            </div>'''

        # Sweat + MC-HC
        sweat_tier = g.get('sweat_tier') or 'PASS'
        sweat_score = g.get('sweat_score') or 0
        sweat_cls = {'PRIME':'sweat-prime','STRONG':'sweat-strong','LIGHT_LEAN':'sweat-light','PASS':'sweat-pass'}.get(sweat_tier, 'sweat-pass')

        hc_html = ''
        if g.get('mc_hc_flag'):
            hc_side = g.get('mc_hc_side')
            hc_pct_val = g.get('mc_hc_pct') or 0
            hc_html = f'<span class="mc-hc">🎯 MC-HC {hc_side} {float(hc_pct_val)*100:.0f}%</span>'

        # Confluence direction
        conf = g.get('conf_net') or 0

        # Primary play (from context.primary_play)
        pp = g.get('primary_play') or {}
        pp_html = ''
        if pp.get('tier'):
            tier_cls = {'PRIME':'pp-prime','STRONG':'pp-strong','LEAN':'pp-lean','LIGHT':'pp-light'}.get(pp['tier'],'pp-light')
            pp_html = f'<div class="pp {tier_cls}"><b>{pp["tier"]}</b> · {html.escape(pp.get("label","") or "")}<br><span class="pp-sub">{html.escape(pp.get("sub","") or "")}</span></div>'

        # Props summary
        props = g.get('props') or []
        prime_p = [p for p in props if p.get('tier') == 'PRIME']
        strong_p = [p for p in props if p.get('tier') == 'STRONG']
        prop_summary = ''
        if prime_p or strong_p:
            parts = []
            if prime_p: parts.append(f'<span class="prime-tag">PRIME×{len(prime_p)}</span>')
            if strong_p: parts.append(f'<span class="strong-tag">STRONG×{len(strong_p)}</span>')
            prop_summary = ' '.join(parts)

        prop_details_html = ''
        for p in sorted(props, key=lambda x: -(x.get('conviction') or 0)):
            tier_cls = 'prime-tag' if p['tier'] == 'PRIME' else 'strong-tag'
            prop_details_html += f'<div class="prop-row"><span class="{tier_cls}">{p["tier"]}</span> <span class="pcv">{p.get("conviction","?")}</span> {html.escape(p.get("player_name") or "?")} · {p.get("prop_type","?")} {p.get("direction","?")} {p.get("prop_line","?")}</div>'

        # Externals
        ext_html = ''
        for e in (g.get('externals') or [])[:12]:
            src = e.get('source','?'); side = e.get('pick_side','?'); surf = e.get('surface','?')
            ext_html += f'<span class="ext-item">{html.escape(src)} · <span class="ext-surf">{surf}</span> {side}</span>'

        # ML lens matrix (5 lens + confluence)
        conf_side = 'H' if conf > 0 else 'A' if conf < 0 else None
        ml_rows_html = ''
        for name, key in [('Panel','panel'),('Jerry','jerry'),('v3 comp','v3'),('v4 XGB','v4'),('MC sim','mc')]:
            pick = ml_picks.get(key)
            m = margins.get(key)
            m_str = f'margin {fmt(m)}' if m is not None else '—'
            ml_rows_html += f'<div class="lens-row"><span class="lens-name">{name}</span>{side_chip(pick)} <span class="lens-val">{m_str}</span></div>'
        ml_rows_html += f'<div class="lens-row"><span class="lens-name">Confluence</span>{side_chip(conf_side)} <span class="lens-val">net {conf:+d}</span></div>'

        # RL lens matrix
        rl_rows_html = ''
        for name, key in [('Panel','panel'),('Jerry','jerry'),('v3 comp','v3'),('v4 XGB','v4'),('MC sim','mc')]:
            pick = rl_picks.get(key)
            m = margins.get(key)
            cover_str = f'cover {fmt((m or 0) + (sp or 0)) if m is not None and sp is not None else "—"}'
            rl_rows_html += f'<div class="lens-row"><span class="lens-name">{name}</span>{side_chip(pick)} <span class="lens-val">{cover_str}</span></div>'
        rl_rows_html += f'<div class="lens-row"><span class="lens-name">Confluence</span>{side_chip(conf_side)} <span class="lens-val">net {conf:+d}</span></div>'

        # Total lens matrix
        tot_rows_html = ''
        for name, key, tot_key in [('Panel','panel','panel_total'),('Jerry','jerry','jerry_total'),
                                     ('v3 comp','v3','v3_total'),('v4 XGB','v4','v4_total')]:
            pick = tot_picks.get(key)
            t = g.get(tot_key)
            tot_rows_html += f'<div class="lens-row"><span class="lens-name">{name}</span>{side_chip(pick)} <span class="lens-val">t={fmtn(t,2)}</span></div>'
        mc_mt = mc.get('mc_mean_total')
        tot_rows_html += f'<div class="lens-row"><span class="lens-name">MC sim</span>{side_chip(tot_picks.get("mc"))} <span class="lens-val">μ {fmtn(mc_mt,2)}</span></div>'

        # ML odds display
        ml_line_html = f'ML: {g.get("home_ml") or "—"} / {g.get("away_ml") or "—"}'

        rows.append(f'''
        <div class="game">
          <div class="game-header">
            <div class="matchup"><span class="team-away">{sh_a}</span> <span class="at">@</span> <span class="team-home">{sh_h}</span></div>
            <div class="market">
              {sh_h} <span class="line">{fmt(sp)}</span> · O/U <span class="line">{fmtn(tot,1)}</span>
              <br><span class="ml-line">{ml_line_html}</span>
            </div>
            <div class="sweat {sweat_cls}">{sweat_tier} <span class="sweat-score">{sweat_score}</span></div>
            {hc_html}
          </div>
          {diverge_html}
          <div class="lens-matrix">
            <div class="lens-col">
              <div class="lens-lbl">WINS ML · {ml_n}/6 {ml_side}</div>
              {ml_rows_html}
            </div>
            <div class="lens-col">
              <div class="lens-lbl">COVERS SPREAD · {rl_n}/6 {rl_side}</div>
              {rl_rows_html}
            </div>
            <div class="lens-col">
              <div class="lens-lbl">TOTAL · {tot_n}/5 {tot_side}</div>
              {tot_rows_html}
            </div>
          </div>
          <div class="model-strip">
            {pp_html}
            <div class="props-summary">{prop_summary}</div>
          </div>
          <details class="drawer">
            <summary>Details · props ({len(props)}) · externals ({len(g.get("externals") or [])})</summary>
            <div class="drawer-body">
              <div class="drawer-half"><h4>Props</h4>{prop_details_html or '<span class="na">none</span>'}</div>
              <div class="drawer-half"><h4>Externals</h4><div class="ext-list">{ext_html or '<span class="na">none</span>'}</div></div>
            </div>
          </details>
        </div>''')

    # Summary counts
    n_diverge = sum(1 for g in games
                    if g.get('_ml_lead',['-',0])[0] != g.get('_rl_lead',['-',0])[0]
                    and g.get('_ml_lead',['-',0])[0] in ('H','A'))
    n_ml_locks = sum(1 for g in games if g.get('_ml_lead',['-',0])[1] >= 5)
    n_rl_locks = sum(1 for g in games if g.get('_rl_lead',['-',0])[1] >= 5)

    slate_date = d.get('date', 'today')

    html_out = f'''<title>MLB Slate · {slate_date} · v2</title>
<style>
:root {{
  --bg: #0a0e14; --panel: #12161d; --panel2: #1a1f28;
  --border: #202832; --border-hi: #2c3644;
  --text: #d8e0ea; --text-mute: #7a8898; --text-dim: #4d5866;
  --accent: #00d4a4;
  --home: #00d4a4; --away: #ff5670;
  --over: #ffb020; --under: #6f8fff;
  --prime: #ffd166; --strong: #00d4a4; --lean: #9aa7ba; --pass: #4d5866;
  --mc-hc: #ff8ac2; --diverge: #ff8c5a;
  --font-mono: ui-monospace, "SF Mono", Consolas, monospace;
  --font-sans: -apple-system, "Inter", system-ui, sans-serif;
}}
body {{
  background: var(--bg); color: var(--text); font-family: var(--font-sans);
  font-size: 13px; line-height: 1.5; margin: 0; padding: 24px 16px 64px;
  max-width: 1400px; margin-inline: auto;
  font-variant-numeric: tabular-nums;
}}
h1 {{ font-size: 22px; margin: 0 0 4px; font-weight: 700; }}
.subtitle {{ color: var(--text-mute); font-size: 13px; margin: 0 0 20px; }}
.subtitle .sep {{ color: var(--text-dim); margin: 0 8px; }}
.bug-fix-banner {{
  background: rgba(255,140,90,0.08); border: 1px solid rgba(255,140,90,0.3);
  border-left: 3px solid var(--diverge); border-radius: 6px;
  padding: 10px 14px; margin-bottom: 20px; font-size: 12px;
  color: var(--text);
}}
.bug-fix-banner b {{ color: var(--diverge); }}
.summary {{
  display: grid; grid-template-columns: repeat(auto-fit,minmax(120px,1fr));
  gap: 8px; margin-bottom: 24px;
}}
.stat-box {{
  background: var(--panel); border: 1px solid var(--border); border-radius: 6px;
  padding: 10px 12px;
}}
.stat-num {{ font-family: var(--font-mono); font-size: 20px; font-weight: 700; color: var(--text); display: block; }}
.stat-lbl {{ font-size: 10px; color: var(--text-mute); text-transform: uppercase; letter-spacing: 0.05em; margin-top: 2px; display: block; }}
.stat-box.warn .stat-num {{ color: var(--diverge); }}
.stat-box.warn {{ border-color: var(--diverge); }}
h2 {{
  font-size: 12px; color: var(--text-mute); text-transform: uppercase;
  letter-spacing: 0.08em; font-weight: 600; margin: 32px 0 12px;
  padding-bottom: 6px; border-bottom: 1px solid var(--border);
}}

.game {{
  background: var(--panel); border: 1px solid var(--border);
  border-radius: 8px; overflow: hidden; margin-bottom: 12px;
}}
.game-header {{
  display: grid; grid-template-columns: 140px 200px 1fr 140px 160px;
  gap: 12px; padding: 12px 14px; background: var(--panel2);
  border-bottom: 1px solid var(--border); align-items: center;
  font-family: var(--font-mono); font-size: 12px;
}}
.matchup {{ font-weight: 700; color: var(--text); font-size: 14px; }}
.team-away {{ color: var(--away); }}
.team-home {{ color: var(--home); }}
.at {{ color: var(--text-dim); margin: 0 6px; font-weight: 400; }}
.market {{ color: var(--text-mute); line-height: 1.6; }}
.market .line {{ color: var(--text); font-weight: 600; }}
.ml-line {{ font-size: 10px; color: var(--text-dim); }}
.sweat {{ font-weight: 700; }}
.sweat-score {{ font-size: 10px; color: var(--text-dim); font-weight: 400; margin-left: 4px; }}
.sweat-prime {{ color: var(--prime); }}
.sweat-strong {{ color: var(--strong); }}
.sweat-light {{ color: var(--lean); }}
.sweat-pass {{ color: var(--pass); }}
.mc-hc {{
  background: rgba(255,138,194,0.15); border: 1px solid rgba(255,138,194,0.4);
  color: var(--mc-hc); padding: 3px 8px; border-radius: 10px;
  font-size: 11px; font-weight: 700; justify-self: end;
}}

.diverge-banner {{
  background: rgba(255,140,90,0.10);
  border-bottom: 1px solid rgba(255,140,90,0.3);
  color: var(--text); padding: 8px 14px; font-size: 12px; line-height: 1.5;
}}
.diverge-banner b {{ color: var(--diverge); }}

.lens-matrix {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 0; }}
.lens-col {{ padding: 12px 14px; border-right: 1px solid var(--border); }}
.lens-col:last-child {{ border-right: none; }}
.lens-lbl {{
  font-size: 10px; color: var(--text-dim); text-transform: uppercase;
  letter-spacing: 0.08em; font-weight: 600; margin-bottom: 8px;
}}
.lens-row {{
  display: grid; grid-template-columns: 90px 34px 1fr; gap: 8px;
  align-items: center; padding: 4px 0;
  font-family: var(--font-mono); font-size: 11px;
}}
.lens-name {{ color: var(--text-mute); }}
.lens-val {{ color: var(--text); font-size: 11px; }}

.chip {{
  display: inline-block; min-width: 28px; text-align: center;
  padding: 2px 6px; border-radius: 4px; font-size: 10px;
  font-weight: 700; font-family: var(--font-mono); border: 1px solid transparent;
}}
.chip.home {{ background: rgba(0,212,164,0.15); color: var(--home); border-color: rgba(0,212,164,0.3); }}
.chip.away {{ background: rgba(255,86,112,0.15); color: var(--away); border-color: rgba(255,86,112,0.3); }}
.chip.over {{ background: rgba(255,176,32,0.15); color: var(--over); border-color: rgba(255,176,32,0.3); }}
.chip.under {{ background: rgba(111,143,255,0.15); color: var(--under); border-color: rgba(111,143,255,0.3); }}
.chip.na {{ background: transparent; color: var(--text-dim); border-color: var(--border); }}

.model-strip {{ padding: 10px 14px; background: rgba(0,0,0,0.15); }}
.pp {{ padding: 10px 12px; border-radius: 6px; margin-bottom: 8px; font-family: var(--font-mono); font-size: 12px; }}
.pp b {{ font-weight: 700; }}
.pp-sub {{ color: var(--text-mute); font-size: 10px; }}
.pp-prime {{ background: rgba(255,209,102,0.1); border: 1px solid rgba(255,209,102,0.3); color: var(--prime); }}
.pp-strong {{ background: rgba(0,212,164,0.1); border: 1px solid rgba(0,212,164,0.3); color: var(--strong); }}
.pp-lean, .pp-light {{ background: var(--panel2); border: 1px solid var(--border); color: var(--text); }}
.prime-tag, .strong-tag {{
  display: inline-block; padding: 2px 7px; border-radius: 3px;
  font-size: 10px; font-weight: 700; font-family: var(--font-mono); margin-right: 4px;
}}
.prime-tag {{ background: rgba(255,209,102,0.15); color: var(--prime); border: 1px solid rgba(255,209,102,0.3); }}
.strong-tag {{ background: rgba(0,212,164,0.12); color: var(--strong); border: 1px solid rgba(0,212,164,0.3); }}
.na {{ color: var(--text-dim); font-size: 10px; font-style: italic; }}

.drawer {{ border-top: 1px solid var(--border); background: rgba(0,0,0,0.15); }}
.drawer summary {{ padding: 8px 14px; color: var(--text-mute); font-size: 11px; cursor: pointer; }}
.drawer-body {{ padding: 12px 14px; display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
.drawer-half h4 {{ font-size: 10px; color: var(--text-dim); text-transform: uppercase; margin: 0 0 8px; }}
.prop-row {{ padding: 3px 0; font-family: var(--font-mono); font-size: 11px; border-bottom: 1px dashed var(--border); }}
.pcv {{ color: var(--text); margin-right: 6px; }}
.ext-list {{ display: flex; flex-direction: column; gap: 3px; font-family: var(--font-mono); font-size: 11px; }}
.ext-item {{ padding: 2px 0; color: var(--text); }}
.ext-surf {{ color: var(--text-dim); font-size: 10px; text-transform: uppercase; }}

@media (max-width: 900px) {{
  .game-header {{ grid-template-columns: 1fr; gap: 6px; }}
  .lens-matrix {{ grid-template-columns: 1fr; }}
  .lens-col {{ border-right: none; border-bottom: 1px solid var(--border); }}
  .drawer-body {{ grid-template-columns: 1fr; }}
}}
</style>

<h1>MLB Slate · {slate_date} · v2 (ML/RL separated)</h1>
<p class="subtitle">{len(games)} games <span class="sep">·</span> WINS_ML and COVERS_SPREAD shown as separate columns · diverge banner fires when they disagree</p>

<div class="bug-fix-banner">
  <b>v2 fix (2026-07-27):</b> previous "SIDE" column conflated ML-winner and spread-cover picks — a "6/6 lens confluence" for spread cover could be only 4/6 for ML. Public DET ML rec was mispitched as a result. v2 shows both markets separately, with a DIVERGE banner when they disagree. About half of MLB games have ML ≠ RL because margins cluster near the +/-1.5 runline.
</div>

<div class="summary">
  <div class="stat-box"><span class="stat-num">{len(games)}</span><span class="stat-lbl">Games</span></div>
  <div class="stat-box {"warn" if n_diverge else ""}"><span class="stat-num">{n_diverge}</span><span class="stat-lbl">ML ≠ RL</span></div>
  <div class="stat-box"><span class="stat-num">{n_ml_locks}</span><span class="stat-lbl">ML 5+/6 lens</span></div>
  <div class="stat-box"><span class="stat-num">{n_rl_locks}</span><span class="stat-lbl">RL 5+/6 lens</span></div>
</div>

<h2>Full Slate</h2>
{"".join(rows)}
'''
    Path(out_path).write_text(html_out, encoding='utf-8')
    print(f'OK rendered {len(games)} games → {out_path}')
    print(f'   ML ≠ RL diverge: {n_diverge}/{len(games)}')
    print(f'   ML 5+/6 locks: {n_ml_locks}')
    print(f'   RL 5+/6 locks: {n_rl_locks}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=None, help='YYYY-MM-DD (default: today)')
    args = ap.parse_args()
    d = args.date or _date.today().isoformat().replace('-','')
    if len(d) == 10 and '-' in d:
        d = d.replace('-','')  # 2026-07-28 → 20260728
    payload = f'{SCRATCH}\\slate_{d[-3:] if len(d)==8 else d}.json'
    # Support both slate_727.json and slate_20260727.json naming
    if not Path(payload).exists():
        payload = f'{SCRATCH}\\slate_{d}.json'
    out = payload.replace('.json', '_v2.html')
    render(payload, out)


if __name__ == '__main__':
    main()
