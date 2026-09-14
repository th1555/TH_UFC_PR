"""
ingest.py — pull new UFC results and append them to the bout ledger.

Written as importable, side-effect-light functions so a dashboard "rerun"
button can call them and show structured status, rather than a CLI script.
Network and parsing failures are caught and returned as status, never
raised into the caller's face.

Sources (Greco1899 mirror of ufcstats.com, refreshed daily):
    ufc_fight_results.csv  - one row per bout: outcome, method, round, url
    ufc_fight_stats.csv    - per-round, per-fighter stats (strikes, control...)
    ufc_event_details.csv  - event -> date
"""
from datetime import date
import re
import unicodedata
import pandas as pd
import numpy as np

BASE = "https://raw.githubusercontent.com/Greco1899/scrape_ufc_stats/main/"


def _strip(df, cols):
    """Trailing/leading whitespace on key columns breaks joins; strip it."""
    df = df.copy()
    for c in cols:
        if c in df.columns:
            df[c] = df[c].astype(str).str.strip()
    return df


# ---- small parsers -------------------------------------------------------
def _landed_attempted(s):
    """'14 of 31' -> (14, 31); missing -> (nan, nan)."""
    if pd.isna(s):
        return (np.nan, np.nan)
    m = re.match(r'\s*(\d+)\s+of\s+(\d+)\s*$', str(s))
    return (int(m.group(1)), int(m.group(2))) if m else (np.nan, np.nan)


def _ctrl_seconds(s):
    """'1:23' -> 83 seconds; '--' or missing -> 0."""
    if pd.isna(s) or str(s).strip() in ('--', ''):
        return 0
    m = re.match(r'\s*(\d+):(\d+)\s*$', str(s))
    return int(m.group(1)) * 60 + int(m.group(2)) if m else 0


def _method_cat(method):
    m = str(method).lower()
    if 'decision' in m:
        return 'decision'
    if 'dq' in m or 'disqualif' in m:
        return 'dq'
    return 'finish'


_REAL_DIVISIONS = [
    "Women's Strawweight", "Women's Flyweight", "Women's Bantamweight",
    "Women's Featherweight",
    "Light Heavyweight", "Heavyweight", "Middleweight", "Welterweight",
    "Lightweight", "Featherweight", "Bantamweight", "Flyweight", "Strawweight",
]


