"""
streamlit_app.py — TH_UFC_PR exploration dashboard.

A read-and-recompute viewer over the ranking functions. Three modes:
  - Power ranking: the ordered board (P4P male/female, or a division), with a
    Dom column surfacing how emphatically each fighter wins.
  - Momentum: who is hottest over a fixed recent window.
  - Most Dominant: the ranked contenders re-sorted by dominance (how emphatic
    their wins are), so "most dominant of the genuine contenders" rather than a
    raw finish count anyone can top.
It loads the committed ledger and recomputes; it never writes or commits. A
"fetch latest" button previews new events in memory without saving them.

Run locally:  streamlit run streamlit_app.py
Hosted:       Streamlit Community Cloud, main file streamlit_app.py.
"""
from pathlib import Path
import pandas as pd
import yaml
import streamlit as st

import ingest
import runner
import rankings
from scoring import load_anchors

ROOT = Path(__file__).resolve().parent
LEDGER = ROOT / "data" / "bout_ledger.parquet"
ANCHORS = ROOT / "anchors" / "performance.yaml"
ALIASES = ROOT / "name_aliases.yaml"

st.set_page_config(page_title="UFC Dominance Rankings", layout="wide")


# ---------------------------------------------------------------------------
# Data: load committed ledger, apply aliases, rebuild once. Cached on the
# ledger file's fingerprint. Returned as a dict to avoid tuple-arity slips.
# ---------------------------------------------------------------------------
def _ledger_fingerprint():
    s = LEDGER.stat()
    return (s.st_mtime, s.st_size)


@st.cache_data(show_spinner="Rebuilding ratings from the ledger...")
def _rebuild_from_committed(_fingerprint):
    ledger = pd.read_parquet(LEDGER)
    aliases = yaml.safe_load(ALIASES.read_text()) if ALIASES.exists() else {}
    ledger = ingest.apply_aliases(ledger, aliases or {})
    return _rebuild_all(ledger)


def _rebuild_all(ledger):
    anchors = load_anchors(ANCHORS)
    history, pooled = runner.rebuild(ledger, anchors)
    return {
        "ledger": ledger,
        "pooled": pooled,
        "div_currents": {d: rankings.division_current(ledger, anchors, d)
                         for d in rankings.REAL_DIVISIONS},
        "mom": rankings.momentum(history),
        "dom": rankings.dominance(history, ledger),
        "history": history,
    }


def get_data():
    if "preview" in st.session_state:
        return st.session_state["preview"]["data"]
    return _rebuild_from_committed(_ledger_fingerprint())


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
st.sidebar.header("View")
mode = st.sidebar.radio("Mode", ["Power ranking", "Momentum (who's hot)", "Most Dominant"])
view = st.sidebar.selectbox(
    "Ranking",
    ["Pound-for-pound (Male)", "Pound-for-pound (Female)"] + rankings.REAL_DIVISIONS,
)
top_n = st.sidebar.slider("Show top", 5, 50, 20, 5)

if mode == "Momentum (who's hot)":
    st.sidebar.header("Momentum window")
    st.sidebar.caption("Who's built the most form over a fixed recent window.")
    window = st.sidebar.slider("Window (months)", 6, 36, 24, 3)
    min_fights = st.sidebar.slider("Minimum fights in window", 2, 6, 3, 1)
else:
    st.sidebar.header("Dials")
    st.sidebar.caption("Eligibility and how the ordering responds to uncertainty.")
    k = st.sidebar.slider("Uncertainty penalty (k)", 0.0, 3.0, rankings.DEFAULT_K, 0.1)
    floor = st.sidebar.slider("Inactivity floor", 0.18, 0.80,
                              rankings.DEFAULT_INACTIVITY_FLOOR, 0.01)
    gate = st.sidebar.slider("Activity gate (months)", 6, 36,
                             rankings.DEFAULT_ACTIVE_MONTHS, 1)

st.sidebar.header("Live feed")
if st.sidebar.button("Fetch latest events (preview, not saved)"):
    committed_ledger = pd.read_parquet(LEDGER)
    with st.spinner("Fetching the feed and folding new events..."):
        new_ledger, status = ingest.ingest(committed_ledger)
        aliases = yaml.safe_load(ALIASES.read_text()) if ALIASES.exists() else {}
        new_ledger = ingest.apply_aliases(new_ledger, aliases or {})
        st.session_state["preview"] = {"data": _rebuild_all(new_ledger), "status": status}
if "preview" in st.session_state and st.sidebar.button("Clear preview (back to committed)"):
    del st.session_state["preview"]


# ---------------------------------------------------------------------------
# Build the selected board
# ---------------------------------------------------------------------------
d = get_data()
ledger, pooled, div_currents, mom, dom = d["ledger"], d["pooled"], d["div_currents"], d["mom"], d["dom"]
ref = pd.Timestamp.today().normalize()
is_p4p = view.startswith("Pound-for-pound")
sex = ("M" if "Male" in view else "F") if is_p4p else None


def eligible_board(top=None):
    """The power-ranking board for the current view (used by Power + Most Dominant)."""
    dial = dict(ref_date=ref, k=k, inactivity_floor=floor, active_months=gate, top=top)
    if is_p4p:
        return rankings.pound_for_pound(pooled, ledger, sex=sex, **dial)
    return rankings.rank(div_currents[view], ledger[ledger["weightclass"] == view], **dial)


