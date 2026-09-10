"""
reproduce_frozen.py — regression gate.

Rebuilds the seed ledger from the frozen reference inputs and asserts it
reproduces the frozen MSc ratings bit-for-bit. Reads only reference/, never
the live data/ ledger, so it stays valid no matter how far the live ledger
grows. Any change that breaks reproduction fails here.

Run:
    python tests/reproduce_frozen.py     # exits non-zero on failure
"""
from pathlib import Path
import sys
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import build_seed_ledger  # noqa: E402
import runner             # noqa: E402
from scoring import load_anchors  # noqa: E402

REF = ROOT / 'reference'
TOL_SCORE = 1e-9
TOL_RATING = 1e-6


def main():
    seed = build_seed_ledger.build_ledger_df()          # pure, no write
    anchors = load_anchors(ROOT / 'anchors' / 'performance.yaml')
    history, current = runner.rebuild(seed, anchors)

    frozen_hist = pd.read_parquet(REF / 'frozen_glicko_history.parquet')
    frozen_cur = pd.read_parquet(REF / 'frozen_glicko_current.parquet')

    mh = frozen_hist.merge(history, on=['FIGHT_ID', 'FIGHTER'], suffixes=('_frz', '_rep'))
    mc = frozen_cur.merge(current, on='FIGHTER', suffixes=('_frz', '_rep'))

    ok = True
    if len(mh) != len(frozen_hist):
        print(f"FAIL: history joined {len(mh):,} of {len(frozen_hist):,} rows"); ok = False
    if len(mc) != len(frozen_cur):
        print(f"FAIL: current joined {len(mc):,} of {len(frozen_cur):,} fighters"); ok = False

    checks = [('SCORE', mh, TOL_SCORE)]
    checks += [(c, mh, TOL_RATING) for c in ('RATING_POST', 'RD_POST', 'SIGMA_POST')]
    checks += [(c, mc, TOL_RATING) for c in ('RATING', 'RD', 'SIGMA')]

    for col, frame, tol in checks:
        d = (frame[col + '_frz'] - frame[col + '_rep']).abs()
        bad = int((d > tol).sum())
        if bad:
            ok = False
        print(f"  {col:12s} max|diff|={d.max():.2e}  beyond tol: {bad}  {'ok' if bad == 0 else 'FAIL'}")

    print("\nPASS" if ok else "\nFAIL")
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