def canonicalise_weightclass(wc):
    """Raw 'Middleweight Bout' -> 'Middleweight'. Ported verbatim from notebook 07."""
    if pd.isna(wc):
        return None
    text = str(wc)
    if re.search(r'\b(Tournament|Ultimate Fighter|Ultimate Japan|Road to)\b', text, re.IGNORECASE):
        for div in _REAL_DIVISIONS:
            if div.lower() in text.lower():
                return div
        return None
    cleaned = re.sub(r'\b(UFC|Interim|Title|Bout)\b', '', text, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned if cleaned else None



def normalise_name(name):
    """For collision detection only: strip accents, case, and punctuation.
    Generational suffixes (Jr/Sr/II/III) are PRESERVED, since they distinguish
    genuinely different fighters (e.g. Lance Gibson vs Lance Gibson Jr., who are
    father and son). Never used to rewrite stored names, only to spot duplicates.
    """
    s = unicodedata.normalize('NFKD', str(name)).encode('ascii', 'ignore').decode()
    s = s.lower()
    s = re.sub(r'[^a-z0-9 ]', ' ', s)          # punctuation -> space; suffixes survive
    return re.sub(r'\s+', ' ', s).strip()


def find_collisions(names):
    """Return {normalised: [raw spellings]} for any normalised name carried by
    more than one raw spelling. Each is a candidate mis-split (one fighter under
    two spellings) for human review, not an automatic merge."""
    groups = {}
    for n in names:
        if pd.isna(n):
            continue
        groups.setdefault(normalise_name(n), []).append(n)
    return {k: sorted(set(v)) for k, v in groups.items() if len(set(v)) > 1}


def apply_aliases(ledger, aliases):
    """Rewrite fighter names through an alias map {variant: canonical}, so a
    confirmed duplicate merges retroactively across all of a fighter's bouts.
    Applied to the live ledger before rebuild/ranking; empty map is a no-op."""
    if not aliases:
        return ledger
    out = ledger.copy()
    out['fighter_1'] = out['fighter_1'].replace(aliases)
    out['fighter_2'] = out['fighter_2'].replace(aliases)
    return out


def _bout_id_from_url(url):
    """The ufcstats fight-details hash is the FIGHT_ID / bout_id."""
    return str(url).rstrip('/').split('/')[-1]


# ---- fetch ---------------------------------------------------------------
def fetch_raw(base=BASE):
    """
    Pull the three source CSVs. Returns (data_dict, error). On any network
    failure, data_dict is None and error is a short message.
    """
    try:
        results = pd.read_csv(base + 'ufc_fight_results.csv')
        stats = pd.read_csv(base + 'ufc_fight_stats.csv')
        events = pd.read_csv(base + 'ufc_event_details.csv')
        return {'results': results, 'stats': stats, 'events': events}, None
    except Exception as e:
        return None, f"feed unreachable: {type(e).__name__}: {e}"


# ---- per-fighter stat aggregation ----------------------------------------
def _aggregate_stats(stats):
    """Sum per-round rows into one total row per (EVENT, BOUT, FIGHTER)."""
    s = _strip(stats, ['EVENT', 'BOUT', 'FIGHTER'])
    sl = s['SIG.STR.'].apply(_landed_attempted)
    s['sig_landed'] = [x[0] for x in sl]
    s['sig_att'] = [x[1] for x in sl]
    td = s['TD'].apply(_landed_attempted)
    s['td_landed'] = [x[0] for x in td]
    s['ctrl'] = s['CTRL'].apply(_ctrl_seconds)
    s['kd'] = pd.to_numeric(s['KD'], errors='coerce')
    s['subatt'] = pd.to_numeric(s['SUB.ATT'], errors='coerce')
    agg = (s.groupby(['EVENT', 'BOUT', 'FIGHTER'], as_index=False)
             [['sig_landed', 'sig_att', 'td_landed', 'kd', 'subatt', 'ctrl']].sum())
    return agg


# ---- build ledger rows for a set of results rows -------------------------
def build_rows(results, stats, events, snapshot):
    """
    Turn raw results (+ stats) into ledger rows. Pure transform. Bouts whose
    stats are absent are emitted with stats_coverage=False (binary at replay).
    """
    events = _strip(events, ['EVENT'])
    results = _strip(results, ['EVENT', 'BOUT'])
    ev_date = dict(zip(events['EVENT'],
                       pd.to_datetime(events['DATE'], format='mixed', errors='coerce')))
    agg = _aggregate_stats(stats)
    # lookup: (EVENT, BOUT, FIGHTER) -> stat totals
    stat_key = {(r.EVENT, r.BOUT, r.FIGHTER): r for r in agg.itertuples(index=False)}

    rows = []
    for r in results.itertuples(index=False):
        bout = str(r.BOUT)
        if ' vs. ' not in bout:
            continue
        a, b = [x.strip() for x in bout.split(' vs. ', 1)]
        outcome_raw = str(r.OUTCOME)
        if outcome_raw.startswith('W'):        # 'W/L' -> first named won
            f1, f2, outcome = a, b, 'F1_WIN'
        elif outcome_raw.startswith('L'):      # 'L/W' -> second named won
            f1, f2, outcome = b, a, 'F1_WIN'
        elif outcome_raw.startswith('D'):      # draw
            f1, f2, outcome = a, b, 'DRAW'
        elif outcome_raw.startswith('N'):      # no contest (still an appearance)
            f1, f2, outcome = a, b, 'NC'
        else:
            f1, f2, outcome = a, b, 'OTHER'

        method = str(r.METHOD).strip()
        mcat = _method_cat(method)
        modelable = outcome in ('F1_WIN', 'DRAW') and mcat in ('decision', 'finish', 'dq')

        s1 = stat_key.get((r.EVENT, bout, f1))
        s2 = stat_key.get((r.EVENT, bout, f2))
        coverage = s1 is not None and s2 is not None

        def g(s, field):
            return getattr(s, field) if s is not None else np.nan

        rows.append({
            'bout_id': _bout_id_from_url(r.URL),
            'event_date': ev_date.get(r.EVENT, pd.NaT),
            'fighter_1': f1, 'fighter_2': f2, 'outcome': outcome,
            'method': method, 'method_cat': mcat,
            'weightclass': canonicalise_weightclass(getattr(r, 'WEIGHTCLASS', None)),
            'finish_round': float(r.ROUND) if pd.notna(r.ROUND) else np.nan,
            'sig_landed_1': g(s1, 'sig_landed'), 'sig_landed_2': g(s2, 'sig_landed'),
            'sig_att_1': g(s1, 'sig_att'), 'sig_att_2': g(s2, 'sig_att'),
            'td_1': g(s1, 'td_landed'), 'td_2': g(s2, 'td_landed'),
            'subatt_1': g(s1, 'subatt'), 'subatt_2': g(s2, 'subatt'),
            'kd_1': g(s1, 'kd'), 'kd_2': g(s2, 'kd'),
            'ctrl_1': g(s1, 'ctrl'), 'ctrl_2': g(s2, 'ctrl'),
            'stats_coverage': bool(coverage), 'modelable': bool(modelable),
            'source': 'greco1899', 'ingest_snapshot': snapshot,
        })
    return pd.DataFrame(rows)


# ---- top-level ingest ----------------------------------------------------
def ingest(ledger, base=BASE, snapshot=None):
    """
    Append any bouts not already in the ledger. Returns (new_ledger, status).
    status carries what a dashboard needs to show: counts, latest dates, and
    a staleness flag (did the feed advance past what we already had?).
    """
    snapshot = snapshot or str(date.today())
    status = {'ok': False, 'error': None, 'new_bouts': 0, 'new_events': 0,
              'feed_latest': None, 'ledger_latest': None, 'stale': None}

    data, err = fetch_raw(base)
    if err:
        status['error'] = err
        return ledger, status

    events = data['events'].copy()
    events['DATE'] = pd.to_datetime(events['DATE'], format='mixed', errors='coerce')
    feed_latest = events['DATE'].max()
    ledger_latest = pd.to_datetime(ledger['event_date']).max()
    status['feed_latest'] = feed_latest
    status['ledger_latest'] = ledger_latest
    # staleness: the feed's newest event should be >= what we already hold
    status['stale'] = bool(feed_latest < ledger_latest)

    new_rows = build_rows(data['results'], data['stats'], events, snapshot)
    have = set(ledger['bout_id'])
    fresh = new_rows[~new_rows['bout_id'].isin(have) & new_rows['event_date'].notna()].copy()
    # Modelable bouts: fold only those newer than what we hold, so we never
    # back-fill old bouts the seed deliberately excluded (keeps the seed's
    # rated set intact). Non-modelable bouts (NCs): fold regardless of date,
    # since they only enrich the activity record and never affect ratings.
    fresh = fresh[(fresh['modelable'] & (fresh['event_date'] >= ledger_latest))
                  | (~fresh['modelable'])]

    if len(fresh):
        new_ledger = pd.concat([ledger, fresh[ledger.columns]], ignore_index=True)
    else:
        new_ledger = ledger

    status['ok'] = True
    status['new_bouts'] = int(len(fresh))
    status['new_events'] = int(fresh['event_date'].nunique()) if len(fresh) else 0
    # name-collision guard: flag any duplicate spellings in the updated roster
    roster = set(new_ledger['fighter_1']) | set(new_ledger['fighter_2'])
    status['name_warnings'] = find_collisions(roster)
    return new_ledger, status