if mode == "Momentum (who's hot)":
    board = rankings.hot_list(d["history"], ledger, ref_date=ref, window_months=window,
                              min_fights=min_fights, division=None if is_p4p else view,
                              sex=sex, top=top_n)
    cols = {"RANK": "#", "FIGHTER": "Fighter", "FORM_SCORE": "Form",
            "FORM": f"Record ({window}mo)", "N_RECENT": "Fights"}
    fmt = {"Form": "{:+.0f}"}

elif mode == "Most Dominant":
    # restrict to the genuine contenders (top by rating) BEFORE sorting on
    # dominance, so an obscure fighter on a finish streak can't top the list
    pool = max(top_n, 40)
    b = eligible_board(top=pool).merge(dom, on="FIGHTER", how="inner")
    b = b.sort_values("DOM_SCORE", ascending=False).reset_index(drop=True)
    b["DOM_RANK"] = b.index + 1
    board = b.merge(mom[["FIGHTER", "FORM"]], on="FIGHTER", how="left").head(top_n)
    cols = {"DOM_RANK": "#", "FIGHTER": "Fighter", "DOM_SCORE": "Dominance",
            "FINISH_PCT": "Finish %", "RANK": "Ranked", "FORM": "Last 5"}
    fmt = {"Dominance": "{:.0f}", "Finish %": "{:.0f}"}

else:  # Power ranking
    board = eligible_board(top=top_n)
    board = board.merge(mom[["FIGHTER", "FORM_SCORE", "FORM"]], on="FIGHTER", how="left")
    board = board.merge(dom[["FIGHTER", "DOM_SCORE"]], on="FIGHTER", how="left")
    cols = {
        "RANK": "#", "FIGHTER": "Fighter", "RATING": "Rating", "RD": "RD",
        "CR": "Conservative", "DOM_SCORE": "Dom", "FORM_SCORE": "Form", "FORM": "Last 5",
        "MONTHS_SINCE_SEEN": "Months idle", "TIER_NOW": "Confidence", "N_FIGHTS": "UFC fights",
    }
    fmt = {"Rating": "{:.0f}", "RD": "{:.0f}", "Conservative": "{:.0f}",
           "Dom": "{:.0f}", "Form": "{:+.0f}", "Months idle": "{:.1f}"}


# ---------------------------------------------------------------------------
# Header + status
# ---------------------------------------------------------------------------
as_of = pd.to_datetime(ledger["event_date"]).max().date()
n_fighters = len(set(ledger["fighter_1"]) | set(ledger["fighter_2"]))
st.title("UFC Dominance Rankings")
tags = {"Power ranking": "", "Momentum (who's hot)": "  ·  momentum",
        "Most Dominant": "  ·  most dominant"}
st.caption(f"{view}{tags[mode]}  ·  data through {as_of}  ·  {n_fighters:,} fighters  ·  "
           f"opponent-adjusted Glicko-2, weighted by how dominant each win was")

if "preview" in st.session_state:
    s = st.session_state["preview"]["status"]
    st.info(f"Previewing the live feed: {s['new_bouts']} new bouts across "
            f"{s['new_events']} events folded in memory (not saved to the repo). "
            f"Feed latest {pd.to_datetime(s['feed_latest']).date()}.")
    if s.get("name_warnings"):
        st.warning(f"Name collisions to review: {list(s['name_warnings'].values())}")


# ---------------------------------------------------------------------------
# The board
# ---------------------------------------------------------------------------
show = board[[c for c in cols if c in board.columns]].rename(columns=cols)
st.dataframe(show.style.format(fmt), hide_index=True, width='stretch',
             height=min(38 * (top_n + 1), 900))

with st.expander("What the columns mean"):
    if mode == "Momentum (who's hot)":
        st.markdown(
            "- **Form**: average per-fight surprise (how much they beat what the "
            "ratings predicted) over the window, scaled. Opponent quality is built "
            "in: beating a stronger opponent counts more. Positive is hot.\n"
            f"- **Record ({window}mo)** / **Fights**: win-loss string and count of "
            "fights inside the window; a fighter needs the minimum to qualify."
        )
    elif mode == "Most Dominant":
        st.markdown(
            "- **Dominance**: how emphatically the fighter wins, the average "
            "continuous-S dominance score over their recent wins (~85 = dominant "
            "finishes, ~60 = grinding decisions). This is the 'how'.\n"
            "- **Finish %**: share of those wins that ended inside the distance.\n"
            "- **Ranked**: the fighter's position on the power ranking (the 'who'). "
            "The gap between the two is the story: a violent finisher can out-dominate "
            "a higher-ranked fighter who wins by control."
        )
    else:
        st.markdown(
            "- **Rating**: opponent-adjusted Glicko-2 strength, weighted by how "
            "dominant each win was. Never decays for inactivity.\n"
            "- **RD**: how settled the rating is, from the fighter's record; lower "
            "means more fights. Recency is shown separately as Months idle.\n"
            "- **Conservative**: the rating discounted for uncertainty, including any "
            "recent layoff. The list sorts by this.\n"
            "- **Dom**: how emphatically they win (average dominance of recent wins).\n"
            "- **Form**: recent form over the last 5 fights, quality-adjusted. **Last 5** "
            "is the win-loss string.\n"
            "- **Months idle**: time since the last appearance (a No Contest counts).\n"
            "- **Confidence**: how well the record establishes the rating."
        )

# --- seam for week-over-week movement (deferred) ---------------------------
# Save the current board dated; a future version joins last week's on FIGHTER
# to add a rank-change column.
