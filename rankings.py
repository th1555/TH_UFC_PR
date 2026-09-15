"""
rankings.py — turn ratings into ranked lists.

Two ideas do the work:

1. Display-time RD. Inactivity widens a fighter's RD as of the day you look,
   without touching the stored rating. Computed here at read time, never
   written back. The inactivity floor sets how fast a layoff inflates RD;
   this is a display choice and does NOT touch the engine's canonical decay
   used in replay, so the ratings and the regression gate are untouched.

2. Conservative estimate. Rank by rating minus k * RD, the lower edge of what
   we're confident a fighter still is. This demotes the uncertain, whether
   uncertain from inactivity (RD grown by layoff) or from too few fights (RD
   never tightened).

Two dials: k punishes uncertainty in general (mainly unproven fighters); the
inactivity floor punishes idle time specifically (recently-idle greats fall,
active fighters are untouched). A 24-month activity gate decides membership.
The rating itself is never decayed; only our confidence in it is.
"""
import math
import numpy as np
import pandas as pd
import glicko_engine as ge

# Defaults chosen by eye-test: active champions on top, recently-idle greats
# just below, the long-inactive pushed well down. Both are tunable per call.
DEFAULT_K = 1.0
DEFAULT_INACTIVITY_FLOOR = 0.40
DEFAULT_ACTIVE_MONTHS = 24


def _tier(rd):
    if rd < 145:
        return 'Established'
    if rd <= 200:
        return 'Provisional'
    return 'Unreliable'


def _display_rd(rd, sigma, days, floor):
    """As-of-today RD with a tunable inactivity floor (display-only)."""
    if days <= 0:
        return rd
    phi = rd / ge.SCALE
    n_periods = days / 180.0
    sigma_inactive = max(sigma, floor)
    phi_decayed = min(math.sqrt(phi ** 2 + n_periods * sigma_inactive ** 2), ge.PHI)
    return phi_decayed * ge.SCALE


def with_display_state(current, ref_date, floor=DEFAULT_INACTIVITY_FLOOR):
    """Add as-of-ref_date RD (RD_NOW), months idle, and current tier."""
    df = current.copy()
    df['LAST_FIGHT'] = pd.to_datetime(df['LAST_FIGHT'])
    days = (ref_date - df['LAST_FIGHT']).dt.days.clip(lower=0)
    df['RD_NOW'] = [_display_rd(rd, sg, d, floor)
                    for rd, sg, d in zip(df['RD'], df['SIGMA'], days)]
    df['MONTHS_IDLE'] = (days / 30.44).round(1)
    # Confidence tier reflects how well the fighter's RECORD establishes the
    # rating (stored RD), not the inactivity-inflated display RD; recency is
    # shown separately as months idle and handled in the ranking already.
    df['TIER_NOW'] = df['RD'].apply(_tier)
    return df


def last_appearance(ledger_subset):
    """Per fighter, the date of their most recent bout of ANY kind (a No Contest
    counts as an appearance even though it produced no rating)."""
    a = pd.concat([
        ledger_subset[['fighter_1', 'event_date']].rename(columns={'fighter_1': 'FIGHTER'}),
        ledger_subset[['fighter_2', 'event_date']].rename(columns={'fighter_2': 'FIGHTER'}),
    ], ignore_index=True)
    return a.groupby('FIGHTER')['event_date'].max()


def _form_string(scores):
    return ''.join('W' if s > 0.5 else ('D' if s == 0.5 else 'L') for s in scores)


