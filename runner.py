"""
runner.py — replay the bout ledger through the Glicko-2 engine.

Rebuild-from-seed: read the whole ordered ledger, score it with an anchor
profile, and replay every bout to produce the rating history and current
ratings. State is derived on each run, never carried forward, so it cannot
drift. Paths are relative to the repo root; nothing is tied to a drive.

Usage:
    python runner.py                       # performance flavour, default paths
    python runner.py --profile control     # a different anchor profile
"""
from pathlib import Path
import argparse
import pandas as pd

import glicko_engine as ge
from scoring import load_anchors, score_ledger

ROOT = Path(__file__).resolve().parent


def rd_tier(rd):
    """RD reliability tier on the display scale (from the frozen project)."""
    if rd < 125:
        return 'Established'
    if rd <= 200:
        return 'Provisional'
    return 'Unreliable'


def replay(scored):
    """
    Walk the scored ledger in date order; return (history_df, current_df).

    history_df has one row per fighter-bout (pre and post ratings);
    current_df has one row per fighter (latest post-fight state).
    """
    # Expand to one row per fighter-bout. A stable sort on (date, bout_id)
    # is what makes the replay bit-reproducible run to run.
    long = pd.concat([
        scored[['bout_id', 'event_date', 'fighter_1', 'fighter_2', 'score_1']]
            .rename(columns={'fighter_1': 'FIGHTER', 'fighter_2': 'OPPONENT', 'score_1': 'SCORE'}),
        scored[['bout_id', 'event_date', 'fighter_2', 'fighter_1', 'score_2']]
            .rename(columns={'fighter_2': 'FIGHTER', 'fighter_1': 'OPPONENT', 'score_2': 'SCORE'}),
    ], ignore_index=True).sort_values(['event_date', 'bout_id']).reset_index(drop=True)

    state = {}
    history = []

    def get(name):
        if name not in state:
            state[name] = {'mu': ge.MU, 'phi': ge.PHI, 'sigma': ge.SIGMA,
                           'last': None, 'n': 0}
        return state[name]

    for bout_id, rows in long.groupby('bout_id', sort=False):
        if len(rows) != 2:            # defensive: a bout must have two sides
            continue
        ra, rb = rows.iloc[0], rows.iloc[1]
        date = ra['event_date']
        if pd.isna(date):
            continue
        na, nb = ra['FIGHTER'], rb['FIGHTER']
        sa, sb = ra['SCORE'], rb['SCORE']
        A, B = get(na), get(nb)

        # Lazy inactivity decay, keyed to each fighter's own layoff.
        if A['last'] is not None:
            A['phi'] = ge.apply_inactivity_decay(A['phi'], A['sigma'], (date - A['last']).days)
        if B['last'] is not None:
            B['phi'] = ge.apply_inactivity_decay(B['phi'], B['sigma'], (date - B['last']).days)

        # Both fighters update from the same pre-fight snapshot.
        pa = (A['mu'], A['phi'], A['sigma'])
        pb = (B['mu'], B['phi'], B['sigma'])
        ma, pha, sga = ge.update_rating(pa[0], pa[1], pa[2], [(pb[0], pb[1])], [sa])
        mb2, phb, sgb = ge.update_rating(pb[0], pb[1], pb[2], [(pa[0], pa[1])], [sb])

        ra_pre, rda_pre = ge.scale_up(pa[0], pa[1])
        ra_post, rda_post = ge.scale_up(ma, pha)
        rb_pre, rdb_pre = ge.scale_up(pb[0], pb[1])
        rb_post, rdb_post = ge.scale_up(mb2, phb)

        A.update(mu=ma, phi=pha, sigma=sga, last=date); A['n'] += 1
        B.update(mu=mb2, phi=phb, sigma=sgb, last=date); B['n'] += 1

        history.append({'FIGHT_ID': bout_id, 'DATE': date, 'FIGHTER': na, 'OPPONENT': nb,
                        'SCORE': sa, 'RATING_PRE': ra_pre, 'RD_PRE': rda_pre,
                        'RATING_POST': ra_post, 'RD_POST': rda_post, 'SIGMA_POST': sga})
        history.append({'FIGHT_ID': bout_id, 'DATE': date, 'FIGHTER': nb, 'OPPONENT': na,
                        'SCORE': sb, 'RATING_PRE': rb_pre, 'RD_PRE': rdb_pre,
                        'RATING_POST': rb_post, 'RD_POST': rdb_post, 'SIGMA_POST': sgb})

    history_df = pd.DataFrame(history)
    current_df = pd.DataFrame([
        {'FIGHTER': name,
         'RATING': ge.scale_up(s['mu'], s['phi'])[0],
         'RD': ge.scale_up(s['mu'], s['phi'])[1],
         'SIGMA': s['sigma'], 'LAST_FIGHT': s['last'],
         'N_FIGHTS': s['n'], 'TIER': rd_tier(ge.scale_up(s['mu'], s['phi'])[1])}
        for name, s in state.items()
    ])
    return history_df, current_df


def run(ledger_path=None, anchors_path=None):
    """Load, score, and replay. Returns (history_df, current_df)."""
    ledger_path = Path(ledger_path) if ledger_path else ROOT / 'data' / 'bout_ledger.parquet'
    anchors_path = Path(anchors_path) if anchors_path else ROOT / 'anchors' / 'performance.yaml'
    ledger = pd.read_parquet(ledger_path)
    anchors = load_anchors(anchors_path)
    scored = score_ledger(ledger, anchors)
    return replay(scored)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ledger', default=None, help='path to bout_ledger.parquet')
    ap.add_argument('--profile', default='performance', help='anchor profile in anchors/')
    ap.add_argument('--anchors', default=None, help='explicit anchor YAML (overrides --profile)')
    args = ap.parse_args()

    anchors_path = args.anchors or (ROOT / 'anchors' / f'{args.profile}.yaml')
    history_df, current_df = run(args.ledger, anchors_path)

    out = ROOT / 'data'
    out.mkdir(exist_ok=True)
    history_df.to_parquet(out / 'rating_history.parquet', index=False)
    current_df.to_parquet(out / 'ratings_current.parquet', index=False)
    print(f"Replayed {history_df['FIGHT_ID'].nunique():,} bouts, {len(current_df):,} fighters.")
    print(f"Wrote rating_history.parquet ({len(history_df):,} rows) and "
          f"ratings_current.parquet ({len(current_df):,} fighters).")


if __name__ == '__main__':
    main()
