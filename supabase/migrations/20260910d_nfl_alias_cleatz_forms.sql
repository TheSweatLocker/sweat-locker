-- 2026-09-10 · NFL alias seed for cleatz "ABBREV Mascot" forms
-- Cleatz publishes team names as "SF 49ers" / "LA Chargers" / "NY Jets" — the
-- variant builder in splits_v2_pipeline._game_id_lookup adds "{canon} {mascot}"
-- which gives "NYJ Jets" (wrong — cleatz uses "NY Jets"). Adding these forms
-- to alt_names[] closes the gap and 15/16 NFL Wk 1 games now resolve.
--
-- LAC/LA collision note: cleatz uses "LA" as prefix for BOTH the Rams and
-- Chargers ("LA Rams" and "LA Chargers"). Because alt_names sit per-canon,
-- these end up in LA's variant set and LAC's variant set respectively —
-- no lookup collision because the pairing is (away, home) game-scoped.
--
-- Applied via API 2026-09-10 (patched in place). This migration re-applies
-- the same additions declaratively for schema completeness.

DO $$
DECLARE
  patches jsonb := $j$
    {
      "SF":  ["SF 49ers", "SFO 49ers", "San Fran"],
      "NYJ": ["NY Jets"],
      "NYG": ["NY Giants"],
      "LA":  ["LA Rams"],
      "LAC": ["LA Chargers"],
      "ARI": ["ARI Cardinals", "AZ Cardinals"],
      "DAL": ["DAL Cowboys"],
      "TEN": ["TEN Titans"],
      "NE":  ["NE Patriots"],
      "BUF": ["BUF Bills"],
      "MIA": ["MIA Dolphins"],
      "BAL": ["BAL Ravens"],
      "CIN": ["CIN Bengals"],
      "CLE": ["CLE Browns"],
      "PIT": ["PIT Steelers"],
      "HOU": ["HOU Texans"],
      "IND": ["IND Colts"],
      "JAX": ["JAX Jaguars", "JAC Jaguars"],
      "DEN": ["DEN Broncos"],
      "KC":  ["KC Chiefs"],
      "LV":  ["LV Raiders"],
      "PHI": ["PHI Eagles"],
      "WAS": ["WAS Commanders", "WSH Commanders"],
      "CHI": ["CHI Bears"],
      "DET": ["DET Lions"],
      "GB":  ["GB Packers", "GNB Packers"],
      "MIN": ["MIN Vikings"],
      "ATL": ["ATL Falcons"],
      "CAR": ["CAR Panthers"],
      "NO":  ["NO Saints", "NOR Saints"],
      "TB":  ["TB Buccaneers", "TAM Buccaneers"],
      "SEA": ["SEA Seahawks"]
    }
  $j$;
  team text;
  new_forms jsonb;
BEGIN
  FOR team, new_forms IN SELECT * FROM jsonb_each(patches) LOOP
    UPDATE nfl_team_aliases
    SET alt_names = ARRAY(
      SELECT DISTINCT unnest(
        COALESCE(alt_names, ARRAY[]::text[])
        || ARRAY(SELECT jsonb_array_elements_text(new_forms))
      )
      ORDER BY 1
    ),
    updated_at = NOW()
    WHERE canonical_name = team;
  END LOOP;
END $$;

NOTIFY pgrst, 'reload schema';
