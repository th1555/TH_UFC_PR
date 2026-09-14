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
import pandas as pd
import glicko_engine as ge

# Defaults chosen by eye-test: active champions on top, recently-idle greats
# just below, the long-inactive pushed well down. Both are tunable per call.
DEFAULT_K = 1.0
DEFAULT_INACTIVITY_FLOOR = 0.40
DEFAULT_ACTIVE_MONTHS = 24


def _tier(rd):
    if rd < 125:
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
    df['TIER_NOW'] = df['RD_NOW'].apply(_tier)
    return df


def last_appearance(ledger_subset):
    """Per fighter, the date of their most recent bout of ANY kind (a No Contest
    counts as an appearance even though it produced no rating)."""
    a = pd.concat([
        ledger_subset[['fighter_1', 'event_date']].rename(columns={'fighter_1': 'FIGHTER'}),
        ledger_subset[['fighter_2', 'event_date']].rename(columns={'fighter_2': 'FIGHTER'}),
    ], ignore_index=True)
    return a.groupby('FIGHTER')['event_date'].max()


def rank(current, ledger_subset, ref_date=None, k=DEFAULT_K,
         inactivity_floor=DEFAULT_INACTIVITY_FLOOR,
         active_months=DEFAULT_ACTIVE_MONTHS, top=None):
    """
    Rank fighters by the conservative estimate.

    Two clocks: RD grows from the last RESULT (LAST_FIGHT), so confidence
    reflects real information; the activity gate uses the last APPEARANCE from
    ledger_subset, so a fighter who competed recently (even to a No Contest)
    stays on the list. ledger_subset is the bouts relevant to this list (all
    bouts for P4P; one division's bouts for a division board).
    """
    ref_date = pd.Timestamp(ref_date) if ref_date is not None else pd.Timestamp.today().normalize()
    df = with_display_state(current, ref_date, floor=inactivity_floor)   # RD_NOW off last result
    df['CR'] = df['RATING'] - k * df['RD_NOW']                           # conservative estimate

    seen = last_appearance(ledger_subset)
    df['LAST_SEEN'] = pd.to_datetime(df['FIGHTER'].map(seen))
    df['MONTHS_SINCE_SEEN'] = ((ref_date - df['LAST_SEEN']).dt.days / 30.44).round(1)

    df = df[df['MONTHS_SINCE_SEEN'] <= active_months].copy()   # gate on APPEARANCE
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


def division_board(ledger, anchors, division, ref_date=None, top=None, **kwargs):
    """Ranked board for one division, using the same dial as the P4P list."""
    sub = ledger[ledger['weightclass'] == division]
    current = division_current(ledger, anchors, division)
    if current is None or not len(current):
        return None
    return rank(current, sub, ref_date=ref_date, top=top, **kwargs)


def all_boards(ledger, anchors, ref_date=None, divisions=None, top=None, **kwargs):
    """Every division board as {division: ranked_df}."""
    divisions = divisions or REAL_DIVISIONS
    boards = {}
    for d in divisions:
        b = division_board(ledger, anchors, d, ref_date=ref_date, top=top, **kwargs)
        if b is not None and len(b):
            boards[d] = b
    return boards