def _with_surprise(history):
    """Add per-fight SURPRISE: actual result minus what the ratings predicted,
    with the prediction set by the opponent's pre-fight rating and uncertainty.
    Opponent quality is therefore baked into every fight's contribution.
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
    expected = 1 / (1 + np.exp(-g_o * (mu_f - mu_o)))
    h['SURPRISE'] = h['SCORE'] - expected
    return h.sort_values('DATE')


def momentum(history, n=5):
    """Recent form per fighter over their last n rated fights (FORM_SCORE +
    a FORM string). Quality-adjusted and comparable across fighters; positive
    is hot, negative cold. Used as a column beside the rankings."""
    h = _with_surprise(history)
    rows = []
    for name, g in h.groupby('FIGHTER'):
        g = g.tail(n)
        rows.append({'FIGHTER': name,
                     'FORM_SCORE': round(g['SURPRISE'].mean() * 100),
                     'FORM': _form_string(g['SCORE']),
                     'N_RECENT': len(g)})
    return pd.DataFrame(rows)


def hot_list(history, ledger, ref_date=None, window_months=24, min_fights=3,
             division=None, sex=None, top=None):
    """Momentum board: who is hottest over a fixed recent window.

    Form is the average per-fight surprise (x100) over fights in the last
    window_months; a fighter must have at least min_fights in that window to
    qualify, which keeps two-fight small samples off the list. Filter to one
    division (by weight class) or one sex. Sorted hottest first.
    """
    ref_date = pd.Timestamp(ref_date) if ref_date is not None else pd.Timestamp.today().normalize()
    h = _with_surprise(history)
    wc = ledger.drop_duplicates('bout_id').set_index('bout_id')['weightclass']
    h['WC'] = h['FIGHT_ID'].map(wc)
    h = h[h['DATE'] >= ref_date - pd.DateOffset(months=window_months)]
    if division:
        h = h[h['WC'] == division]
    rows = []
    for name, g in h.groupby('FIGHTER'):
        if len(g) < min_fights:
            continue
        rows.append({'FIGHTER': name,
                     'FORM_SCORE': round(g['SURPRISE'].mean() * 100),
                     'FORM': _form_string(g['SCORE']),
                     'N_RECENT': len(g),
                     'LAST_SEEN': g['DATE'].max()})
    board = pd.DataFrame(rows)
    if sex and len(board):
        smap = fighter_sex(ledger)
        board = board[board['FIGHTER'].map(smap) == sex]
    board = board.sort_values('FORM_SCORE', ascending=False).reset_index(drop=True)
    board.insert(0, 'RANK', board.index + 1)
    return board.head(top) if top else board


def rank(current, ledger_subset, ref_date=None, k=DEFAULT_K,
         inactivity_floor=DEFAULT_INACTIVITY_FLOOR,
         active_months=DEFAULT_ACTIVE_MONTHS, apply_inactivity=True, top=None):
    """
    Rank fighters by the conservative estimate.

    Two clocks: RD grows from the last RESULT (LAST_FIGHT), so confidence
    reflects real information; the activity gate uses the last APPEARANCE from
    ledger_subset, so a fighter who competed recently (even to a No Contest)
    stays on the list. ledger_subset is the bouts relevant to this list (all
    bouts for P4P; one division's bouts for a division board).
    """
    ref_date = pd.Timestamp(ref_date) if ref_date is not None else pd.Timestamp.today().normalize()

    if apply_inactivity:
        # "Current form": RD widens with the layoff, and we gate on activity.
        df = with_display_state(current, ref_date, floor=inactivity_floor)
    else:
        # "All-time": use the fighter's actual career-end RD; no inactivity
        # inflation, no activity gate. Ranks careers, not current form.
        df = current.copy()
        df['RD_NOW'] = df['RD']
        df['TIER_NOW'] = df['RD'].apply(_tier)
        df['MONTHS_IDLE'] = ((ref_date - pd.to_datetime(df['LAST_FIGHT'])).dt.days / 30.44).round(1)

    df['CR'] = df['RATING'] - k * df['RD_NOW']                 # conservative estimate

    if active_months is not None:                             # activity gate (current only)
        seen = last_appearance(ledger_subset)
        df['LAST_SEEN'] = pd.to_datetime(df['FIGHTER'].map(seen))
        df['MONTHS_SINCE_SEEN'] = ((ref_date - df['LAST_SEEN']).dt.days / 30.44).round(1)
        df = df[df['MONTHS_SINCE_SEEN'] <= active_months].copy()

    df = df.sort_values('CR', ascending=False).reset_index(drop=True)
    df.insert(0, 'RANK', df.index + 1)
    return df.head(top) if top else df


# ---------------------------------------------------------------------------
# Per-division boards
#
# A division rating is built from results AT that weight only: filter the
# ledger to the division's bouts and replay. A fighter earns an independent
# rating in every division they've fought in, so multi-division fighters
# (Makhachev at LW and WW, Jones at LHW and HW) appear on more than one board,
# each rating earned against that division's opponents. The activity gate uses
# the fighter's last bout IN THAT DIVISION, so a fighter who has moved on drops
# off their old division's board.
# ---------------------------------------------------------------------------
import runner  # noqa: E402  (safe: runner does not import rankings)

REAL_DIVISIONS = [
    "Heavyweight", "Light Heavyweight", "Middleweight", "Welterweight",
    "Lightweight", "Featherweight", "Bantamweight", "Flyweight",
    "Women's Bantamweight", "Women's Flyweight", "Women's Strawweight",
]


def division_current(ledger, anchors, division):
    """Division-specific ratings: replay only the bouts fought at this weight."""
    sub = ledger[ledger['weightclass'] == division]
    if not len(sub):
        return None
    _, current = runner.rebuild(sub, anchors)
    return current


def division_board(ledger, anchors, division, ref_date=None, all_time=False, top=None, **kwargs):
    """Ranked board for one division. all_time=True drops the activity gate and
    the inactivity RD inflation, ranking careers rather than current form."""
    sub = ledger[ledger['weightclass'] == division]
    current = division_current(ledger, anchors, division)
    if current is None or not len(current):
        return None
    if all_time:
        return rank(current, sub, apply_inactivity=False, active_months=None, top=top, **kwargs)
    return rank(current, sub, ref_date=ref_date, top=top, **kwargs)

def fighter_sex(ledger):
    """Map each fighter to 'F' or 'M'. Women's divisions start with "Women's",
    and the sexes never meet, so any Women's-division bout marks a fighter female."""
    long = pd.concat([
        ledger[['fighter_1', 'weightclass']].rename(columns={'fighter_1': 'FIGHTER'}),
        ledger[['fighter_2', 'weightclass']].rename(columns={'fighter_2': 'FIGHTER'}),
    ], ignore_index=True)
    is_female = (long.assign(w=long['weightclass'].astype(str).str.startswith("Women's"))
                     .groupby('FIGHTER')['w'].any())
    return is_female.map({True: 'F', False: 'M'})


def pound_for_pound(current, ledger, sex=None, ref_date=None, top=None, **kwargs):
    """P4P list, optionally filtered to one sex ('M' or 'F')."""
    df = rank(current, ledger, ref_date=ref_date, **kwargs)
    if sex:
        smap = fighter_sex(ledger)
        df = df[df['FIGHTER'].map(smap) == sex].reset_index(drop=True)
        df['RANK'] = df.index + 1
    return df.head(top) if top else df



def all_boards(ledger, anchors, ref_date=None, divisions=None, top=None, **kwargs):
    """Every division board as {division: ranked_df}."""
    divisions = divisions or REAL_DIVISIONS
    boards = {}
    for d in divisions:
        b = division_board(ledger, anchors, d, ref_date=ref_date, top=top, **kwargs)
        if b is not None and len(b):
            boards[d] = b
    return boards
