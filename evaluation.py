"""
evaluation.py — does the rating actually predict, and are its probabilities honest?

Every rating in the history is walk-forward: RATING_PRE for a fight uses only
prior fights, so predicting the outcome from the pre-fight ratings is a genuine
out-of-sample test. This module reports:

  - accuracy (how often the favourite wins),
  - Brier score and log-loss (proper scores that reward being both right AND
    honestly uncertain; lower is better),
  - a calibration table (when the model says X%, do they win X%?),
  - a temperature recalibration that corrects over/under-confidence, fitted on
    earlier fights and checked on later ones so the fix is shown to generalise.

It is a standing metric, not a pass/fail gate: run it after any change to see
whether prediction got better or worse.

Run:  python evaluation.py
"""
import numpy as np
import pandas as pd

import glicko_engine as ge

CAL_BINS = [0.0, 0.55, 0.65, 0.75, 0.85, 1.0]
EPS = 1e-12


def prediction_frame(history):
    """One row per fighter-bout with the pre-fight win probability and outcome.

    P_WIN is the pre-fight predicted probability the fighter wins, from the two
    fighters' pre-fight ratings (opponent uncertainty damps the gap, as in the
    engine). Draws are dropped; each bout contributes both fighters' rows, which
    is symmetric and unbiased for these metrics.
    """
    h = history.copy()
    h['DATE'] = pd.to_datetime(h['DATE'])
    opp = h[['FIGHT_ID', 'FIGHTER', 'RATING_PRE', 'RD_PRE']].rename(
        columns={'FIGHTER': 'OPPONENT', 'RATING_PRE': 'OPP_RATING_PRE', 'RD_PRE': 'OPP_RD_PRE'})
    h = h.merge(opp, on=['FIGHT_ID', 'OPPONENT'], how='left')
    mu_f = (h['RATING_PRE'] - 1500) / ge.SCALE
    mu_o = (h['OPP_RATING_PRE'] - 1500) / ge.SCALE
    phi_o = h['OPP_RD_PRE'] / ge.SCALE
    g_o = 1 / np.sqrt(1 + 3 * phi_o ** 2 / np.pi ** 2)
    h['P_WIN'] = 1 / (1 + np.exp(-g_o * (mu_f - mu_o)))
    h['WON'] = (h['SCORE'] > 0.5).astype(int)
    return h[h['SCORE'] != 0.5].dropna(subset=['P_WIN']).reset_index(drop=True)


def metrics(pf, p_col='P_WIN'):
    """Accuracy, Brier score and log-loss for a prediction frame."""
    p = pf[p_col].clip(EPS, 1 - EPS)
    y = pf['WON']
    return {
        'n': int(len(pf)),
        'accuracy': float((p.round() == y).mean()),
        'brier': float(((p - y) ** 2).mean()),
        'log_loss': float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean()),
    }


def calibration_table(pf, p_col='P_WIN'):
    """Predicted vs actual win rate by probability bucket."""
    b = pf.copy()
    b['bin'] = pd.cut(b[p_col], CAL_BINS)
    t = b.groupby('bin', observed=True).agg(
        predicted=(p_col, 'mean'), actual=('WON', 'mean'), n=('WON', 'size'))
    return t


def _logit(p):
    p = np.clip(p, EPS, 1 - EPS)
    return np.log(p / (1 - p))


def apply_temperature(p, s):
    """Recalibrate: scale the log-odds by s (s < 1 reduces overconfidence)."""
    return 1 / (1 + np.exp(-s * _logit(p)))


def fit_temperature(pf, p_col='P_WIN'):
    """Find the scalar s that minimises log-loss (coarse-to-fine, no scipy)."""
    y = pf['WON'].to_numpy()
    lo = _logit(pf[p_col].to_numpy())

    def loss(s):
        p = np.clip(1 / (1 + np.exp(-s * lo)), EPS, 1 - EPS)
        return -(y * np.log(p) + (1 - y) * np.log(1 - p)).mean()

    grid = np.arange(0.30, 1.61, 0.01)
    best = min(grid, key=loss)
    fine = np.arange(best - 0.01, best + 0.011, 0.001)
    return float(min(fine, key=loss))


def report(history, split_date='2024-01-01'):
    """Print the full backtest: raw metrics, calibration, and the recalibration
    fitted on fights before split_date and evaluated on fights from it on."""
    pf = prediction_frame(history)

    print("PREDICTIVE BACKTEST (walk-forward; higher pre-fight rating = favourite)\n")
    m = metrics(pf)
    print(f"  all fights (n={m['n']:,}):  accuracy {m['accuracy']*100:.1f}%   "
          f"Brier {m['brier']:.4f}   log-loss {m['log_loss']:.4f}")
    print("  reference: always 50% -> Brier 0.2500, log-loss 0.6931 "
          "(lower than these = real signal)")

    print("\nCalibration (raw):")
    ct = calibration_table(pf)
    print((ct.assign(predicted=(ct['predicted'] * 100).round(0),
                     actual=(ct['actual'] * 100).round(0))).to_string())

    # temperature recalibration, fitted on the past and checked on the future
    train = pf[pf['DATE'] < split_date]
    test = pf[pf['DATE'] >= split_date]
    if len(train) and len(test):
        s = fit_temperature(train)
        test = test.copy()
        test['P_CAL'] = apply_temperature(test['P_WIN'], s)
        before = metrics(test, 'P_WIN')
        after = metrics(test, 'P_CAL')
        print(f"\nRecalibration (temperature s={s:.3f}, fit on <{split_date}, "
              f"tested on >={split_date}, n={before['n']:,}):")
        print(f"  before:  Brier {before['brier']:.4f}   log-loss {before['log_loss']:.4f}")
        print(f"  after:   Brier {after['brier']:.4f}   log-loss {after['log_loss']:.4f}")
        print("\nCalibration after recalibration (test set):")
        ca = calibration_table(test, 'P_CAL')
        print((ca.assign(predicted=(ca['predicted'] * 100).round(0),
                         actual=(ca['actual'] * 100).round(0))).to_string())


if __name__ == '__main__':
    from pathlib import Path
    import runner
    from scoring import load_anchors
    root = Path(__file__).resolve().parent
    ledger = pd.read_parquet(root / 'data' / 'bout_ledger.parquet')
    history, _ = runner.rebuild(ledger, load_anchors(root / 'anchors' / 'performance.yaml'))
    report(history)
