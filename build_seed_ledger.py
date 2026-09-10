"""
build_seed_ledger.py — one-off construction of the seed bout ledger.

Turns the frozen MSc intermediates (kept in reference/) into the live
data/bout_ledger.parquet: one row per bout, raw per-fighter totals, coverage
and modelable flags, no score baked in. Future events are appended to the
ledger by the ingestion step; this builder only makes the historical seed.

Run once:
    python build_seed_ledger.py
"""
from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent
REF = ROOT / 'reference'
DATA = ROOT / 'data'


def build():
    # Covered bouts carry round-level stats. Keep one row per bout id (last
    # wins, matching the frozen lookup's overwrite behaviour on duplicates).
    bp = pd.read_parquet(REF / 'frozen_bout_pairs.parquet').drop_duplicates('FIGHT_ID', keep='last')
    fpf = pd.read_parquet(REF / 'frozen_fights_per_fighter.parquet')

    # fighter_1 = winner, fighter_2 = loser; stats aligned to each.
    covered = pd.DataFrame({
        'bout_id': bp['FIGHT_ID'], 'event_date': bp['date'],
        'fighter_1': bp['FIGHTER_W'], 'fighter_2': bp['FIGHTER_L'], 'outcome': 'F1_WIN',
        'method': bp['method'], 'method_cat': bp['method_cat'], 'finish_round': bp['finish_round'],
        'sig_landed_1': bp['SIG.STR._landed_W'], 'sig_landed_2': bp['SIG.STR._landed_L'],
        'sig_att_1': bp['SIG.STR._attempted_W'], 'sig_att_2': bp['SIG.STR._attempted_L'],
        'td_1': bp['TD_landed_W'], 'td_2': bp['TD_landed_L'],
        'subatt_1': bp['SUB.ATT_num_W'], 'subatt_2': bp['SUB.ATT_num_L'],
        'kd_1': bp['KD_num_W'], 'kd_2': bp['KD_num_L'],
        'ctrl_1': bp['CTRL_secs_W'], 'ctrl_2': bp['CTRL_secs_L'],
        'stats_coverage': True,
    })

    # Remaining bouts have no round-level coverage. Pivot fpf's two rows per
    # bout into one ledger row with null stats (binary-scored at replay).
    missing = set(fpf['FIGHT_ID']) - set(bp['FIGHT_ID'])
    records = []
    for fid, g in fpf[fpf['FIGHT_ID'].isin(missing)].groupby('FIGHT_ID', sort=False):
        win = g[g['RESULT'] == 'W']
        if len(win) == 1:
            f1 = win.iloc[0]
            f2 = g[g['FIGHTER'] != f1['FIGHTER']].iloc[0]
            outcome = 'F1_WIN'
        else:                                   # draw or no clear winner
            f1, f2 = g.iloc[0], g.iloc[1]
            outcome = 'DRAW'
        records.append({
            'bout_id': fid, 'event_date': f1['DATE_PARSED'],
            'fighter_1': f1['FIGHTER'], 'fighter_2': f2['FIGHTER'], 'outcome': outcome,
            'method': f1['METHOD'], 'method_cat': f1['METHOD_CAT'], 'finish_round': np.nan,
            'sig_landed_1': np.nan, 'sig_landed_2': np.nan, 'sig_att_1': np.nan, 'sig_att_2': np.nan,
            'td_1': np.nan, 'td_2': np.nan, 'subatt_1': np.nan, 'subatt_2': np.nan,
            'kd_1': np.nan, 'kd_2': np.nan, 'ctrl_1': np.nan, 'ctrl_2': np.nan,
            'stats_coverage': False,
        })
    uncovered = pd.DataFrame(records)

    ledger = pd.concat([covered, uncovered], ignore_index=True)
    ledger['modelable'] = True
    ledger['source'] = 'seed'
    ledger['ingest_snapshot'] = 'freeze_ufc329_2026-07-11'

    DATA.mkdir(exist_ok=True)
    out = DATA / 'bout_ledger.parquet'
    ledger.to_parquet(out, index=False)
    print(f"Wrote {out}")
    print(f"  {len(ledger):,} bouts "
          f"({int(ledger['stats_coverage'].sum()):,} covered, "
          f"{int((~ledger['stats_coverage']).sum()):,} binary)")
    return ledger


if __name__ == '__main__':
    build()
