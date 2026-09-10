"""
scoring.py — continuous-S dominance scoring.

Turns raw per-fighter bout stats into the winner's continuous-S score using
a dominance-weight profile from anchors/<flavour>.yaml. Transcribed from
notebook 11 (blocks 22, 23) and validated to reproduce the frozen scores
bit-exactly.

The score is a function of the winner-minus-loser differences, so swapping
the anchor profile is all it takes to produce a different flavour. Bouts
without stat coverage, and draws, use a plain binary result instead.
"""
import yaml
import pandas as pd


def load_anchors(path):
    """Load a dominance-weight profile (YAML) into a dict."""
    with open(path) as f:
        return yaml.safe_load(f)


def method_to_baseline_key(method_str):
    """Map a raw method string ('KO/TKO', 'Decision - Split') to a baseline key."""
    if pd.isna(method_str):
        return None
    m = str(method_str).lower()
    # Decision variants are checked before the bare 'decision' catch-all.
    if 'decision' in m and 'unanimous' in m:
        return 'decision_unanimous'
    if 'decision' in m and 'majority' in m:
        return 'decision_majority'
    if 'decision' in m and 'split' in m:
        return 'decision_split'
    if 'decision' in m:
        return 'decision_unanimous'          # unlabelled decision
    if 'submission' in m:
        return 'submission'
    if 'doctor' in m or 'stoppage' in m:
        return 'doctor_stoppage'
    if 'ko' in m or 'tko' in m:
        return 'ko_tko'
    if 'dq' in m or 'disqualif' in m:
        return 'dq'
    return None


def winner_continuous_s(strike_diff, control_diff, kd_diff, finish_round,
                        method, method_cat, anchors):
    """
    Continuous-S score for the winner of one bout.

    Inputs are winner-minus-loser differences plus the bout's method and
    finish round. Returns a score in [winner_min, winner_max], or None when
    the method cannot be mapped (the caller then uses the binary fallback).
    """
    mb = anchors['method_baselines']
    weights = anchors['dominance']['weights']
    sat = anchors['dominance']['saturation']
    max_swing = anchors['dominance']['max_swing']
    wmin = anchors['clipping']['winner_min']
    wmax = anchors['clipping']['winner_max']

    key = method_to_baseline_key(method)
    if key is None or key not in mb:
        return None

    # Each signal scaled to its saturation point, capped to [0, 1].
    sd = min(1.0, max(0.0, strike_diff / sat['strike_diff']))
    cd = min(1.0, max(0.0, control_diff / sat['control_diff']))
    kd = min(1.0, max(0.0, kd_diff / sat['knockdowns']))

    # Finish-round share: earlier finishes score higher; decisions score 0.
    if pd.isna(finish_round) or method_cat == 'decision':
        fr = 0.0
    elif finish_round == 1:
        fr = sat['finish_round_r1']
    elif finish_round == 2:
        fr = sat['finish_round_r2']
    elif finish_round == 3:
        fr = sat['finish_round_r3']
    else:
        fr = sat['finish_round_r4_5']

    weighted = (weights['strike_diff'] * sd + weights['control_diff'] * cd +
                weights['knockdowns'] * kd + weights['finish_round'] * fr)
    score = mb[key] + weighted * max_swing
    return min(wmax, max(wmin, score))


def score_ledger(ledger, anchors):
    """
    Return a copy of the ledger with score_1 and score_2 columns added.

    fighter_1 is the winner on decided bouts, so the winner's continuous-S
    score maps to score_1 and (1 - score) to score_2. Draws, non-coverage
    bouts, and any bout whose method cannot be mapped use the binary result.
    """
    def one(r):
        # Draws never take the dominance score (no dominant winner).
        if r['outcome'] == 'DRAW':
            return pd.Series({'score_1': 0.5, 'score_2': 0.5})
        if r['stats_coverage']:
            ws = winner_continuous_s(
                r['sig_landed_1'] - r['sig_landed_2'],
                r['ctrl_1'] - r['ctrl_2'],
                r['kd_1'] - r['kd_2'],
                r['finish_round'], r['method'], r['method_cat'], anchors)
            if ws is not None:
                return pd.Series({'score_1': ws, 'score_2': 1.0 - ws})
        # Binary fallback: fighter_1 won.
        return pd.Series({'score_1': 1.0, 'score_2': 0.0})

    out = ledger.copy()
    out[['score_1', 'score_2']] = out.apply(one, axis=1)
    return out
